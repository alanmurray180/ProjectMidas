"""Flask web app for the Midas gold trading dashboard."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from flask import Flask, render_template, request

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates")

# Link targets for the 30D/12M toggle.  The Flask app serves both variants
# from one route via a query string; the static build emits them as two
# separate files, so the targets differ per rendering mode.
SERVED_LINKS = {
    "30d": "/?range=30d",
    "12m": "/?range=12m",
    "cot_csv": "/cot_history.csv",
}
STATIC_LINKS = {"30d": "./", "12m": "./12m.html", "cot_csv": "./cot_history.csv"}

# The published page is static, so it cannot rebuild itself — a button in
# the page would need a GitHub token, and the site is public.  These link
# to the workflows instead, where "Run workflow" is two clicks: one to
# rebuild and publish, one to probe the upstreams without deploying.
_ACTIONS = "https://github.com/alanmurray180/ProjectMidas/actions/workflows"
WORKFLOW_LINKS = {
    "rebuild": f"{_ACTIONS}/pages.yml",
    "check_sources": f"{_ACTIONS}/health.yml",
}

# Maps the user-facing toggle value to parameters for each data source.
_RANGE_PRESETS = {
    "30d": {
        "yahoo": "1mo",
        "fred_days": 45,
        "cpi_months": 6,
        "etf_rows": 10,
        # Overshoots the window so weekends and holidays still leave a full
        # span of trading days in the series.
        "spot_days": 35,
        "label": "30-day",
    },
    "12m": {
        "yahoo": "1y",
        "fred_days": 395,
        "cpi_months": 13,
        "etf_rows": 20,
        # MetalPriceAPI rejects a timeframe wider than 365 days, so this sits
        # just inside the cap rather than overshooting it the way fred_days
        # above does.
        "spot_days": 364,
        "label": "12-month",
    },
}

# The panels the dashboard renders, in display order, mapped to the context
# key that is only present once that panel actually has data.  Every
# ``_fetch_*`` helper traps its own exceptions, so a panel failing is
# invisible unless we inspect the payload — which is what this drives.
PANELS: tuple[tuple[str, str, str], ...] = (
    ("gold_price", "Gold spot price", "price"),
    ("dxy", "Dollar index", "latest"),
    ("gsr", "Gold/silver ratio", "latest"),
    ("real_yield", "10Y real yield", "latest"),
    ("cpi", "CPI year-on-year", "latest"),
    ("vix", "VIX", "latest"),
    ("cot", "CFTC positioning", "report_date"),
    ("etf", "ETF scorecard", "total_score"),
    ("macro", "Macro scorecard", "total_score"),
    ("aggregate", "Gold ETF aggregate", "holdings"),
    ("wgc", "WGC commentary", "title"),
    ("swiss_trade", "Swiss gold trade", "imports"),
)

# Panels that legitimately have nothing to show some of the time.  The WGC
# publishes monthly, so an absent write-up is not a fault.
_OPTIONAL_PANELS = frozenset({"wgc"})

# Panels whose data can arrive from more than one upstream, mapped to the
# source they are meant to run on.  A panel served by anything else has a
# full-looking payload and a dead primary — indistinguishable from healthy
# unless the source is checked, which is how the Swiss card reported "ok"
# for weeks on its Comtrade fallback with BAZG refusing every connection.
PRIMARY_SOURCE = {
    "swiss_trade": "BAZG",
    "cot": "cftc_commodity_code",
}


def _fallback_note(name: str, value: dict, payload: object) -> str | None:
    """Describe why a populated panel is still not running as intended.

    Returns None when the panel is genuinely healthy.  Each branch reads a
    signal the client already records, so this adds no new fetching.
    """
    primary = PRIMARY_SOURCE.get(name)
    if primary:
        source = value.get("source")
        if source and source != primary:
            return f"on {source} fallback, {primary} unavailable"

    # The headline price and its history are separate calls, so the card can
    # render a current figure with no line under it.  That is a real partial
    # outage of the panel, not a cosmetic one.
    if name == "gold_price" and not value.get("sparkline_svg"):
        return "price history unavailable, headline only"

    # The scorecard's tonnage leg falls back to a Yahoo-derived estimate when
    # every SPDR endpoint fails, which it currently does.
    if name == "etf":
        tonnes = value.get("tonnes")
        if isinstance(tonnes, dict) and tonnes.get("estimated"):
            return "GLD tonnage estimated from Yahoo, SPDR unavailable"

    # The aggregate exists to total tonnage; Yahoo happily returns every fund
    # with no size field at all, which looks like a full list of holdings.
    if name == "aggregate":
        rows = [r for r in payload if isinstance(r, dict)]
        if not any(r.get("tonnes") for r in rows):
            return "no tonnage for any fund"
        missing = sum(1 for r in rows if not r.get("tonnes"))
        if missing:
            return f"no tonnage for {missing} of {len(rows)} funds"

    # The scorecard still renders with a missing leg, but a leg scored "N/A"
    # means one of its three upstreams went dark.
    if name == "etf":
        dead = [
            leg
            for leg in ("tonnes", "volume", "price")
            if isinstance(value.get(leg), dict)
            and value[leg].get("label") in (None, "N/A")
        ]
        if dead:
            return f"no data for {', '.join(dead)}"
    return None


def _panel_state(name: str, value: object) -> tuple[str, str | None]:
    """Classify one panel as ``ok``, ``degraded`` or ``failed``, with a note.

    The note explains a non-ok state in the terms someone fixing it needs —
    which upstream is gone — rather than leaving them to read build logs.
    """
    if value is None:
        state = "degraded" if name in _OPTIONAL_PANELS else "failed"
        return state, "nothing published" if name in _OPTIONAL_PANELS else "no data"
    if not isinstance(value, dict):
        return "failed", "unexpected payload type"
    if value.get("error"):
        return "failed", str(value["error"])[:120]

    required = dict((n, k) for n, _, k in PANELS)[name]
    payload = value.get(required)
    if payload is None or payload == "":
        if name in _OPTIONAL_PANELS:
            return "degraded", "nothing published"
        return "failed", f"missing {required}"
    # Panels backed by a list (the ETF aggregate) are only useful populated.
    if isinstance(payload, (list, tuple)) and not payload:
        return "failed", f"{required} is empty"

    note = _fallback_note(name, value, payload)
    return ("degraded", note) if note else ("ok", None)


def panel_health(context: dict) -> dict:
    """Summarise which dashboard panels actually came back with data."""
    detail = {}
    for name, label, _ in PANELS:
        state, note = _panel_state(name, context.get(name))
        detail[name] = {"label": label, "state": state, "note": note}
    states = [d["state"] for d in detail.values()]
    return {
        "detail": detail,
        "ok": states.count("ok"),
        "degraded": states.count("degraded"),
        "failed": states.count("failed"),
        "total": len(states),
    }


def _fetch_gold_price(spot_days: int = 35) -> dict | None:
    from midas.clients.metal_price import MetalPriceClient

    # The constructor raises when the key is missing, so it belongs inside the
    # guard: otherwise an expired key takes the whole build down instead of
    # degrading this one card.
    try:
        client = MetalPriceClient()
        price = client.latest()
        out = {
            "price": f"{price.price:,.2f}",
            "currency": price.currency,
            "timestamp": price.timestamp.strftime("%Y-%m-%d %H:%M"),
        }
    except Exception as exc:
        return {"error": str(exc)}

    # The series is deliberately a second, separate call rather than part of
    # the block above: the headline price is the card's reason to exist, and
    # a history that fails should cost the chart, not the number.  It is the
    # same XAU spot series as the headline, so the last point of the line
    # agrees with the figure above it — charting futures here instead would
    # be free but would quietly plot a different instrument.
    try:
        end = date.today()
        series = client.timeframe(end - timedelta(days=spot_days), end)
        values = [p.price for p in series if p.price]
        if len(values) >= 2:
            latest_v, first_v = values[-1], values[0]
            change = latest_v - first_v
            change_pct = (change / first_v) * 100 if first_v else 0
            sl = _sparkline(values)
            out.update(
                change=f"{change:+,.2f}",
                change_pct=f"{change_pct:+.2f}",
                positive=change >= 0,
                first_date=series[0].timestamp.date().isoformat(),
                latest_date=series[-1].timestamp.date().isoformat(),
                sparkline_svg=sl["svg"],
                svg_w=sl["w"],
                svg_h=sl["h"],
                hi=f"{sl['hi']:,.2f}",
                lo=f"{sl['lo']:,.2f}",
            )
        else:
            log.info("Gold spot history: %d point(s), too few to chart", len(values))
    except Exception as exc:
        log.info("Gold spot history unavailable, headline only: %s", exc)
    return out


def _signed(value: int | None) -> str:
    """Format a contract change with an explicit sign, or an em dash.

    An unchanged leg prints as a bare ``0``: "+0" reads like a small
    addition at a glance, which is exactly what it is not.
    """
    if value is None:
        return "—"
    return "0" if value == 0 else f"{value:+,}"


def _cot_chart(series: list[float], w: int = 560, h: int = 90) -> dict:
    """Plot a COT series, keeping the zero line where the eye expects it.

    ``_sparkline`` scales to the data, which is right for a price and wrong
    for a net position: a series that never crosses zero would otherwise
    look as though it swung through it.  The scale here always includes
    zero, and the baseline's y coordinate comes back with the points so the
    template can draw it.
    """
    lo, hi = min(series), max(series)
    lo, hi = min(lo, 0), max(hi, 0)
    span = hi - lo if hi != lo else 1
    points = " ".join(
        f"{(i / max(len(series) - 1, 1)) * w:.1f},{h - ((v - lo) / span) * h:.1f}"
        for i, v in enumerate(series)
    )
    return {
        "points": points,
        "w": w,
        "h": h,
        "lo": lo,
        "hi": hi,
        "zero_y": f"{h - ((0 - lo) / span) * h:.1f}",
    }


def _cot_line(series: list[float], lo: float, hi: float, w: int, h: int) -> str:
    """Plot a series on a scale someone else set, for overlaid lines."""
    span = hi - lo if hi != lo else 1
    return " ".join(
        f"{(i / max(len(series) - 1, 1)) * w:.1f},{h - ((v - lo) / span) * h:.1f}"
        for i, v in enumerate(series)
    )


def cot_history_csv(rows: list[dict]) -> str:
    """Render the weekly COT history as CSV for a spreadsheet."""
    import csv
    import io

    if not rows:
        return ""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def _fetch_cot_positions() -> dict | None:
    try:
        from midas.clients.cftc import CFTCClient
        from midas.clients.cot_trends import COT_LOOKBACK_LABELS, COTTrends

        client = CFTCClient()
        history = client.get_history()
        data = COTTrends(history).compute()

        latest = history[-1]
        categories = [
            {
                "label": c["label"],
                "note": c["note"],
                "long": f"{c['long']:,}",
                "short": f"{c['short']:,}",
                "spread": f"{c['spread']:,}" if c["spread"] else "—",
                "net": _signed(c["net"]),
                "net_positive": c["net"] >= 0,
                "long_pct_oi": (
                    f"{c['long_pct_oi']:.1f}%" if c["long_pct_oi"] is not None else "—"
                ),
                "short_pct_oi": (
                    f"{c['short_pct_oi']:.1f}%" if c["short_pct_oi"] is not None else "—"
                ),
                "ratio": (
                    f"{c['long_short_ratio']:.2f}x"
                    if c["long_short_ratio"] is not None
                    else "—"
                ),
                # Trends per leg, so the table can answer "who is adding" —
                # a net figure alone cannot tell longs covering from shorts
                # piling in, and those are different markets.
                "trend": {
                    leg: {
                        label: {
                            "text": _signed(c["changes"][leg][label]),
                            "positive": (c["changes"][leg][label] or 0) > 0,
                            # An unchanged or unavailable leg is neither
                            # bullish nor bearish, so it stays uncoloured.
                            "flat": not c["changes"][leg][label],
                        }
                        for label in COT_LOOKBACK_LABELS
                    }
                    for leg in ("long", "short", "net")
                },
                "is_total": c["key"] == "total",
                "is_mm": c["key"] == "mm",
            }
            for c in data["categories"]
        ]

        mm = next(c for c in data["categories"] if c["key"] == "mm")
        ctx = data["context"]
        net_series = [row["mm_net"] for row in data["series"]]
        long_series = [row["mm_long"] for row in data["series"]]
        short_series = [row["mm_short"] for row in data["series"]]

        # Plot at most two years: five years of weekly points in a 560px box
        # is a smear, and the recent shape is what the eye is reading for.
        plot_weeks = min(len(net_series), 104)
        net_chart = _cot_chart(net_series[-plot_weeks:])
        gross_lo = min(min(long_series[-plot_weeks:]), min(short_series[-plot_weeks:]))
        gross_hi = max(max(long_series[-plot_weeks:]), max(short_series[-plot_weeks:]))
        gross_w, gross_h = 560, 90

        report_age = (date.today() - data["report_date"]).days

        return {
            "report_date": data["report_date"].isoformat(),
            "report_age_days": report_age,
            "market_name": data["market_name"] or "COMEX gold futures",
            "history_weeks": data["weeks"],
            "history_start": data["history_start"].isoformat(),
            "open_interest": f"{data['open_interest']:,}",
            "oi_change_1w": _signed(data["oi_changes"]["1w"]),
            "oi_change_4w": _signed(data["oi_changes"]["4w"]),
            "categories": categories,
            "lookbacks": list(COT_LOOKBACK_LABELS),
            # Headline managed-money figures, kept at the top level so the
            # summary row does not have to dig through the table.
            "mm_long": f"{latest.mm_long:,}",
            "mm_short": f"{latest.mm_short:,}",
            "mm_net": f"{latest.mm_net:+,}",
            "mm_net_positive": latest.mm_net >= 0,
            "mm_net_change_1w": _signed(mm["changes"]["net"]["1w"]),
            "mm_net_change_4w": _signed(mm["changes"]["net"]["4w"]),
            "mm_net_change_13w": _signed(mm["changes"]["net"]["13w"]),
            "mm_net_change_52w": _signed(mm["changes"]["net"]["52w"]),
            # The same net changes with their direction attached, for the
            # one-pager, which colours them rather than tabulating them.
            "mm_net_trend": {
                label: {
                    "text": _signed(mm["changes"]["net"][label]),
                    "positive": (mm["changes"]["net"][label] or 0) > 0,
                    "flat": not mm["changes"]["net"][label],
                }
                for label in COT_LOOKBACK_LABELS
            },
            "context_label": ctx["label"],
            "context_weeks": ctx["weeks"],
            "context_percentile": (
                f"{ctx['percentile']:.0f}" if ctx["percentile"] is not None else "—"
            ),
            "context_high": f"{ctx['high']:+,}",
            "context_low": f"{ctx['low']:+,}",
            "context_range_pct": f"{ctx['range_pct']:.1f}",
            "plot_weeks": plot_weeks,
            "plot_start": data["series"][-plot_weeks]["report_date"],
            "net_chart": net_chart,
            "gross_chart": {
                "long": _cot_line(
                    long_series[-plot_weeks:], gross_lo, gross_hi, gross_w, gross_h
                ),
                "short": _cot_line(
                    short_series[-plot_weeks:], gross_lo, gross_hi, gross_w, gross_h
                ),
                "w": gross_w,
                "h": gross_h,
                "lo": f"{gross_lo:,}",
                "hi": f"{gross_hi:,}",
            },
            # The full weekly series, published alongside the page as CSV so
            # the history is usable in a spreadsheet rather than only on
            # screen.  Not rendered into the HTML.
            "series": data["series"],
            # Which Socrata filter answered.  The commodity-code query is the
            # precise one; the looser name matches are fallbacks worth
            # knowing about, since they can pick up the wrong contract.
            "source": client.source_used,
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
                "estimated": tonnes.get("estimated", False),
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


def _fetch_macro_scorecard() -> dict | None:
    try:
        from midas.clients.macro_scorecard import MacroScorecard

        data = MacroScorecard().compute()
        score = data["total_score"]
        labels = {
            4: "Strong Bullish", 3: "Bullish", 2: "Moderately Bullish",
            1: "Slightly Bullish", 0: "Neutral",
            -1: "Slightly Bearish", -2: "Moderately Bearish",
            -3: "Bearish", -4: "Strong Bearish",
        }
        return {
            "total_score": score,
            "score_label": labels.get(score, "Neutral"),
            "positive": score > 0,
            "negative": score < 0,
            "dxy": data["dxy"],
            "yield_10y": data["yield_10y"],
            "cpi": data["cpi"],
            "vix": data["vix"],
        }
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return {"error": str(exc)}


def _fetch_swiss_gold_trade() -> dict | None:
    try:
        from midas.clients.swiss_trade import SwissGoldTradeClient

        data = SwissGoldTradeClient().get_trade()
        if not data["imports"] and not data["exports"]:
            return {"error": "No Swiss gold trade data available"}

        def _fmt_val(v):
            if v >= 1e9:
                return f"${v / 1e9:,.1f}B"
            if v >= 1e6:
                return f"${v / 1e6:,.0f}M"
            return f"${v:,.0f}"

        def _fmt_kg(v):
            if v >= 1000:
                return f"{v / 1000:,.1f}t"
            return f"{v:,.0f} kg"

        def _fmt_row(r):
            return {
                "country": r["country"],
                "value_usd": _fmt_val(r["value_usd"]),
                "weight_kg": _fmt_kg(r["weight_kg"]),
                "raw_value": r["value_usd"],
                "raw_weight": r["weight_kg"],
            }

        by_type = []
        for t in data.get("by_type", []):
            by_type.append({
                "label": t["label"],
                "import_kg": _fmt_kg(t["import_kg"]),
                "export_kg": _fmt_kg(t["export_kg"]),
            })

        return {
            "year": data["year"],
            "source": data.get("source", ""),
            "imports": [_fmt_row(r) for r in data["imports"][:15]],
            "exports": [_fmt_row(r) for r in data["exports"][:15]],
            "by_type": by_type,
            "total_import_usd": _fmt_val(data["total_import_usd"]),
            "total_export_usd": _fmt_val(data["total_export_usd"]),
            "total_import_kg": _fmt_kg(data["total_import_kg"]),
            "total_export_kg": _fmt_kg(data["total_export_kg"]),
        }
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return {"error": str(exc)}


def build_context(period: str = "30d", links: dict | None = None) -> dict:
    """Gather every dashboard panel for *period* into a template context.

    Each ``_fetch_*`` helper traps its own exceptions and returns an
    ``{"error": ...}`` payload, so one dead upstream degrades a single card
    rather than the whole page.
    """
    if period not in _RANGE_PRESETS:
        period = "30d"
    preset = _RANGE_PRESETS[period]
    yr = preset["yahoo"]

    context = {
        "gold_price": _fetch_gold_price(preset["spot_days"]),
        "dxy": _fetch_dxy(yr),
        "gsr": _fetch_gold_silver_ratio(yr),
        "real_yield": _fetch_real_yield(preset["fred_days"]),
        "cpi": _fetch_cpi(preset["cpi_months"]),
        "vix": _fetch_vix(yr),
        "cot": _fetch_cot_positions(),
        "etf": _fetch_etf_scorecard(),
        "macro": _fetch_macro_scorecard(),
        "aggregate": _fetch_gold_etf_aggregate(),
        "wgc": _fetch_wgc_commentary(),
        "swiss_trade": _fetch_swiss_gold_trade(),
    }
    generated = datetime.now(timezone.utc)
    context.update(
        period=period,
        range_label=preset["label"],
        links=links or SERVED_LINKS,
        workflows=WORKFLOW_LINKS,
        generated_at=generated.strftime("%Y-%m-%d %H:%M UTC"),
        # Machine-readable twin of generated_at.  The page has no server to
        # ask how old it is, so it works its own age out from this.
        generated_at_iso=generated.replace(microsecond=0).isoformat(),
        health=panel_health(context),
    )
    return context


def render_context(context: dict) -> str:
    """Render a prepared context to HTML, supplying an application context.

    Split out from :func:`render_dashboard` so the static build can inspect
    the panel health it is about to publish.
    """
    with app.app_context():
        return render_template("dashboard.html", **context)


def render_dashboard(period: str = "30d", links: dict | None = None) -> str:
    """Render the dashboard to an HTML string."""
    return render_context(build_context(period, links))


@app.route("/cot_history.csv")
def cot_history():
    """Serve the weekly COT history the COT card links to.

    The static build writes this file once per build; served live it costs
    one CFTC call, which is the same call the page itself makes.
    """
    from flask import Response

    cot = _fetch_cot_positions() or {}
    csv_text = cot_history_csv(cot.get("series") or [])
    if not csv_text:
        return Response("report_date\n", mimetype="text/csv", status=503)
    return Response(
        csv_text,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=cot_history.csv"},
    )


@app.route("/")
def dashboard():
    period = request.args.get("range", "30d")
    return render_template("dashboard.html", **build_context(period))


if __name__ == "__main__":
    app.run(debug=True, port=5000)
