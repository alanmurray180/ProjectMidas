"""Data models for gold market data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional


@dataclass
class GoldPrice:
    """Spot gold price at a point in time."""
    timestamp: datetime
    currency: str
    price: float
    unit: str = "troy_oz"


@dataclass
class COTPosition:
    """CFTC Commitments of Traders positioning snapshot for gold futures."""
    report_date: date
    # Producer / Merchant / Processor / User
    prod_long: int
    prod_short: int
    # Swap Dealers
    swap_long: int
    swap_short: int
    swap_spread: int
    # Managed Money (hedge funds)
    mm_long: int
    mm_short: int
    mm_spread: int
    # Other Reportables
    other_long: int
    other_short: int
    other_spread: int
    # Non-Reportable
    nonrep_long: int
    nonrep_short: int
    # Open Interest
    open_interest: int

    @property
    def mm_net(self) -> int:
        """Managed-money net position — the market's key speculative signal."""
        return self.mm_long - self.mm_short


@dataclass
class ETFHolding:
    """Gold ETF holdings snapshot."""
    date: date
    fund: str  # e.g. "GLD", "IAU"
    tonnes: float
    ounces: Optional[float] = None
    value_usd: Optional[float] = None

    @property
    def daily_flow_tonnes(self) -> Optional[float]:
        """Placeholder — compute from a series externally."""
        return None


@dataclass
class PhysicalDemand:
    """Quarterly bar & coin / jewellery demand from WGC."""
    year: int
    quarter: int
    category: str  # "bar_and_coin", "jewellery", "central_bank", "technology"
    region: str
    tonnes: float
