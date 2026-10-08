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


# Window for the ratio index, in daily closes: roughly a year of trading
# days, fixed rather than tied to the page's 30-day / 12-month toggle, so
# the score does not change with the view.
GSR_INDEX_RANGE = "1y"

# Same 20/80 bands as the COT index, for one read across the Other column.
# Not backtested for the ratio: they are a starting point, not a finding.
GSR_HIGH_PCT = 80.0
GSR_LOW_PCT = 20.0


def gsr_signal(records: list[dict]) -> dict:
    """Score the latest ratio against its own trailing range.

    The index is the ratio's percentile within *records* (0–100).  The
    score reads it as a precious-metals regime signal: silver leading — a
    ratio low in its range — is the pattern of a broad metals bull and
    scores +1; silver lagging — a ratio high in its range — is the pattern
    of risk-off or deflationary stress and scores −1.

    Raises ``ValueError`` on an empty series, for the caller to turn into
    an N/A row rather than a score of zero.
    """
    from midas.clients.cot_trends import _percentile_rank

    if not records:
        raise ValueError("No gold/silver ratio history to score")
    ratios = [r["ratio"] for r in records]
    current = ratios[-1]
    pct = _percentile_rank(ratios, current)

    if pct >= GSR_HIGH_PCT:
        label, score = "Silver lagging", -1
    elif pct <= GSR_LOW_PCT:
        label, score = "Silver leading", 1
    else:
        label, score = "Mid-range", 0

    return {
        "current": current,
        "high": max(ratios),
        "low": min(ratios),
        "percentile": pct,
        "label": label,
        "score": score,
        "days": len(ratios),
        "date": records[-1]["date"],
    }
