"""Client for MetalPriceAPI — gold spot pricing.

API docs: https://metalpriceapi.com/documentation
Free tier: 100 requests/month, daily granularity.

NOTE: Free-tier plans may not support base=XAU.  We request base=USD and
look up the USDXAU rate which is already the gold price per troy ounce.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

import httpx

from midas.config import METALPRICEAPI_BASE, METALPRICEAPI_KEY
from midas.models.gold import GoldPrice

log = logging.getLogger(__name__)


def _extract_gold_price(rates: dict, currency: str) -> float:
    """Extract a gold price from an API rates dict.

    The API is called with base=USD (or other fiat currency) and
    currencies=XAU.  The response contains two keys:
      - {currency}XAU: gold price per troy oz (e.g. USDXAU = 4410.08)
      - XAU: fractional XAU per unit of currency (e.g. 0.000227)

    We prefer the {currency}XAU key as it is already the price.
    """
    rate_key = f"{currency}XAU"
    price = rates.get(rate_key)
    if price is None or price == 0:
        # Fallback: try the "XAU" key and invert
        xau_rate = rates.get("XAU")
        if xau_rate and xau_rate != 0:
            price = 1.0 / xau_rate
            log.debug("rates[XAU] = %s → gold price = %s", xau_rate, price)
        else:
            raise ValueError(f"Cannot extract gold price from rates: {rates}")
    else:
        log.debug("rates[%s] = %s", rate_key, price)
    return price


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
        log.debug("MetalPriceAPI %s response: %s", endpoint, data)
        if not data.get("success", True):
            raise RuntimeError(f"MetalPriceAPI error: {data}")
        return data

    def latest(self, currency: str = "USD") -> GoldPrice:
        """Get the latest gold spot price."""
        data = self._get("latest", {"base": currency, "currencies": "XAU"})
        price = _extract_gold_price(data["rates"], currency)
        return GoldPrice(
            timestamp=datetime.fromtimestamp(data["timestamp"]),
            currency=currency,
            price=price,
        )

    def historical(self, dt: date, currency: str = "USD") -> GoldPrice:
        """Get gold price for a specific historical date (YYYY-MM-DD)."""
        data = self._get(
            dt.strftime("%Y-%m-%d"),
            {"base": currency, "currencies": "XAU"},
        )
        price = _extract_gold_price(data["rates"], currency)
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
                "base": currency,
                "currencies": "XAU",
            },
        )
        results = []
        for date_str, rates in sorted(data.get("rates", {}).items()):
            price = _xau_to_usd(rates, currency)
            results.append(
                GoldPrice(
                    timestamp=datetime.strptime(date_str, "%Y-%m-%d"),
                    currency=currency,
                    price=price,
                )
            )
        return results
