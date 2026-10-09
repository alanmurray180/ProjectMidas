#!/usr/bin/env python3
"""Measure the gold/silver ratio signal against forward gold returns.

The Other column scores the ratio's percentile within its trailing year:
low in its range ("silver leading") +1, high ("silver lagging") −1.  That
direction is the regime reading — a broad metals bull has silver leading,
risk-off stress has it lagging — and the opposite case is just as easy to
argue: a high ratio as gold rich, or as the safe-haven bid that favours
gold.  This answers which, if either, the data supports.

Two candidates, each scored +1 / 0 / −1 from gold's perspective as the
dashboard would score it:

  1. Ratio level — percentile within the trailing 252 trading days, at a
     sweep of bands.  +1 when low in its range, −1 when high.
  2. Ratio trend — change over 4 and 13 weeks.  +1 when falling (silver
     outperforming), −1 when rising.

A bucket that does *worse* than baseline when the dashboard says +1 is
the evidence for flipping the sign, so read both ends.

The same two caveats as ``scripts/cot_backtest.py`` apply and are printed
rather than hidden:

**Drift.** Gold rose hard across the sample, so raw returns are positive
almost everywhere.  Only the excess over the all-period baseline matters.

**Overlap.** Observations are sampled weekly (every fifth trading day),
so a 13-week forward return overlaps the next twelve.  The effective
sample size is printed beside each bucket.

No lookahead: the signal on day *i* uses closes up to and including day
*i*, and the entry is the close of day *i + 1*.

Run it from the **Check data sources** workflow; Yahoo is not reachable
from every environment::

    python scripts/gsr_backtest.py
"""

from __future__ import annotations

import statistics
import sys
from datetime import date

# Trailing window for the percentile, in trading days — the dashboard's
# one-year window.
WINDOW_DAYS = 252

# Sample every fifth trading day: weekly, like the COT backtest, so the
# two sets of results are read the same way.
STEP_DAYS = 5

# Forward horizons, in weeks of five trading days.
HORIZONS = (1, 4, 13)

# Percentile bands to sweep for the level signal: (low, high).  20/80 is
# what the dashboard uses today.
LEVEL_THRESHOLDS = ((10, 90), (20, 80), (30, 70))

# Lookbacks for the trend signal, in weeks.
TREND_WEEKS = (4, 13)


def _percentile(values: list[float], value: float) -> float:
    below = sum(1 for v in values if v < value)
    equal = sum(1 for v in values if v == value)
    return (below + equal / 2) / len(values) * 100


def _sign(value: float) -> int:
    return (value > 0) - (value < 0)


def align(gold: list[dict], silver: list[dict]) -> list[tuple[date, float, float]]:
    """Pair gold and silver closes by date, oldest first, dropping gaps."""
    silver_by_date = {s["date"]: s["close"] for s in silver if s.get("close")}
    out = []
    for g in gold:
        s = silver_by_date.get(g["date"])
        if g.get("close") and s:
            out.append((g["date"], g["close"], s))
    return sorted(out)


def build_panel(
    series: list[tuple[date, float, float]], level_lo: float, level_hi: float
) -> list[dict]:
    """One row per sampled day: the signals, and the gold returns that followed.

    *series* is ``(date, gold, silver)``, oldest first.  Everything a row's
    signal uses sits at or before its own index; everything its returns use
    sits after.
    """
    ratios = [g / s for _, g, s in series]
    golds = [g for _, g, _ in series]
    longest_trend = max(TREND_WEEKS) * STEP_DAYS
    start = max(WINDOW_DAYS - 1, longest_trend)

    rows = []
    for i in range(start, len(series) - 1, STEP_DAYS):
        window = ratios[i - WINDOW_DAYS + 1 : i + 1]
        pct = _percentile(window, ratios[i])
        level = 1 if pct <= level_lo else (-1 if pct >= level_hi else 0)

        row = {"date": series[i][0], "ratio": ratios[i], "pct": pct, "level": level}
        for weeks in TREND_WEEKS:
            # Falling ratio — silver outperforming — scores +1.
            row[f"trend{weeks}"] = -_sign(ratios[i] - ratios[i - weeks * STEP_DAYS])

        entry = golds[i + 1]
        for weeks in HORIZONS:
            exit_idx = i + 1 + weeks * STEP_DAYS
            row[f"fwd{weeks}"] = (
                (golds[exit_idx] / entry - 1) * 100 if exit_idx < len(golds) else None
            )
        rows.append(row)
    return rows


def _stats(values: list[float]) -> tuple[int, float, float, float]:
    if not values:
        return 0, 0.0, 0.0, 0.0
    hit = sum(1 for v in values if v > 0) / len(values) * 100
    return len(values), statistics.mean(values), statistics.median(values), hit


def baseline(rows: list[dict]) -> dict[int, float]:
    out = {}
    print("\nBaseline — every sampled week, whatever the signals said")
    print(f"  {'horizon':<9}{'n':>5}{'eff n':>7}{'mean %':>9}{'median %':>10}{'hit %':>8}")
    for weeks in HORIZONS:
        vals = [r[f"fwd{weeks}"] for r in rows if r[f"fwd{weeks}"] is not None]
        n, mean, median, hit = _stats(vals)
        out[weeks] = mean
        eff = max(1, round(n / weeks))
        print(f"  {str(weeks) + 'w':<9}{n:>5}{eff:>7}{mean:>9.2f}{median:>10.2f}{hit:>8.1f}")
    return out


def report_signal(rows: list[dict], key: str, label: str, base: dict) -> None:
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
            eff = max(1, round(n / weeks))
            print(f"  {state:<+8}{str(weeks) + 'w':<9}{n:>5}{eff:>7}{mean:>9.2f}"
                  f"{mean - base[weeks]:>9.2f}{median:>10.2f}{hit:>8.1f}")


def main() -> int:
    from midas.clients.etf import _fetch_yahoo_chart

    print("Fetching 10 years of gold (GC=F) and silver (SI=F) closes ...", flush=True)
    series = align(
        _fetch_yahoo_chart("GC=F", range_="10y"),
        _fetch_yahoo_chart("SI=F", range_="10y"),
    )
    if len(series) < WINDOW_DAYS + max(HORIZONS) * STEP_DAYS:
        print(f"only {len(series)} paired closes; not enough to score", flush=True)
        return 1
    print(f"Paired closes: {len(series)}, {series[0][0]} to {series[-1][0]}", flush=True)

    for lo, hi in LEVEL_THRESHOLDS:
        rows = build_panel(series, lo, hi)
        print(f"\n{'=' * 78}")
        print(f"Level bands: {lo}th / {hi}th percentile of the trailing {WINDOW_DAYS} days")
        print(f"Sampled weeks: {len(rows)}, {rows[0]['date']} to {rows[-1]['date']}")
        print("=" * 78)
        base = baseline(rows)
        report_signal(rows, "level", "1. Ratio level (low in range = +1, as the dashboard scores it)", base)
        # The trend signals do not depend on the bands, so print them once.
        if (lo, hi) == LEVEL_THRESHOLDS[1]:
            for weeks in TREND_WEEKS:
                report_signal(rows, f"trend{weeks}", f"2. Ratio trend, {weeks} weeks (falling = +1)", base)

    print("\nRead 'vs base', not 'mean': gold rose across the sample, so raw")
    print("returns are positive almost everywhere.  If +1 trails base and −1")
    print("beats it, the dashboard's sign is the wrong way round.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
