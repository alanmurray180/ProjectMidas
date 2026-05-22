"""Client for the FRED (Federal Reserve Economic Data) API.

Fetches time-series observations for any FRED series.  Used on the
dashboard for:

  * **DFII10** — 10-Year Real Yield (Treasury Inflation-Indexed
    Security, Constant Maturity).  Daily frequency.
  * **CPIAUCSL** — Consumer Price Index for All Urban Consumers:
    All Items.  Monthly frequency; we compute the trailing 12-month
    YoY percentage change to show headline inflation.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

import httpx

from midas.config import FRED_API_KEY, FRED_BASE

log = logging.getLogger(__name__)

_OBSERVATIONS = f"{FRED_BASE}/series/observations"


def _fetch_series(
    series_id: str,
    limit: int = 60,
    sort_order: str = "desc",
    observation_start: Optional[str] = None,
) -> list[dict]:
    params: dict = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "sort_order": sort_order,
        "limit": limit,
    }
    if observation_start:
        params["observation_start"] = observation_start

    resp = httpx.get(_OBSERVATIONS, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    records = []
    for obs in data.get("observations", []):
        val = obs.get("value")
        if val is None or val == ".":
            continue
        try:
            records.append(
                {
                    "date": date.fromisoformat(obs["date"]),
                    "value": float(val),
                }
            )
        except (ValueError, KeyError):
            continue
    return sorted(records, key=lambda r: r["date"])


class FREDClient:
    """Fetch economic data series from FRED."""

    def get_real_yield_10y(self, days: int = 30) -> list[dict]:
        """Daily 10-Year real yield (DFII10) for the last *days*."""
        start = (date.today() - timedelta(days=days + 10)).isoformat()
        records = _fetch_series(
            "DFII10", limit=days + 10, sort_order="desc",
            observation_start=start,
        )
        return records[-days:] if len(records) > days else records

    def get_cpi_yoy(self, months: int = 13) -> list[dict]:
        """Monthly CPI (CPIAUCSL) trailing 12-month YoY % change.

        We fetch *months* + 12 raw observations so that the earliest
        returned month has a year-ago comparator.
        """
        start = (
            date.today() - timedelta(days=(months + 13) * 31)
        ).isoformat()
        raw = _fetch_series(
            "CPIAUCSL", limit=months + 14, sort_order="desc",
            observation_start=start,
        )
        if len(raw) < 13:
            return []

        by_date = {r["date"]: r["value"] for r in raw}
        sorted_dates = sorted(by_date.keys())

        result: list[dict] = []
        for d in sorted_dates:
            year_ago = d.replace(year=d.year - 1)
            candidates = [
                dd for dd in sorted_dates
                if abs((dd - year_ago).days) <= 35
            ]
            if not candidates:
                continue
            closest = min(candidates, key=lambda dd: abs((dd - year_ago).days))
            prev = by_date[closest]
            cur = by_date[d]
            if prev > 0:
                yoy = ((cur - prev) / prev) * 100
                result.append({"date": d, "value": round(yoy, 2)})
        return result[-months:] if len(result) > months else result
