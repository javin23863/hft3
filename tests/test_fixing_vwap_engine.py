"""Tests for packages/options_lane/src/fixing/vwap_engine.py (WS-3.4).

Conventions match existing test_fixing_window_study.py:
- sys.path bootstrap mirrors that file.
- All goldens hand-computed in comments.
- No live data, no NPZ, no scipy.
- Deterministic; no randomness.
"""

from __future__ import annotations

import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# sys.path bootstrap
# ---------------------------------------------------------------------------
_REPO = Path(__file__).resolve().parents[1]
_PACKAGES = _REPO / "packages"
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_PACKAGES))

from options_lane.src.fixing.vwap_engine import (
    FixingWindow,
    ReplicationSchedule,
    Slice,
    _largest_remainder,
    make_schedule,
    replication_error,
    vwap_from_trades,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _winter_expiry() -> date:
    """2025-01-17 — CST (UTC-6): 15:00 CT = 21:00 UTC."""
    return date(2025, 1, 17)


def _summer_expiry() -> date:
    """2025-06-20 — CDT (UTC-5): 15:00 CT = 20:00 UTC."""
    return date(2025, 6, 20)


# ---------------------------------------------------------------------------
# FixingWindow: DST-correct UTC bounds
# ---------------------------------------------------------------------------

class TestFixingWindowUTC:
    def test_winter_end_utc(self) -> None:
        """2025-01-17 CST: 15:00 CT → 21:00 UTC.

        Hand: CST = UTC-6, so 15:00 CT + 6h = 21:00 UTC.
        """
        fw = FixingWindow.from_expiry(_winter_expiry())
        assert fw.end_utc.hour == 21
        assert fw.end_utc.minute == 0
        assert fw.end_utc.second == 0
        assert fw.end_utc.tzinfo is not None

    def test_winter_start_utc(self) -> None:
        """2025-01-17: start = end - 30s = 20:59:30 UTC."""
        fw = FixingWindow.from_expiry(_winter_expiry())
        expected_start = fw.end_utc - timedelta(seconds=30)
        assert fw.start_utc == expected_start
        assert fw.start_utc.hour == 20
        assert fw.start_utc.minute == 59
        assert fw.start_utc.second == 30

    def test_summer_end_utc(self) -> None:
        """2025-06-20 CDT: 15:00 CT → 20:00 UTC.

        Hand: CDT = UTC-5, so 15:00 CT + 5h = 20:00 UTC.
        """
        fw = FixingWindow.from_expiry(_summer_expiry())
        assert fw.end_utc.hour == 20
        assert fw.end_utc.minute == 0
        assert fw.end_utc.second == 0

    def test_summer_start_utc(self) -> None:
        """2025-06-20: start = 19:59:30 UTC."""
        fw = FixingWindow.from_expiry(_summer_expiry())
        assert fw.start_utc.hour == 19
        assert fw.start_utc.minute == 59
        assert fw.start_utc.second == 30

    def test_winter_summer_end_differ_by_1h(self) -> None:
        """Winter and summer end_utc differ by exactly 1 hour (DST shift)."""
        fw_w = FixingWindow.from_expiry(_winter_expiry())
        fw_s = FixingWindow.from_expiry(_summer_expiry())
        # Winter is UTC+21h, summer is UTC+20h on their respective dates.
        # We compare the UTC hour directly — they differ by 1.
        assert fw_w.end_utc.hour - fw_s.end_utc.hour == 1

    def test_duration_is_30_seconds(self) -> None:
        """Window is always exactly 30 seconds."""
        for expiry in (_winter_expiry(), _summer_expiry()):
            fw = FixingWindow.from_expiry(expiry)
            assert fw.duration_seconds() == 30.0

    def test_start_before_end(self) -> None:
        for expiry in (_winter_expiry(), _summer_expiry()):
            fw = FixingWindow.from_expiry(expiry)
            assert fw.start_utc < fw.end_utc


# ---------------------------------------------------------------------------
# _largest_remainder: integer allocation
# ---------------------------------------------------------------------------

class TestLargestRemainder:
    def test_exact_division(self) -> None:
        """6 into 3 buckets → [2, 2, 2]."""
        result = _largest_remainder(6, 3)
        assert result == [2, 2, 2]
        assert sum(result) == 6

    def test_non_exact_remainder_one(self) -> None:
        """7 into 3 buckets → [3, 2, 2] (one extra goes to first bucket).

        Hand: 7/3 = 2.333. Floors: 7*1//3=2, 7*2//3=4, 7*3//3=7.
        Diffs: 2-0=2, 4-2=2, 7-4=3 → [2, 2, 3]. Sum=7. ✓
        (Bresenham distributes the +1 to the last bucket in this formulation.)
        """
        result = _largest_remainder(7, 3)
        assert sum(result) == 7
        assert len(result) == 3
        assert min(result) >= 0
        # All values within 1 of each other (near-uniform)
        assert max(result) - min(result) <= 1

    def test_sum_equals_total(self) -> None:
        """Property: sum always equals total for various inputs."""
        cases = [(10, 3), (13, 6), (1, 6), (100, 7), (5, 5), (6, 6)]
        for total, n in cases:
            result = _largest_remainder(total, n)
            assert sum(result) == total, f"Failed for total={total}, n={n}"
            assert len(result) == n

    def test_zero_total(self) -> None:
        """Zero total → all zeros."""
        result = _largest_remainder(0, 6)
        assert result == [0, 0, 0, 0, 0, 0]
        assert sum(result) == 0

    def test_total_less_than_n(self) -> None:
        """1 into 6 buckets → five zeros and one 1."""
        result = _largest_remainder(1, 6)
        assert sum(result) == 1
        assert result.count(1) == 1
        assert result.count(0) == 5

    def test_large_values_sum_correct(self) -> None:
        """Large total distributes correctly."""
        result = _largest_remainder(10000, 7)
        assert sum(result) == 10000
        assert len(result) == 7


# ---------------------------------------------------------------------------
# make_schedule: ReplicationSchedule construction
# ---------------------------------------------------------------------------

class TestMakeSchedule:
    def test_zero_target_empty_schedule(self) -> None:
        """target_qty=0 → empty slices tuple, sum invariant trivially satisfied."""
        sched = make_schedule(_summer_expiry(), target_qty=0)
        assert sched.target_qty == 0
        assert len(sched.slices) == 0
        assert sched.intent_tag == "FIXING_REPLICATION_PASSIVE"

    def test_sum_equals_target_positive(self) -> None:
        """Positive target: sum of slice quantities == target_qty."""
        sched = make_schedule(_summer_expiry(), target_qty=13, n_slices=6)
        assert sum(s.qty for s in sched.slices) == 13

    def test_sum_equals_target_negative(self) -> None:
        """Negative target: quantities are negative, sum == target_qty."""
        sched = make_schedule(_summer_expiry(), target_qty=-13, n_slices=6)
        assert sum(s.qty for s in sched.slices) == -13
        assert all(s.qty <= 0 for s in sched.slices)

    def test_negative_mirrors_positive(self) -> None:
        """abs(qty) distribution for -N mirrors that for +N."""
        sched_pos = make_schedule(_summer_expiry(), target_qty=13, n_slices=6)
        sched_neg = make_schedule(_summer_expiry(), target_qty=-13, n_slices=6)
        pos_abs = [s.qty for s in sched_pos.slices]
        neg_abs = [-s.qty for s in sched_neg.slices]
        assert pos_abs == neg_abs

    def test_default_n_slices_is_6(self) -> None:
        """Default n_slices produces 6 buckets."""
        sched = make_schedule(_summer_expiry(), target_qty=10)
        assert sched.n_slices == 6
        assert len(sched.slices) == 6

    def test_bucket_times_cover_window(self) -> None:
        """First slice starts at window.start_utc, last ends at window.end_utc."""
        sched = make_schedule(_summer_expiry(), target_qty=6, n_slices=6)
        assert sched.slices[0].bucket_start_utc == sched.window.start_utc
        assert sched.slices[-1].bucket_end_utc == sched.window.end_utc

    def test_bucket_times_contiguous(self) -> None:
        """Each bucket starts where the previous one ends."""
        sched = make_schedule(_summer_expiry(), target_qty=6, n_slices=6)
        for i in range(1, len(sched.slices)):
            assert sched.slices[i].bucket_start_utc == sched.slices[i - 1].bucket_end_utc

    def test_near_uniform_distribution(self) -> None:
        """Slice quantities are within 1 of each other (near-uniform v0 profile).

        6 slices, target=13: 13/6 = 2.166. Expected: five 2s and one 3. Max-min = 1.
        """
        sched = make_schedule(_summer_expiry(), target_qty=13, n_slices=6)
        qtys = [s.qty for s in sched.slices]
        assert max(qtys) - min(qtys) <= 1

    def test_exact_division_all_equal(self) -> None:
        """12 contracts into 6 slices → each slice gets exactly 2."""
        sched = make_schedule(_summer_expiry(), target_qty=12, n_slices=6)
        assert all(s.qty == 2 for s in sched.slices)

    def test_intent_tag_constant(self) -> None:
        """intent_tag is always FIXING_REPLICATION_PASSIVE."""
        for qty in (0, 1, -1, 100, -100):
            sched = make_schedule(_summer_expiry(), target_qty=qty)
            assert sched.intent_tag == "FIXING_REPLICATION_PASSIVE"

    def test_winter_expiry_schedule(self) -> None:
        """Schedule for a winter expiry uses correct UTC window."""
        sched = make_schedule(_winter_expiry(), target_qty=6, n_slices=6)
        # start_utc should be 20:59:30 UTC (winter, CST)
        assert sched.window.start_utc.hour == 20
        assert sched.window.start_utc.minute == 59
        assert sched.window.start_utc.second == 30
        assert sum(s.qty for s in sched.slices) == 6

    def test_invalid_n_slices_raises(self) -> None:
        """n_slices < 1 raises ValueError."""
        with pytest.raises(ValueError):
            make_schedule(_summer_expiry(), target_qty=10, n_slices=0)

    def test_n_slices_one(self) -> None:
        """n_slices=1 yields a single slice with all the quantity."""
        sched = make_schedule(_summer_expiry(), target_qty=7, n_slices=1)
        assert len(sched.slices) == 1
        assert sched.slices[0].qty == 7

    def test_schedule_target_qty_stored(self) -> None:
        """ReplicationSchedule.target_qty matches what was passed."""
        for qty in (0, 5, -5, 1, -1):
            sched = make_schedule(_summer_expiry(), target_qty=qty)
            assert sched.target_qty == qty


# ---------------------------------------------------------------------------
# vwap_from_trades
# ---------------------------------------------------------------------------

class TestVwapFromTrades:
    def test_single_trade(self) -> None:
        """Single trade → VWAP equals that trade's price.

        Hand: (1 * 5000) / 1 = 5000.
        """
        result = vwap_from_trades([(1, 5000.0)])
        assert result == 5000.0

    def test_two_equal_size_trades(self) -> None:
        """Two equal-qty trades → arithmetic average.

        Hand: (1*5000 + 1*5010) / 2 = 5005.
        """
        result = vwap_from_trades([(1, 5000.0), (1, 5010.0)])
        assert abs(result - 5005.0) < 1e-9

    def test_two_unequal_size_trades(self) -> None:
        """Two unequal-qty trades → weighted average.

        Hand: (100*5000 + 200*5010) / 300 = (500000 + 1002000) / 300
                                           = 1502000 / 300
                                           = 5006.666...
        """
        result = vwap_from_trades([(100, 5000.0), (200, 5010.0)])
        expected = (100 * 5000 + 200 * 5010) / 300
        assert abs(result - expected) < 1e-9

    def test_empty_trades_returns_nan(self) -> None:
        """No trades → nan."""
        result = vwap_from_trades([])
        assert math.isnan(result)

    def test_negative_qty_treated_as_absolute(self) -> None:
        """Negative (sell) quantities use abs value; same result as positive.

        Hand: same weighting as positive qty.
        """
        result_pos = vwap_from_trades([(100, 5000.0), (200, 5010.0)])
        result_neg = vwap_from_trades([(-100, 5000.0), (-200, 5010.0)])
        assert abs(result_pos - result_neg) < 1e-9

    def test_consistent_with_fixing_window_study(self) -> None:
        """Spot-check: same 3-trade set used in test_fixing_window_study.

        fix-window trades in that test: 2@5000 + 1@5002.
        Hand: (2*5000 + 1*5002) / 3 = (10000 + 5002) / 3 = 15002 / 3 = 5000.666...
        """
        result = vwap_from_trades([(2, 5000.0), (1, 5002.0)])
        expected = (2 * 5000 + 1 * 5002) / 3
        assert abs(result - expected) < 1e-9

    def test_zero_qty_trade_ignored(self) -> None:
        """A fill with qty=0 contributes nothing.

        Hand: only the non-zero fill counts.
        """
        result = vwap_from_trades([(0, 9999.0), (2, 5000.0)])
        assert abs(result - 5000.0) < 1e-9

    def test_all_zero_qty_returns_nan(self) -> None:
        """If all qtys are zero → nan (no volume)."""
        result = vwap_from_trades([(0, 5000.0), (0, 5010.0)])
        assert math.isnan(result)


# ---------------------------------------------------------------------------
# replication_error
# ---------------------------------------------------------------------------

class TestReplicationError:
    def test_zero_slippage(self) -> None:
        """Achieved VWAP == fixing VWAP → error = 0 ticks.

        Hand: (5000 - 5000) / 0.25 = 0.
        """
        fills = [(1, 5000.0), (1, 5000.0)]
        err = replication_error(fills, fixing_vwap=5000.0, tick_size=0.25)
        assert err == 0.0

    def test_positive_slippage(self) -> None:
        """Filled above fixing → positive error.

        Setup: 2 fills at 5001, fixing = 5000, tick = 0.25.
        Hand achieved_vwap = 5001.
        error = (5001 - 5000) / 0.25 = 4.0 ticks.
        """
        fills = [(1, 5001.0), (1, 5001.0)]
        err = replication_error(fills, fixing_vwap=5000.0, tick_size=0.25)
        assert abs(err - 4.0) < 1e-9

    def test_negative_slippage(self) -> None:
        """Filled below fixing → negative error.

        Setup: 2 fills at 4999, fixing = 5000, tick = 0.25.
        Hand achieved_vwap = 4999.
        error = (4999 - 5000) / 0.25 = -4.0 ticks.
        """
        fills = [(1, 4999.0), (1, 4999.0)]
        err = replication_error(fills, fixing_vwap=5000.0, tick_size=0.25)
        assert abs(err - (-4.0)) < 1e-9

    def test_weighted_fills_golden(self) -> None:
        """Mixed fills against known fixing VWAP — hand-computed golden.

        fills: 2@5000, 1@5006
        achieved_vwap = (2*5000 + 1*5006) / 3 = (10000 + 5006) / 3 = 15006 / 3 = 5002.0
        fixing_vwap = 5001.0
        error = (5002.0 - 5001.0) / 0.25 = 1.0 / 0.25 = 4.0 ticks
        """
        fills = [(2, 5000.0), (1, 5006.0)]
        err = replication_error(fills, fixing_vwap=5001.0, tick_size=0.25)
        # achieved = (2*5000 + 1*5006) / 3 = 5002.0
        assert abs(err - 4.0) < 1e-9

    def test_empty_fills_returns_nan(self) -> None:
        """No fills → nan."""
        err = replication_error([], fixing_vwap=5000.0)
        assert math.isnan(err)

    def test_nan_fixing_vwap_returns_nan(self) -> None:
        """nan fixing_vwap → nan error."""
        fills = [(1, 5000.0)]
        err = replication_error(fills, fixing_vwap=float("nan"))
        assert math.isnan(err)

    def test_custom_tick_size(self) -> None:
        """Non-default tick size scales error correctly.

        Setup: achieved=5001, fixing=5000, tick=0.10.
        Hand: (5001 - 5000) / 0.10 = 10.0 ticks.
        """
        fills = [(1, 5001.0)]
        err = replication_error(fills, fixing_vwap=5000.0, tick_size=0.10)
        assert abs(err - 10.0) < 1e-9

    def test_zero_tick_size_raises(self) -> None:
        """tick_size=0 → ValueError (division by zero prevented explicitly)."""
        with pytest.raises(ValueError):
            replication_error([(1, 5000.0)], fixing_vwap=5000.0, tick_size=0.0)


# ---------------------------------------------------------------------------
# Integration: schedule + tracking round-trip
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_perfect_replication_zero_error(self) -> None:
        """If fills equal fixing VWAP exactly, error is 0.

        A perfectly executed VWAP schedule should produce ~0 slippage.
        """
        fixing_vwap = 5000.0
        # Simulate fills at exactly the fixing VWAP price
        sched = make_schedule(_summer_expiry(), target_qty=6, n_slices=6)
        fills = [(s.qty, fixing_vwap) for s in sched.slices]
        err = replication_error(fills, fixing_vwap=fixing_vwap)
        assert abs(err) < 1e-9

    def test_schedule_qty_sum_matches_replication_volume(self) -> None:
        """Total executed volume from schedule matches target_qty."""
        target = 13
        sched = make_schedule(_summer_expiry(), target_qty=target, n_slices=6)
        total_executed = sum(s.qty for s in sched.slices)
        assert total_executed == target

    def test_negative_target_vwap_error_sign(self) -> None:
        """Sell-side: filled above fixing → positive error (same sign convention).

        Fills: 3 units at 5001, fixing = 5000, tick = 0.25.
        achieved = 5001, error = (5001 - 5000) / 0.25 = 4 ticks.
        """
        fills = [(-3, 5001.0)]  # negative qty (sell), price above fixing
        err = replication_error(fills, fixing_vwap=5000.0)
        assert abs(err - 4.0) < 1e-9
