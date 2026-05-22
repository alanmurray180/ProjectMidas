"""Client for the US Dollar Index (DXY).

Fetches daily close prices from Yahoo Finance using the same chart API
as the ETF client (ticker ``DX-Y.NYB``).  The data feeds a sparkline
trend chart on the dashboard and the current level / change indicators.
"""

from __future__ import annotations

from midas.clients.etf import _fetch_yahoo_chart


class DXYClient:
    """Fetch DXY (US Dollar Index) daily close prices."""

    def get_prices(self, days: int = 30) -> list[dict]:
        """Return daily DXY closes for the last *days* calendar days.

        Each record: ``{date: date, close: float, volume: int|None}``
        """
        return _fetch_yahoo_chart("DX-Y.NYB", range_=f"{days}d")
