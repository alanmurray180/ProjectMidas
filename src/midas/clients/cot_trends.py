"""COT positioning trends — contracts by side, and how they are moving.

A single COT snapshot says who is long and who is short.  What it cannot
say is whether 180,000 managed-money longs is a crowd or a vacuum, which
only the weeks behind it answer.  This module turns the weekly series from
:class:`~midas.clients.cftc.CFTCClient` into:

  * **contracts by side** — long, short, spread and net for each of the
    five trader categories, plus the gross totals, each as a share of open
    interest;
  * **trends** — the change in long, short and net over 1, 4, 13 and 52
    weeks, for every category;
  * **context** — where managed-money net, as a share of open interest,
    sits in its own 52-week range: a percentile, the high and the low.

The CFTC publishes five years of weekly reports, so the history is fetched
rather than accumulated: the trend is right from the first run, not after
a year of collecting.

Report timing: positions are as at Tuesday's close and published the
following Friday afternoon UK time, so the newest report is between three
and ten days old.  Trend readings move on Fridays and at no other time.
"""

from __future__ import annotations

import logging
from typing import Optional

from midas.models.gold import COTPosition

log = logging.getLogger(__name__)

# Trader categories in the disaggregated report, in the order the dashboard
# shows them: speculative money first, since that is the side that moves.
CATEGORIES: tuple[tuple[str, str, str], ...] = (
    ("mm", "Managed Money", "Hedge funds and CTAs — the speculative flow"),
    ("prod", "Producer / Merchant", "Miners, refiners and users hedging physical"),
    ("swap", "Swap Dealers", "Banks hedging OTC and index exposure"),
    ("other", "Other Reportables", "Large traders outside the other categories"),
    ("nonrep", "Non-Reportable", "Small traders below the reporting threshold"),
)

# Lookbacks in weekly reports.  52 weeks is a year of reports, not a
# calendar year: the CFTC skips no weeks, but a government shutdown can
# delay a run of them, so a window is always "reports back", never "days".
LOOKBACKS: tuple[tuple[str, int], ...] = (
    ("1w", 1),
    ("4w", 4),
    ("13w", 13),
    ("52w", 52),
)

# Just the labels, for callers laying out a column per lookback.
COT_LOOKBACK_LABELS = tuple(label for label, _ in LOOKBACKS)

# Window for the percentile and the range that frame the latest net figure.
CONTEXT_WEEKS = 52

# Percentile bands for the crowding read.  Tested over 260 reports against
# forward gold returns: 20/80 carried the clearest separation with enough
# observations behind it, where 10/90 fired too rarely to trust and 30/70
# washed the effect out.  Three states, not five — the intermediate bands
# behaved no differently from mid-range.
CROWDED_PCT = 80.0
WASHED_PCT = 20.0


def _percentile_rank(values: list[float], value: float) -> Optional[float]:
    """Where *value* sits within *values*, 0–100.

    Ties count as half, so a flat series reads 50 rather than 0 or 100.
    """
    if not values:
        return None
    below = sum(1 for v in values if v < value)
    equal = sum(1 for v in values if v == value)
    return (below + equal / 2) / len(values) * 100


