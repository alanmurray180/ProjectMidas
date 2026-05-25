"""Gold/Silver ratio client.

Fetches daily close prices for gold futures (GC=F) and silver futures
(SI=F) from Yahoo Finance, then computes the ratio.  The 30-day
history feeds a sparkline on the dashboard.
"""

from __future__ import annotations

from midas.clients.etf import _fetch_yahoo_chart


class GoldSilverRatioClient:
    """Compute the gold/silver price ratio from futures data."""

    def get_ratio(self, range_: str = "1mo") -> list[dict]:
        """Return daily gold/silver ratio for the given Yahoo range.

        Each record: ``{date: date, gold: float, silver: float, ratio: float}``
        """
        gold = _fetch_yahoo_chart("GC=F", range_=range_)
        silver = _fetch_yahoo_chart("SI=F", range_=range_)

        silver_by_date = {s["date"]: s["close"] for s in silver}

        records = []
        for g in gold:
            s_close = silver_by_date.get(g["date"])
            if s_close and s_close > 0:
                records.append(
                    {
                        "date": g["date"],
                        "gold": g["close"],
                        "silver": s_close,
                        "ratio": round(g["close"] / s_close, 2),
                    }
                )
        return sorted(records, key=lambda r: r["date"])
