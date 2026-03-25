"""Flask web app for the Midas gold trading dashboard."""

from __future__ import annotations

import logging
from datetime import date, timedelta

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
        since = date.today() - timedelta(days=30)
        holdings = client.get_gld_holdings(since=since)
        if not holdings:
            return {"error": "No holdings data available"}
        latest = holdings[-1]
        rows = [
            {"date": h.date.isoformat(), "tonnes": f"{h.tonnes:,.2f}"}
            for h in holdings[-10:]
        ]
        return {
            "latest_date": latest.date.isoformat(),
            "latest_tonnes": f"{latest.tonnes:,.2f}",
            "recent": rows,
        }
    except Exception as exc:
        return {"error": str(exc)}


@app.route("/")
def dashboard():
    gold_price = _fetch_gold_price()
    cot = _fetch_cot_positions()
    etf = _fetch_etf_holdings()
    return render_template("dashboard.html", gold_price=gold_price, cot=cot, etf=etf)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
