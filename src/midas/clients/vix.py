"""Client for the CBOE Volatility Index (VIX).

Fetches daily close prices from Yahoo Finance (ticker ``^VIX``).
"""

from __future__ import annotations

from midas.clients.etf import _fetch_yahoo_chart


class VIXClient:
    """Fetch VIX daily close prices."""

    def get_prices(self, range_: str = "1mo") -> list[dict]:
        """Return daily VIX closes for the given Yahoo range (e.g. '1mo', '1y').

        Each record: ``{date: date, close: float, volume: int|None}``
        """
        return _fetch_yahoo_chart("^VIX", range_=range_)
