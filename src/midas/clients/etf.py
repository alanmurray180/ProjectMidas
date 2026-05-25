"""Client for gold ETF data.

Fetches GLD price history from Yahoo Finance's chart API.
Each GLD share tracks ~1/10th troy oz of gold, so price moves reflect
gold market dynamics.  Daily close prices are used as a proxy for ETF
activity when raw holdings data is not available.

Fallback source: IAU (iShares Gold Trust) if GLD request fails.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import httpx

_YAHOO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://finance.yahoo.com/",
}

_YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
_YAHOO_CHART_ALT = "https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"


def _fetch_yahoo_chart(ticker: str, range_: str = "1mo") -> list[dict]:
    """Fetch daily OHLCV data from Yahoo Finance for *ticker*.

    Returns a list of dicts with keys: date, open, high, low, close, volume.
    Tries query1 then query2 as a fallback.
    """
    params = {"interval": "1d", "range": range_}
    last_exc: Exception | None = None

    for url_tmpl in (_YAHOO_CHART, _YAHOO_CHART_ALT):
        url = url_tmpl.format(ticker=ticker)
        try:
            resp = httpx.get(
                url,
                params=params,
                headers=_YAHOO_HEADERS,
                timeout=30,
                follow_redirects=True,
            )
            resp.raise_for_status()
            data = resp.json()
            result = data["chart"]["result"][0]
            timestamps = result["timestamp"]
            quotes = result["indicators"]["quote"][0]
            closes = quotes.get("close", [])
            volumes = quotes.get("volume", [])

            records = []
            for i, ts in enumerate(timestamps):
                close = closes[i] if i < len(closes) else None
                volume = volumes[i] if i < len(volumes) else None
                if close is None:
                    continue
                records.append(
                    {
                        "date": datetime.utcfromtimestamp(ts).date(),
                        "close": round(float(close), 2),
                        "volume": int(volume) if volume is not None else None,
                    }
                )
            return sorted(records, key=lambda r: r["date"])
        except Exception as exc:
            last_exc = exc

    raise RuntimeError(
        f"Yahoo Finance chart fetch failed for {ticker}: {last_exc}"
    )


class GoldETFClient:
    """Fetch GLD (and IAU) ETF price data from Yahoo Finance."""

    def get_gld_prices(self, range_: str = "1mo") -> list[dict]:
        """Return daily GLD close prices for the given Yahoo range.

        Each record: {date: date, close: float, volume: int|None}
        Falls back to IAU if GLD fails.
        """
        try:
            return _fetch_yahoo_chart("GLD", range_=range_)
        except Exception:
            return _fetch_yahoo_chart("IAU", range_=range_)
