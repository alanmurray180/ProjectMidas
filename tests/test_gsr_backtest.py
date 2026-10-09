"""Tests for the gold/silver ratio backtest: alignment, scoring, no lookahead."""

from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "gsr_backtest", Path(__file__).resolve().parents[1] / "scripts" / "gsr_backtest.py"
)
bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bt)


def _series(ratios: list[float], gold: list[float] | None = None):
    start = date(2020, 1, 1)
    gold = gold or [2000.0] * len(ratios)
    return [(start + timedelta(days=i), g, g / r) for i, (g, r) in enumerate(zip(gold, ratios))]


def test_align_pairs_by_date_and_drops_gaps():
    d = date(2025, 1, 1)
    gold = [{"date": d, "close": 2000.0}, {"date": d + timedelta(1), "close": 2010.0}]
    silver = [{"date": d + timedelta(1), "close": 25.0}]

    assert bt.align(gold, silver) == [(d + timedelta(1), 2010.0, 25.0)]


def test_level_scores_low_ratio_plus_one_and_high_minus_one():
    n = bt.WINDOW_DAYS + 200
    rising = _series([50.0 + i * 0.1 for i in range(n)])
    falling = _series([150.0 - i * 0.1 for i in range(n)])

    # A ratio at the top of its range every day is "silver lagging".
    assert {r["level"] for r in bt.build_panel(rising, 20, 80)} == {-1}
    assert {r["trend4"] for r in bt.build_panel(rising, 20, 80)} == {-1}
    # At the bottom of its range it is "silver leading".
    assert {r["level"] for r in bt.build_panel(falling, 20, 80)} == {1}
    assert {r["trend13"] for r in bt.build_panel(falling, 20, 80)} == {1}


def test_forward_return_starts_the_day_after_the_signal():
    n = bt.WINDOW_DAYS + 200
    gold = [1000.0 + i for i in range(n)]
    rows = bt.build_panel(_series([80.0] * n, gold), 20, 80)
    first = rows[0]
    i = next(idx for idx, (d, _, _) in enumerate(_series([80.0] * n, gold)) if d == first["date"])

    entry, exit_ = gold[i + 1], gold[i + 1 + bt.STEP_DAYS]
    assert first["fwd1"] == pytest.approx((exit_ / entry - 1) * 100)


def test_signal_ignores_the_future():
    """Changing prices after a row must not change that row's signals."""
    n = bt.WINDOW_DAYS + 200
    base = [80.0 + (i % 17) for i in range(n)]
    shocked = base[:300] + [200.0] * (n - 300)

    before = {r["date"]: r for r in bt.build_panel(_series(base), 20, 80)}
    after = {r["date"]: r for r in bt.build_panel(_series(shocked), 20, 80)}
    for day, row in before.items():
        idx = (day - date(2020, 1, 1)).days
        if idx < 300:
            for key in ("pct", "level", "trend4", "trend13"):
                assert after[day][key] == row[key]


def test_rows_near_the_end_carry_no_unknowable_returns():
    n = bt.WINDOW_DAYS + 100
    rows = bt.build_panel(_series([80.0] * n), 20, 80)

    assert rows[-1]["fwd13"] is None
    assert all(r["fwd1"] is not None for r in rows[:-2])
