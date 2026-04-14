"""Aggregate holdings across the world's major physical-gold ETFs.

We can't easily get the World Gold Council's cross-issuer aggregate
(it sits behind a login wall on gold.org), so instead we compute our
own "Top Gold ETFs Aggregate" directly from each fund's live market
data on Yahoo Finance.

Strategy
--------
For each ETF we pull ``marketCap`` (USD) from Yahoo's ``quoteSummary``
endpoint.  Physical-gold ETFs trade very close to NAV (typically
within 10 bps), so::

    tonnes_gold = market_cap_usd / (spot_usd_per_oz * 32_150.7466)

gives a reliable estimate of the gold tonnage held by each fund —
without having to track per-share oz ratios that erode monthly due
to management fees.

The list below covers ~65% of global gold-ETF AUM (the remainder is
spread across dozens of smaller regional vehicles).

Yahoo's ``quoteSummary`` is unofficial and occasionally flaps.  We
fetch each ticker independently and simply skip any that fail, so a
single bad response doesn't nuke the whole aggregate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

log = logging.getLogger(__name__)

# 1 metric tonne = 32,150.7466 troy ounces.
_OZ_PER_TONNE = 32_150.7466

_YAHOO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://finance.yahoo.com/",
}

_QUOTE_SUMMARY = (
    "https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
)
_QUOTE_SUMMARY_ALT = (
    "https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
)


# Representative list of physical-gold ETFs with meaningful AUM.
# (ticker, human_name, listing_region)
GOLD_ETFS: tuple[tuple[str, str, str], ...] = (
    ("GLD",     "SPDR Gold Shares",              "North America"),
    ("IAU",     "iShares Gold Trust",            "North America"),
    ("GLDM",    "SPDR Gold MiniShares",          "North America"),
    ("SGOL",    "abrdn Physical Gold Shares",    "North America"),
    ("BAR",     "GraniteShares Gold Trust",      "North America"),
    ("OUNZ",    "VanEck Merk Gold Trust",        "North America"),
    ("AAAU",    "Goldman Sachs Physical Gold",   "North America"),
    ("4GLD.DE", "Xetra-Gold",                    "Europe"),
    ("PHAU.L",  "WisdomTree Physical Gold",      "Europe"),
    ("SGLN.L",  "iShares Physical Gold ETC",     "Europe"),
)


@dataclass
class ETFHolding:
    ticker: str
    name: str
    region: str
    market_cap_usd: Optional[float]
    price_usd: Optional[float]
    currency: str
    tonnes: Optional[float]


def _fetch_quote_summary(ticker: str) -> Optional[dict]:
    params = {"modules": "price,defaultKeyStatistics"}
    for url_tmpl in (_QUOTE_SUMMARY, _QUOTE_SUMMARY_ALT):
        url = url_tmpl.format(ticker=ticker)
        try:
            resp = httpx.get(
                url,
                params=params,
                headers=_YAHOO_HEADERS,
                timeout=20,
                follow_redirects=True,
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("quoteSummary", {}).get("result") or []
            if results:
                return results[0]
        except Exception as exc:
            log.info("quoteSummary %s failed on %s: %s", ticker, url, exc)
    return None


def _raw_value(obj: dict, key: str) -> Optional[float]:
    """Yahoo wraps numbers as {"raw": x, "fmt": "..."} most of the time."""
    val = obj.get(key)
    if isinstance(val, dict):
        return val.get("raw")
    if isinstance(val, (int, float)):
        return float(val)
    return None


def _load_usd_fx_rate(quote_currency: str) -> Optional[float]:
    """Return *how many USD* one unit of ``quote_currency`` is worth."""
    if quote_currency.upper() == "USD":
        return 1.0
    pair = f"{quote_currency.upper()}USD=X"
    summary = _fetch_quote_summary(pair)
    if not summary:
        return None
    price = summary.get("price") or {}
    return _raw_value(price, "regularMarketPrice")


class MajorETFsClient:
    """Aggregate tonnes & AUM across the top physical-gold ETFs."""

    def __init__(self, spot_usd_per_oz: Optional[float] = None) -> None:
        self.spot_usd_per_oz = spot_usd_per_oz

    def _resolve_spot(self) -> Optional[float]:
        if self.spot_usd_per_oz:
            return self.spot_usd_per_oz
        try:
            from midas.clients.metal_price import MetalPriceClient

            spot = MetalPriceClient().latest()
            return float(spot.price)
        except Exception as exc:
            log.warning("Could not resolve gold spot price: %s", exc)
            return None

    def get_aggregate(self) -> dict:
        """Fetch all ETFs, convert AUM to tonnes, return summary dict."""
        spot = self._resolve_spot()

        holdings: list[ETFHolding] = []
        fx_cache: dict[str, Optional[float]] = {"USD": 1.0}

        for ticker, name, region in GOLD_ETFS:
            summary = _fetch_quote_summary(ticker)
            if not summary:
                log.info("No data for %s; skipping", ticker)
                continue

            price_block = summary.get("price") or {}
            currency = (
                price_block.get("currency") or "USD"
            ).upper()
            market_cap = _raw_value(price_block, "marketCap")
            regular_price = _raw_value(price_block, "regularMarketPrice")

            # Convert market cap into USD if the fund reports in another ccy.
            if currency not in fx_cache:
                fx_cache[currency] = _load_usd_fx_rate(currency)
            fx = fx_cache.get(currency)
            market_cap_usd: Optional[float]
            if market_cap is not None and fx is not None:
                market_cap_usd = market_cap * fx
            else:
                market_cap_usd = None

            tonnes: Optional[float] = None
            if market_cap_usd and spot and spot > 0:
                tonnes = market_cap_usd / (spot * _OZ_PER_TONNE)

            holdings.append(
                ETFHolding(
                    ticker=ticker,
                    name=name,
                    region=region,
                    market_cap_usd=market_cap_usd,
                    price_usd=regular_price,
                    currency=currency,
                    tonnes=tonnes,
                )
            )

        total_aum_usd = sum(
            h.market_cap_usd or 0 for h in holdings if h.market_cap_usd
        )
        total_tonnes = sum(
            h.tonnes or 0 for h in holdings if h.tonnes
        )

        by_region: dict[str, dict[str, float]] = {}
        for h in holdings:
            if h.tonnes is None and h.market_cap_usd is None:
                continue
            bucket = by_region.setdefault(
                h.region, {"tonnes": 0.0, "aum_usd": 0.0, "fund_count": 0}
            )
            if h.tonnes:
                bucket["tonnes"] += h.tonnes
            if h.market_cap_usd:
                bucket["aum_usd"] += h.market_cap_usd
            bucket["fund_count"] += 1

        return {
            "spot_usd_per_oz": spot,
            "holdings": [h.__dict__ for h in holdings],
            "total_aum_usd": total_aum_usd or None,
            "total_tonnes": total_tonnes or None,
            "by_region": by_region,
            "fund_count": len(holdings),
        }
