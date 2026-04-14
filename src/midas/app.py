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


def _fetch_wgc_etf() -> dict | None:
    try:
        from midas.clients.wgc import WGCETFClient

        client = WGCETFClient()
        data = client.get_latest()

        def _fmt_num(v, decimals=1):
            if v is None:
                return None
            return f"{v:,.{decimals}f}"

        return {
            "report_period": data.get("report_period"),
            "global_tonnes": _fmt_num(data.get("global_tonnes"), 1),
            "global_aum_bn": _fmt_num(data.get("global_aum_bn"), 1),
            "regional_flows": [
                {
                    "region": r["region"],
                    "flow_usd_mn": _fmt_num(r["flow_usd_mn"], 1),
                    "positive": r["flow_usd_mn"] > 0,
                }
                for r in data.get("regional_flows", [])
            ],
            "source_url": data.get("source_url"),
        }
    except Exception as exc:
        return {"error": str(exc)}


@app.route("/")
def dashboard():
    gold_price = _fetch_gold_price()
    cot = _fetch_cot_positions()
    etf = _fetch_etf_holdings()
    wgc = _fetch_wgc_etf()
    return render_template(
        "dashboard.html",
        gold_price=gold_price,
        cot=cot,
        etf=etf,
        wgc=wgc,
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
