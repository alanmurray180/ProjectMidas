"""Client for gold ETF holdings / flows data.

Primary source: SPDR Gold Shares (GLD) publishes a daily CSV of
holdings at spdrgoldshares.com.  This is the world's largest gold ETF
and a reliable proxy for overall gold ETF flows.

Fallback: Yahoo Finance for price/NAV data of GLD and IAU.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import httpx
import pandas as pd

from midas.config import GLD_HOLDINGS_URL
from midas.models.gold import ETFHolding


class GoldETFClient:
    """Fetch gold ETF holdings data, primarily from SPDR Gold Shares."""

    def __init__(self, gld_url: str | None = None):
        self.gld_url = gld_url or GLD_HOLDINGS_URL

    def get_gld_holdings(self, since: Optional[date] = None) -> list[ETFHolding]:
        """Download GLD historical holdings from SPDR's published CSV.

        Returns daily snapshots of tonnes held in the trust.
        """
        resp = httpx.get(self.gld_url, timeout=30, follow_redirects=True)
        resp.raise_for_status()

        df = pd.read_csv(
            pd.io.common.StringIO(resp.text),
            skiprows=6,  # SPDR CSV has header junk
            on_bad_lines="skip",
        )

        # Normalise column names (they vary across vintages)
        df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

        date_col = next(c for c in df.columns if "date" in c)
        tonnes_col = next(c for c in df.columns if "tonnes" in c or "tonne" in c)

        holdings: list[ETFHolding] = []
        for _, row in df.iterrows():
            try:
                dt = _parse_date(str(row[date_col]).strip())
            except (ValueError, TypeError):
                continue
            if since and dt < since:
                continue
            try:
                tonnes = float(str(row[tonnes_col]).replace(",", ""))
            except (ValueError, TypeError):
                continue

            holdings.append(
                ETFHolding(date=dt, fund="GLD", tonnes=tonnes)
            )

        return sorted(holdings, key=lambda h: h.date)

    def get_gld_flows(self, since: Optional[date] = None) -> pd.DataFrame:
        """Compute daily flows (change in tonnes) from GLD holdings.

        Returns a DataFrame with columns: date, tonnes, daily_change_tonnes.
        """
        holdings = self.get_gld_holdings(since=since)
        if not holdings:
            return pd.DataFrame(columns=["date", "tonnes", "daily_change_tonnes"])

        df = pd.DataFrame([{"date": h.date, "tonnes": h.tonnes} for h in holdings])
        df = df.sort_values("date").reset_index(drop=True)
        df["daily_change_tonnes"] = df["tonnes"].diff()
        return df


def _parse_date(s: str) -> date:
    """Try common date formats used in SPDR CSVs."""
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {s!r}")