class COTTrends:
    """Derive contract splits and trends from a weekly COT history."""

    def __init__(self, history: list[COTPosition]):
        # Oldest first: every lookback below indexes backwards from the end.
        self.history = sorted(history, key=lambda p: p.report_date)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _at(self, weeks_back: int) -> Optional[COTPosition]:
        """The report *weeks_back* reports before the latest one."""
        idx = len(self.history) - 1 - weeks_back
        return self.history[idx] if idx >= 0 else None

    @staticmethod
    def _side(pos: COTPosition, key: str, side: str) -> int:
        """Read one leg — ``mm`` + ``long`` — off a snapshot.

        Producers and non-reportables have no spread column in the
        disaggregated report, so that leg reads zero: it is absent by
        design rather than missing, and every category shares one layout.
        """
        return getattr(pos, f"{key}_{side}", 0)

    def _changes(self, key: str, side: str) -> dict[str, Optional[int]]:
        """Change in one leg over each lookback, in contracts."""
        latest = self.history[-1]
        now = self._side(latest, key, side)
        out: dict[str, Optional[int]] = {}
        for label, weeks in LOOKBACKS:
            prior = self._at(weeks)
            out[label] = None if prior is None else now - self._side(prior, key, side)
        return out

    def _category(self, key: str, label: str, note: str) -> dict:
        latest = self.history[-1]
        long_ = self._side(latest, key, "long")
        short = self._side(latest, key, "short")
        return {
            "key": key,
            "label": label,
            "note": note,
            "long": long_,
            "short": short,
            "spread": self._side(latest, key, "spread"),
            "net": long_ - short,
            "long_pct_oi": latest.pct_of_oi(long_),
            "short_pct_oi": latest.pct_of_oi(short),
            # Above 1.0 the category is net long.  Reads more naturally than
            # net contracts when comparing categories of different sizes.
            "long_short_ratio": (long_ / short) if short else None,
            "changes": {
                "long": self._changes(key, "long"),
                "short": self._changes(key, "short"),
                "net": self._changes(key, "net"),
            },
        }

    def _totals(self) -> dict:
        """Gross long and short across all categories.

        The two sides are equal by construction — every long is someone's
        short — so this row is a cross-check on the parse, and its open
        interest is the denominator every percentage above uses.
        """
        latest = self.history[-1]
        long_ = latest.total_long
        short = latest.total_short

        def _delta(attr: str) -> dict[str, Optional[int]]:
            now = getattr(latest, attr)
            return {
                label: (None if (p := self._at(weeks)) is None else now - getattr(p, attr))
                for label, weeks in LOOKBACKS
            }

        return {
            "key": "total",
            "label": "All categories",
            "note": "Gross contracts, spreads excluded",
            "long": long_,
            "short": short,
            "spread": latest.total_spread,
            "net": long_ - short,
            "long_pct_oi": latest.pct_of_oi(long_),
            "short_pct_oi": latest.pct_of_oi(short),
            "long_short_ratio": (long_ / short) if short else None,
            "changes": {
                "long": _delta("total_long"),
                "short": _delta("total_short"),
                # Zero in a clean report, since the two sides move together.
                # Computed rather than assumed: a non-zero reading here means
                # the rows were spliced from two contracts or two weeks.
                "net": {
                    label: (
                        None
                        if (p := self._at(weeks)) is None
                        else (long_ - short) - (p.total_long - p.total_short)
                    )
                    for label, weeks in LOOKBACKS
                },
            },
        }

    def _context(self) -> dict:
        """Frame managed-money net against its own recent range.

        Measured as a share of open interest rather than in contracts.
        Open interest is not constant across a year of reports, so a raw
        contract percentile drifts to "crowded" whenever the market grows,
        whoever is holding it.  The share asks the question that matters:
        how much of this market is one-way speculative money.
        """
        window_pos = self.history[-CONTEXT_WEEKS:]
        window = [
            share for p in window_pos if (share := p.pct_of_oi(p.mm_net)) is not None
        ]
        latest = window_pos[-1]
        current = latest.pct_of_oi(latest.mm_net)
        pct = _percentile_rank(window, current) if current is not None else None
        high = max(window) if window else None
        low = min(window) if window else None

        # Labels describe the crowd, not a recommendation: a crowded long is
        # where the fuel for a liquidation sits, not a sell signal on its own.
        if pct is None:
            label = "No history"
        elif pct >= CROWDED_PCT:
            label = "Crowded long"
        elif pct <= WASHED_PCT:
            label = "Washed out"
        else:
            label = "Mid-range"

        spread = None if high is None or low is None else high - low
        return {
            "weeks": len(window),
            # Every figure below is managed-money net as a percentage of open
            # interest, so the bar and the percentile cannot disagree.
            "current": current,
            "high": high,
            "low": low,
            # The contract count behind the share, for callers that want it.
            "net": latest.mm_net,
            "percentile": pct,
            "label": label,
            # Position within the range, which is what a range bar draws.
            "range_pct": (
                ((current - low) / spread * 100) if spread else 50.0
            ),
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute(self) -> dict:
        """Build the full positioning payload.

        Raises ``ValueError`` on an empty history: the caller renders an
        error card, which is honest, rather than a table of zeros.
        """
        if not self.history:
            raise ValueError("No COT history to analyse")

        latest = self.history[-1]
        categories = [self._category(k, lbl, note) for k, lbl, note in CATEGORIES]
        categories.append(self._totals())

        oi_changes = {
            label: (
                None
                if (prior := self._at(weeks)) is None
                else latest.open_interest - prior.open_interest
            )
            for label, weeks in LOOKBACKS
        }

        if not latest.balances:
            log.warning(
                "COT %s does not balance: %d long vs %d short — a leg is "
                "missing or mis-parsed",
                latest.report_date,
                latest.total_long,
                latest.total_short,
            )

        return {
            "report_date": latest.report_date,
            "market_name": latest.market_name,
            # Long must equal short in a clean report.  Carried through so
            # the health check can fail the panel instead of publishing
            # numbers that look plausible and are not.
            "balances": latest.balances,
            "weeks": len(self.history),
            "history_start": self.history[0].report_date,
            "open_interest": latest.open_interest,
            "oi_changes": oi_changes,
            "categories": categories,
            "context": self._context(),
            "series": self.series(),
        }

    def series(self) -> list[dict]:
        """The weekly history as flat rows — one per report, oldest first.

        This is what the CSV export and the charts read, so it carries every
        category rather than only the one the dashboard happens to plot.
        """
        rows = []
        for pos in self.history:
            row = {
                "report_date": pos.report_date.isoformat(),
                "market_name": pos.market_name,
                "open_interest": pos.open_interest,
            }
            for key, _, _ in CATEGORIES:
                row[f"{key}_long"] = self._side(pos, key, "long")
                row[f"{key}_short"] = self._side(pos, key, "short")
                row[f"{key}_spread"] = self._side(pos, key, "spread")
                row[f"{key}_net"] = self._side(pos, key, "long") - self._side(
                    pos, key, "short"
                )
            row["total_long"] = pos.total_long
            row["total_short"] = pos.total_short
            rows.append(row)
        return rows
