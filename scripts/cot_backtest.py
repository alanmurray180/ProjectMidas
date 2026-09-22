#!/usr/bin/env python3
"""Measure the proposed COT scorecard signals against forward gold returns.

Four candidate signals, each scored +1 / 0 / −1 from gold's perspective:

  1. Spec momentum   — managed-money net rose over 4 reports
  2. Spec crowding   — MM net as a share of open interest, against its own
                       trailing 52-report range (contrarian: crowded = −1)
  3. Short-leg flow  — managed-money shorts fell over 4 reports
  4. Participation   — open interest confirms the price move, and scores
                       zero when open interest is falling

The question this answers is not "does the score look sensible" but "does
a reading of +1 precede a better gold return than a reading of −1, by
enough to matter".  Two things make that harder than it sounds, and both
are reported rather than hidden:

**Drift.** Gold rose hard across this sample, so every bucket's raw return
is positive.  Only the excess over the all-period baseline says anything,
so that is the column to read.

**Overlap.** Weekly observations with 4- and 13-week forward returns
overlap heavily: ~260 observations carry only ~65 independent 4-week
windows and ~20 independent 13-week ones.  Differences that look large can
still be noise, so the effective sample size is printed beside each.

No lookahead: every signal at report *i* uses only reports up to *i*, and
the entry price is the first close on or after the Friday the report is
published — never the Tuesday the positions were taken.

Run it from the **Check data sources** workflow; the CFTC and Yahoo hosts
are not reachable from every environment::

    python scripts/cot_backtest.py
"""

from __future__ import annotations

import statistics
import sys
from datetime import date, datetime, timedelta

import httpx

from midas.clients.cftc import CFTCClient

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

# The report lands Friday afternoon, three days after the Tuesday close it
# describes.  Entering before that is trading on information nobody had.
PUBLICATION_LAG_DAYS = 3

# Forward horizons, in weeks.
HORIZONS = (1, 4, 13)

# Lookback for the momentum and participation signals, in weekly reports.
MOMENTUM_WEEKS = 4

# Trailing window for the crowding percentile, in weekly reports.
CROWDING_WEEKS = 52

# Percentile thresholds to sweep for the crowding signal: (low, high).
CROWDING_THRESHOLDS = ((10, 90), (20, 80), (30, 70))


def gold_closes() -> list[tuple[date, float]]:
    """Daily gold futures closes, oldest first."""
    resp = httpx.get(
        YAHOO_CHART,
        params={"interval": "1d", "range": "10y"},
        headers=HEADERS,
        timeout=30,
        follow_redirects=True,
    )
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    stamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    out = [
        (datetime.utcfromtimestamp(ts).date(), close)
        for ts, close in zip(stamps, closes)
        if close is not None
    ]
    return sorted(out)


def close_on_or_after(closes: list[tuple[date, float]], when: date) -> float | None:
    for day, close in closes:
        if day >= when:
            return close
    return None


def close_on_or_before(closes: list[tuple[date, float]], when: date) -> float | None:
    found = None
    for day, close in closes:
        if day <= when:
            found = close
        else:
            break
    return found


def _percentile(values: list[float], value: float) -> float:
    below = sum(1 for v in values if v < value)
    equal = sum(1 for v in values if v == value)
    return (below + equal / 2) / len(values) * 100


def _sign(value: float) -> int:
    return (value > 0) - (value < 0)


def build_panel(history, closes, crowding_lo: int, crowding_hi: int) -> list[dict]:
    """One row per report: the four signals, and the returns that followed."""
    rows = []
    for i, pos in enumerate(history):
        if i < max(MOMENTUM_WEEKS, CROWDING_WEEKS - 1):
            continue  # not enough history behind this report to score it

        prior = history[i - MOMENTUM_WEEKS]

        # 1. Spec momentum.
        s1 = _sign(pos.mm_net - prior.mm_net)

        # 2. Spec crowding, normalised by open interest so the percentile is
        #    not flattered by open interest growing over the years.
        window = [
            p.mm_net / p.open_interest
            for p in history[i - CROWDING_WEEKS + 1 : i + 1]
            if p.open_interest
        ]
        share = pos.mm_net / pos.open_interest if pos.open_interest else 0.0
        pct = _percentile(window, share) if window else 50.0
        s2 = 1 if pct <= crowding_lo else (-1 if pct >= crowding_hi else 0)

        # 3. Short-leg flow: shorts falling is bullish.
        s3 = -_sign(pos.mm_short - prior.mm_short)

        # 4. Participation: open interest confirming the move, Tuesday to
        #    Tuesday so the price window matches the positioning window.
        price_now = close_on_or_before(closes, pos.report_date)
        price_then = close_on_or_before(closes, prior.report_date)
        if price_now is None or price_then is None:
            continue
        doi = pos.open_interest - prior.open_interest
        s4 = _sign(price_now - price_then) if doi > 0 else 0

        entry_day = pos.report_date + timedelta(days=PUBLICATION_LAG_DAYS)
        entry = close_on_or_after(closes, entry_day)
        if entry is None:
            continue

        row = {
            "report_date": pos.report_date,
            "s1": s1,
            "s2": s2,
            "s3": s3,
            "s4": s4,
            "score": s1 + s2 + s3 + s4,
            "mm_net_pct_oi": share * 100,
            "crowding_pct": pct,
        }
        for weeks in HORIZONS:
            exit_price = close_on_or_before(closes, entry_day + timedelta(weeks=weeks))
            row[f"fwd{weeks}"] = (
                None if exit_price is None else (exit_price / entry - 1) * 100
            )
        rows.append(row)
    return rows


