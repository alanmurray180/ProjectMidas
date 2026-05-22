"""Client for the CBOE Volatility Index (VIX).

Fetches daily close prices from Yahoo Finance (ticker ``^VIX``).
"""

from __future__ import annotations

from midas.clients.etf import _fetch_yahoo_chart


class VIXClient:
    """Fetch VIX daily close prices."""

    def get_prices(self, days: int = 30) -> list[dict]:
        """Return daily VIX closes for the last *days* calendar days.

        Each record: ``{date: date, close: float, volume: int|None}``
        """
        return _fetch_yahoo_chart("^VIX", range_=f"{days}d")
