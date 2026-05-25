"""Flask web app for the Midas gold trading dashboard."""

from __future__ import annotations

import logging
from datetime import date

from flask import Flask, render_template, request

logging.basicConfig(level=logging.INFO)

app = Flask(__name__, template_folder="templates")

# Maps the user-facing toggle value to parameters for each data source.
_RANGE_PRESETS = {
    "30d": {
        "yahoo": "1mo",
        "fred_days": 45,
        "cpi_months": 6,
        "etf_rows": 10,
        "label": "30-day",
    },
    "12m": {
        "yahoo": "1y",
        "fred_days": 395,
        "cpi_months": 13,
        "etf_rows": 20,
        "label": "12-month",
    },
}


def _fetch_gold_price() -> dict | None:
    try:
        from midas.clients.metal_price import MetalPriceClient

        client = MetalPriceClient()
        price = client.latest()
        return {
            "price": f"{price.price:,.2f}",
            "currency": price.currency,
            "timestamp": price.timestamp.strftime("%Y-%m-%d %H:%M"),
        }
    except Exception as exc:
        return {"error": str(exc)}


def _fetch_cot_positions() -> dict | None:
    try:
        from midas.clients.cftc import CFTCClient

        client = CFTCClient()
        pos = client.latest_position()
        return {
            "report_date": pos.report_date.isoformat(),
            "mm_long": f"{pos.mm_long:,}",
            "mm_short": f"{pos.mm_short:,}",
            "mm_net": f"{pos.mm_net:,}",
            "prod_long": f"{pos.prod_long:,}",
            "prod_short": f"{pos.prod_short:,}",
            "swap_long": f"{pos.swap_long:,}",
            "swap_short": f"{pos.swap_short:,}",
            "other_long": f"{pos.other_long:,}",
            "other_short": f"{pos.other_short:,}",
            "open_interest": f"{pos.open_interest:,}",
        }
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return {"error": str(exc)}


def _fetch_etf_scorecard() -> dict | None:
    try:
        from midas.clients.etf_scorecard import GoldETFScorecard

        data = GoldETFScorecard().compute()
        score = data["total_score"]
        labels = {
            3: "Strong Bullish", 2: "Bullish", 1: "Slightly Bullish",
            0: "Neutral",
            -1: "Slightly Bearish", -2: "Bearish", -3: "Strong Bearish",
        }

        def _fmt_vol(v):
            if v is None:
                return "—"
            if v >= 1_000_000:
                return f"{v / 1_000_000:,.1f}M"
            if v >= 1_000:
                return f"{v / 1_000:,.0f}K"
            return f"{v:,}"

        tonnes = data["tonnes"]
        volume = data["volume"]
        price = data["price"]

        return {
            "total_score": score,
            "score_label": labels.get(score, "Neutral"),
            "positive": score > 0,
            "negative": score < 0,
            "tonnes": {
                "score": tonnes["score"],
                "label": tonnes.get("label", "N/A"),
                "current_5d_avg": (
                    f"{tonnes['current_5d_avg']:,.2f}"
                    if "current_5d_avg" in tonnes else "—"
                ),
                "prev_5d_avg": (
                    f"{tonnes['prev_5d_avg']:,.2f}"
                    if "prev_5d_avg" in tonnes else "—"
                ),
                "latest_tonnes": (
                    f"{tonnes['latest_tonnes']:,.2f}"
                    if "latest_tonnes" in tonnes else "—"
                ),
                "latest_date": tonnes.get("latest_date", ""),
            },
            "volume": {
                "score": volume["score"],
                "label": volume.get("label", "N/A"),
                "latest_volume": _fmt_vol(volume.get("latest_volume")),
                "ma_20d": _fmt_vol(volume.get("ma_20d")),
                "latest_date": volume.get("latest_date", ""),
                "ticker_count": volume.get("ticker_count", 0),
            },
            "price": {
                "score": price["score"],
                "label": price.get("label", "N/A"),
                "latest_close": (
                    f"${price['latest_close']:,.2f}"
                    if "latest_close" in price else "—"
                ),
                "ma_50d": (
                    f"${price['ma_50d']:,.2f}"
                    if "ma_50d" in price else "—"
                ),
                "latest_date": price.get("latest_date", ""),
            },
            "tickers_loaded": data.get("tickers_loaded", []),
        }
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return {"error": str(exc)}


