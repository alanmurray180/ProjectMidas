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
    # Which contract the row describes, e.g. "GOLD - COMMODITY EXCHANGE INC.".
    # The CFTC publishes several gold contracts (full-size, micro) under the
    # same commodity code, so a series built without checking this can splice
    # two different markets into one line.
    market_name: str = ""

    @property
    def mm_net(self) -> int:
        """Managed-money net position — the market's key speculative signal."""
        return self.mm_long - self.mm_short

    @property
    def prod_net(self) -> int:
        """Producer/merchant net — the commercial hedging side of the book."""
        return self.prod_long - self.prod_short

    @property
    def swap_net(self) -> int:
        return self.swap_long - self.swap_short

    @property
    def other_net(self) -> int:
        return self.other_long - self.other_short

    @property
    def nonrep_net(self) -> int:
        return self.nonrep_long - self.nonrep_short

    @property
    def total_long(self) -> int:
        """Gross long contracts across every trader category.

        Spread contracts are excluded: they are long and short at once, so
        adding them to either side double-counts the position.
        """
        return (
            self.prod_long
            + self.swap_long
            + self.mm_long
            + self.other_long
            + self.nonrep_long
        )

    @property
    def total_short(self) -> int:
        """Gross short contracts across every trader category."""
        return (
            self.prod_short
            + self.swap_short
            + self.mm_short
            + self.other_short
            + self.nonrep_short
        )

    @property
    def total_spread(self) -> int:
        """Spread contracts held by the reportable categories."""
        return self.swap_spread + self.mm_spread + self.other_spread

    @property
    def balances(self) -> bool:
        """Whether gross long equals gross short, as a real report does.

        Every long is someone's short, so a snapshot that fails this was
        mis-parsed — most likely a leg whose field name did not match and
        read as zero, which looks on screen like a category holding no
        position rather than like an error.
        """
        return self.total_long == self.total_short

    def pct_of_oi(self, contracts: int) -> float | None:
        """Express a contract count as a percentage of open interest."""
        if not self.open_interest:
            return None
        return contracts / self.open_interest * 100


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