def _stats(values: list[float]) -> tuple[int, float, float, float]:
    if not values:
        return 0, 0.0, 0.0, 0.0
    hit = sum(1 for v in values if v > 0) / len(values) * 100
    return len(values), statistics.mean(values), statistics.median(values), hit


def report_signal(rows: list[dict], key: str, label: str, baseline: dict) -> None:
    print(f"\n{label}")
    print(f"  {'state':<8}{'horizon':<9}{'n':>5}{'eff n':>7}{'mean %':>9}"
          f"{'vs base':>9}{'median %':>10}{'hit %':>8}")
    for state in (1, 0, -1):
        for weeks in HORIZONS:
            vals = [
                r[f"fwd{weeks}"]
                for r in rows
                if r[key] == state and r[f"fwd{weeks}"] is not None
            ]
            n, mean, median, hit = _stats(vals)
            if not n:
                continue
            # Overlapping windows: only every weeks-th observation is
            # independent, so say what the sample is really worth.
            eff = max(1, round(n / weeks))
            print(f"  {state:<+8}{str(weeks) + 'w':<9}{n:>5}{eff:>7}{mean:>9.2f}"
                  f"{mean - baseline[weeks]:>9.2f}{median:>10.2f}{hit:>8.1f}")


def report_score(rows: list[dict], baseline: dict) -> None:
    buckets = (
        ("<= -2", lambda s: s <= -2),
        ("-1", lambda s: s == -1),
        ("0", lambda s: s == 0),
        ("+1", lambda s: s == 1),
        (">= +2", lambda s: s >= 2),
    )
    print("\nComposite score (sum of the four)")
    print(f"  {'bucket':<8}{'horizon':<9}{'n':>5}{'eff n':>7}{'mean %':>9}"
          f"{'vs base':>9}{'median %':>10}{'hit %':>8}")
    for name, test in buckets:
        for weeks in HORIZONS:
            vals = [
                r[f"fwd{weeks}"]
                for r in rows
                if test(r["score"]) and r[f"fwd{weeks}"] is not None
            ]
            n, mean, median, hit = _stats(vals)
            if not n:
                continue
            eff = max(1, round(n / weeks))
            print(f"  {name:<8}{str(weeks) + 'w':<9}{n:>5}{eff:>7}{mean:>9.2f}"
                  f"{mean - baseline[weeks]:>9.2f}{median:>10.2f}{hit:>8.1f}")


def correlations(rows: list[dict]) -> None:
    """How much the signals repeat each other, as plain agreement rates."""
    keys = ("s1", "s2", "s3", "s4")
    print("\nSignal agreement (share of reports where the pair scores alike)")
    print(f"  {'':<6}" + "".join(f"{k:>7}" for k in keys))
    for a in keys:
        cells = []
        for b in keys:
            same = sum(1 for r in rows if r[a] == r[b]) / len(rows) * 100
            cells.append(f"{same:>7.0f}")
        print(f"  {a:<6}" + "".join(cells))


def main() -> int:
    print("Fetching 260 weekly COT reports (futures only) ...", flush=True)
    history = CFTCClient(dataset="futures_only").get_history(weeks=260)
    if len(history) < CROWDING_WEEKS + MOMENTUM_WEEKS:
        print(f"only {len(history)} reports; not enough to score", flush=True)
        return 1

    print("Fetching gold futures daily closes ...", flush=True)
    closes = gold_closes()
    print(
        f"COT: {len(history)} reports, {history[0].report_date} to "
        f"{history[-1].report_date}",
        flush=True,
    )
    print(f"Price: {len(closes)} closes, {closes[0][0]} to {closes[-1][0]}", flush=True)

    for lo, hi in CROWDING_THRESHOLDS:
        rows = build_panel(history, closes, lo, hi)
        if not rows:
            print("no scoreable reports", flush=True)
            return 1

        baseline = {}
        print(f"\n{'=' * 78}")
        print(f"Crowding thresholds: {lo}th / {hi}th percentile")
        print(f"Scoreable reports: {len(rows)}, "
              f"{rows[0]['report_date']} to {rows[-1]['report_date']}")
        print("=" * 78)
        print("\nBaseline — every report, whatever the signals said")
        print(f"  {'horizon':<9}{'n':>5}{'eff n':>7}{'mean %':>9}{'median %':>10}{'hit %':>8}")
        for weeks in HORIZONS:
            vals = [r[f"fwd{weeks}"] for r in rows if r[f"fwd{weeks}"] is not None]
            n, mean, median, hit = _stats(vals)
            baseline[weeks] = mean
            eff = max(1, round(n / weeks))
            print(f"  {str(weeks) + 'w':<9}{n:>5}{eff:>7}{mean:>9.2f}{median:>10.2f}{hit:>8.1f}")

        report_signal(rows, "s1", "1. Spec momentum (Δ MM net, 4 reports)", baseline)
        report_signal(rows, "s2", "2. Spec crowding (MM net %OI vs 52w, contrarian)", baseline)
        report_signal(rows, "s3", "3. Short-leg flow (Δ MM short, 4 reports)", baseline)
        report_signal(rows, "s4", "4. Participation (OI confirms price)", baseline)
        report_score(rows, baseline)
        if (lo, hi) == CROWDING_THRESHOLDS[1]:
            correlations(rows)

    print("\nRead the 'vs base' column, not 'mean': gold rose across the whole")
    print("sample, so every bucket is positive on raw return.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