def _fetch_gold_etf_aggregate() -> dict | None:
    try:
        from midas.clients.major_etfs import MajorETFsClient

        data = MajorETFsClient().get_aggregate()

        def _fmt_num(v, decimals=1):
            if v is None:
                return None
            return f"{v:,.{decimals}f}"

        def _fmt_tonnes(v):
            return _fmt_num(v, 1) if v else None

        def _fmt_bn(v):
            return _fmt_num(v / 1e9, 2) if v else None

        holdings = []
        for h in data.get("holdings", []):
            holdings.append(
                {
                    "ticker": h["ticker"],
                    "name": h["name"],
                    "region": h["region"],
                    "tonnes": _fmt_tonnes(h.get("tonnes")),
                    "aum_bn": _fmt_bn(h.get("market_cap_usd")),
                }
            )
        holdings.sort(
            key=lambda r: float((r["tonnes"] or "0").replace(",", "")),
            reverse=True,
        )

        by_region = [
            {
                "region": region,
                "tonnes": _fmt_tonnes(bucket["tonnes"]),
                "aum_bn": _fmt_bn(bucket["aum_usd"]),
                "fund_count": int(bucket["fund_count"]),
            }
            for region, bucket in data.get("by_region", {}).items()
        ]
        by_region.sort(
            key=lambda r: float((r["tonnes"] or "0").replace(",", "")),
            reverse=True,
        )

        return {
            "total_tonnes": _fmt_tonnes(data.get("total_tonnes")),
            "total_aum_bn": _fmt_bn(data.get("total_aum_usd")),
            "fund_count": data.get("fund_count"),
            "spot_usd_per_oz": _fmt_num(data.get("spot_usd_per_oz"), 2),
            "holdings": holdings,
            "by_region": by_region,
        }
    except Exception as exc:
        return {"error": str(exc)}


def _sparkline(values: list[float], w: int = 280, h: int = 60) -> dict:
    lo, hi = min(values), max(values)
    span = hi - lo if hi != lo else 1
    points = []
    for i, val in enumerate(values):
        x = (i / max(len(values) - 1, 1)) * w
        y = h - ((val - lo) / span) * h
        points.append(f"{x:.1f},{y:.1f}")
    return {"svg": " ".join(points), "w": w, "h": h, "lo": lo, "hi": hi}


def _fetch_dxy(yahoo_range: str) -> dict | None:
    try:
        from midas.clients.dxy import DXYClient

        prices = DXYClient().get_prices(range_=yahoo_range)
        if not prices:
            return {"error": "No DXY data available"}
        latest, first = prices[-1], prices[0]
        change = latest["close"] - first["close"]
        change_pct = (change / first["close"]) * 100 if first["close"] else 0
        sl = _sparkline([p["close"] for p in prices])

        return {
            "latest": f"{latest['close']:.2f}",
            "latest_date": latest["date"].isoformat(),
            "first_date": first["date"].isoformat(),
            "change": f"{change:+.2f}",
            "change_pct": f"{change_pct:+.2f}",
            "positive": change >= 0,
            "sparkline_svg": sl["svg"],
            "svg_w": sl["w"],
            "svg_h": sl["h"],
            "hi": f"{sl['hi']:.2f}",
            "lo": f"{sl['lo']:.2f}",
        }
    except Exception as exc:
        return {"error": str(exc)}


def _fetch_gold_silver_ratio(yahoo_range: str) -> dict | None:
    try:
        from midas.clients.gold_silver import GoldSilverRatioClient

        records = GoldSilverRatioClient().get_ratio(range_=yahoo_range)
        if not records:
            return {"error": "No gold/silver ratio data available"}
        latest, first = records[-1], records[0]
        change = latest["ratio"] - first["ratio"]
        change_pct = (change / first["ratio"]) * 100 if first["ratio"] else 0
        sl = _sparkline([r["ratio"] for r in records])

        return {
            "latest": f"{latest['ratio']:.1f}",
            "gold": f"{latest['gold']:,.2f}",
            "silver": f"{latest['silver']:.2f}",
            "latest_date": latest["date"].isoformat(),
            "first_date": first["date"].isoformat(),
            "change": f"{change:+.1f}",
            "change_pct": f"{change_pct:+.2f}",
            "positive": change >= 0,
            "sparkline_svg": sl["svg"],
            "svg_w": sl["w"],
            "svg_h": sl["h"],
            "hi": f"{sl['hi']:.1f}",
            "lo": f"{sl['lo']:.1f}",
        }
    except Exception as exc:
        return {"error": str(exc)}


