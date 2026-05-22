"""Flask web app for the Midas gold trading dashboard."""

from __future__ import annotations

import logging
from datetime import date

from flask import Flask, render_template

logging.basicConfig(level=logging.INFO)

app = Flask(__name__, template_folder="templates")


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


def _fetch_etf_holdings() -> dict | None:
    try:
        from midas.clients.etf import GoldETFClient

        client = GoldETFClient()
        prices = client.get_gld_prices(days=30)
        if not prices:
            return {"error": "No ETF price data available"}
        latest = prices[-1]
        rows = [
            {
                "date": p["date"].isoformat(),
                "close": f"{p['close']:,.2f}",
                "volume": f"{p['volume']:,}" if p.get("volume") else "—",
            }
            for p in prices[-10:]
        ]
        return {
            "ticker": "GLD",
            "latest_date": latest["date"].isoformat(),
            "latest_close": f"{latest['close']:,.2f}",
            "recent": rows,
        }
    except Exception as exc:
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


def _fetch_dxy() -> dict | None:
    try:
        from midas.clients.dxy import DXYClient

        prices = DXYClient().get_prices(days=30)
        if not prices:
            return {"error": "No DXY data available"}
        latest = prices[-1]
        first = prices[0]
        change = latest["close"] - first["close"]
        change_pct = (change / first["close"]) * 100 if first["close"] else 0

        sparkline_closes = [p["close"] for p in prices]
        lo, hi = min(sparkline_closes), max(sparkline_closes)
        span = hi - lo if hi != lo else 1
        svg_w, svg_h = 280, 60
        points = []
        for i, val in enumerate(sparkline_closes):
            x = (i / max(len(sparkline_closes) - 1, 1)) * svg_w
            y = svg_h - ((val - lo) / span) * svg_h
            points.append(f"{x:.1f},{y:.1f}")
        polyline = " ".join(points)

        return {
            "latest": f"{latest['close']:.2f}",
            "latest_date": latest["date"].isoformat(),
            "first_date": first["date"].isoformat(),
            "change": f"{change:+.2f}",
            "change_pct": f"{change_pct:+.2f}",
            "positive": change >= 0,
            "sparkline_svg": polyline,
            "svg_w": svg_w,
            "svg_h": svg_h,
            "hi": f"{hi:.2f}",
            "lo": f"{lo:.2f}",
        }
    except Exception as exc:
        return {"error": str(exc)}


def _fetch_gold_silver_ratio() -> dict | None:
    try:
        from midas.clients.gold_silver import GoldSilverRatioClient

        records = GoldSilverRatioClient().get_ratio(days=30)
        if not records:
            return {"error": "No gold/silver ratio data available"}
        latest = records[-1]
        first = records[0]
        change = latest["ratio"] - first["ratio"]
        change_pct = (change / first["ratio"]) * 100 if first["ratio"] else 0

        ratios = [r["ratio"] for r in records]
        lo, hi = min(ratios), max(ratios)
        span = hi - lo if hi != lo else 1
        svg_w, svg_h = 280, 60
        points = []
        for i, val in enumerate(ratios):
            x = (i / max(len(ratios) - 1, 1)) * svg_w
            y = svg_h - ((val - lo) / span) * svg_h
            points.append(f"{x:.1f},{y:.1f}")
        polyline = " ".join(points)

        return {
            "latest": f"{latest['ratio']:.1f}",
            "gold": f"{latest['gold']:,.2f}",
            "silver": f"{latest['silver']:.2f}",
            "latest_date": latest["date"].isoformat(),
            "first_date": first["date"].isoformat(),
            "change": f"{change:+.1f}",
            "change_pct": f"{change_pct:+.2f}",
            "positive": change >= 0,
            "sparkline_svg": polyline,
            "svg_w": svg_w,
            "svg_h": svg_h,
            "hi": f"{hi:.1f}",
            "lo": f"{lo:.1f}",
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
    gold_price = _fetch_gold_price()
    dxy = _fetch_dxy()
    gsr = _fetch_gold_silver_ratio()
    cot = _fetch_cot_positions()
    etf = _fetch_etf_holdings()
    aggregate = _fetch_gold_etf_aggregate()
    wgc = _fetch_wgc_commentary()
    return render_template(
        "dashboard.html",
        gold_price=gold_price,
        dxy=dxy,
        gsr=gsr,
        cot=cot,
        etf=etf,
        aggregate=aggregate,
        wgc=wgc,
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
