"""Client for MetalPriceAPI — gold spot pricing.

API docs: https://metalpriceapi.com/documentation
Free tier: 100 requests/month, daily granularity.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import httpx

from midas.config import METALPRICEAPI_BASE, METALPRICEAPI_KEY
from midas.models.gold import GoldPrice


class MetalPriceClient:
    """Fetch gold spot prices from MetalPriceAPI."""

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or METALPRICEAPI_KEY
        self.base_url = base_url or METALPRICEAPI_BASE
        if not self.api_key:
            raise ValueError(
                "METALPRICEAPI_KEY is required. "
                "Get one at https://metalpriceapi.com and set it in .env"
            )

    def _get(self, endpoint: str, params: dict | None = None) -> dict:
        params = params or {}
        params["api_key"] = self.api_key
        url = f"{self.base_url}/{endpoint}"
        resp = httpx.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success", True):
            raise RuntimeError(f"MetalPriceAPI error: {data}")
        return data

    def latest(self, currency: str = "USD") -> GoldPrice:
        """Get the latest gold spot price.

        The API returns rates where base=XAU and value=units of currency per
        ounce, so XAUUSD is the gold price in USD.
        """
        data = self._get("latest", {"base": "XAU", "currencies": currency})
        rate_key = f"XAU{currency}"
        price = data["rates"][rate_key]
        return GoldPrice(
            timestamp=datetime.fromtimestamp(data["timestamp"]),
            currency=currency,
            price=price,
        )

    def historical(self, dt: date, currency: str = "USD") -> GoldPrice:
        """Get gold price for a specific historical date (YYYY-MM-DD)."""
        data = self._get(
            dt.strftime("%Y-%m-%d"),
            {"base": "XAU", "currencies": currency},
        )
        rate_key = f"XAU{currency}"
        price = data["rates"][rate_key]
        return GoldPrice(
            timestamp=datetime.combine(dt, datetime.min.time()),
            currency=currency,
            price=price,
        )

    def timeframe(
        self,
        start: date,
        end: date,
        currency: str = "USD",
    ) -> list[GoldPrice]:
        """Get daily gold prices over a date range."""
        data = self._get(
            "timeframe",
            {
                "start_date": start.strftime("%Y-%m-%d"),
                "end_date": end.strftime("%Y-%m-%d"),
                "base": "XAU",
                "currencies": currency,
            },
        )
        results = []
        rate_key = f"XAU{currency}"
        for date_str, rates in sorted(data.get("rates", {}).items()):
            results.append(
                GoldPrice(
                    timestamp=datetime.strptime(date_str, "%Y-%m-%d"),
                    currency=currency,
                    price=rates[rate_key],
                )
            )
        return results