def _fetch_real_yield(fred_days: int) -> dict | None:
    try:
        from midas.clients.fred import FREDClient

        records = FREDClient().get_real_yield_10y(days=fred_days)
        if not records:
            return {"error": "No 10Y real yield data available"}
        latest, first = records[-1], records[0]
        change = latest["value"] - first["value"]
        sl = _sparkline([r["value"] for r in records])

        return {
            "latest": f"{latest['value']:.2f}%",
            "latest_date": latest["date"].isoformat(),
            "first_date": first["date"].isoformat(),
            "change": f"{change:+.2f}",
            "positive": change >= 0,
            "sparkline_svg": sl["svg"],
            "svg_w": sl["w"],
            "svg_h": sl["h"],
            "hi": f"{sl['hi']:.2f}%",
            "lo": f"{sl['lo']:.2f}%",
        }
    except Exception as exc:
        return {"error": str(exc)}


def _fetch_cpi(cpi_months: int) -> dict | None:
    try:
        from midas.clients.fred import FREDClient

        records = FREDClient().get_cpi_yoy(months=cpi_months)
        if not records:
            return {"error": "No CPI data available"}
        latest, first = records[-1], records[0]
        change = latest["value"] - first["value"]
        sl = _sparkline([r["value"] for r in records])

        return {
            "latest": f"{latest['value']:.1f}%",
            "latest_date": latest["date"].isoformat(),
            "first_date": first["date"].isoformat(),
            "change": f"{change:+.1f}",
            "positive": change >= 0,
            "sparkline_svg": sl["svg"],
            "svg_w": sl["w"],
            "svg_h": sl["h"],
            "hi": f"{sl['hi']:.1f}%",
            "lo": f"{sl['lo']:.1f}%",
            "months": len(records),
        }
    except Exception as exc:
        return {"error": str(exc)}


def _fetch_vix(yahoo_range: str) -> dict | None:
    try:
        from midas.clients.vix import VIXClient

        prices = VIXClient().get_prices(range_=yahoo_range)
        if not prices:
            return {"error": "No VIX data available"}
        latest, first = prices[-1], prices[0]
        change = latest["close"] - first["close"]
        change_pct = (change / first["close"]) * 100 if first["close"] else 0
        sl = _sparkline([p["close"] for p in prices])

        return {
            "latest": f"{latest['close']:.2f}",
            "latest_date": latest["date"].isoformat(),
            "first_date": first["date"].isoformat(),
            "change": f"{change:+.2f}",
            "change_pct": f"{change_pct:+.2f}",
            "positive": change >= 0,
            "sparkline_svg": sl["svg"],
            "svg_w": sl["w"],
            "svg_h": sl["h"],
            "hi": f"{sl['hi']:.2f}",
            "lo": f"{sl['lo']:.2f}",
        }
    except Exception as exc:
        return {"error": str(exc)}


def _fetch_wgc_commentary() -> dict | None:
    try:
        from midas.clients.wgc import WGCETFClient

        info = WGCETFClient().get_latest_commentary()
        if not info:
            return None
        return {
            "title": info.get("title"),
            "description": info.get("description"),
            "page_url": info.get("page_url"),
            "period": info.get("period"),
        }
    except Exception as exc:
        return {"error": str(exc)}


@app.route("/")
def dashboard():
    period = request.args.get("range", "30d")
    if period not in _RANGE_PRESETS:
        period = "30d"
    preset = _RANGE_PRESETS[period]
    yr = preset["yahoo"]

    gold_price = _fetch_gold_price()
    dxy = _fetch_dxy(yr)
    gsr = _fetch_gold_silver_ratio(yr)
    real_yield = _fetch_real_yield(preset["fred_days"])
    cpi = _fetch_cpi(preset["cpi_months"])
    vix = _fetch_vix(yr)
    cot = _fetch_cot_positions()
    etf = _fetch_etf_scorecard()
    aggregate = _fetch_gold_etf_aggregate()
    wgc = _fetch_wgc_commentary()
    return render_template(
        "dashboard.html",
        gold_price=gold_price,
        dxy=dxy,
        gsr=gsr,
        real_yield=real_yield,
        cpi=cpi,
        vix=vix,
        cot=cot,
        etf=etf,
        aggregate=aggregate,
        wgc=wgc,
        period=period,
        range_label=preset["label"],
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
