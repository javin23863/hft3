"""
Tests for prop-cohort feature slots 31-34 in MBOFeatureExtractor.

Verifies that the four previously-zero slots now carry real signal:
  31 - CUTOFF_PRESSURE_SCORE
  32 - PROP_REENTRY_SCORE
  33 - NEWS_RESTRICTION_FLATTEN_SCORE
  34 - MAX_CONTRACT_TRADE_IMBALANCE

Test mandate:
  - Empty/no-trade window  → all four slots exactly 0.0.
  - Targeted synthetic streams → each slot is non-zero with the correct sign.
  - Distinctness           → slot 34 diverges from slot 0 on block-vs-noise flow.
  - Window reset           → accumulators clear so the second window starts fresh.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# PYTHONPATH bootstrap — makes this runnable from any working directory
# without relying on an installed package.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from features_engine.src.features.feature_index import FeatureIndex
from features_engine.src.features.mbo_features import MBOEvent, MBOFeatureExtractor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_TS = 1_000_000_000  # 1 second in nanoseconds
_TICK = 0.25
_WINDOW_NS = 1_000_000_000  # 1-second rolling window (extractor default)


def _make_extractor() -> MBOFeatureExtractor:
    return MBOFeatureExtractor(tick_size=_TICK, rolling_window_ns=_WINDOW_NS)


def _trade(ts_ns: int, order_id: int, side: str, price: float, size: int) -> MBOEvent:
    """Convenience: create a TRADE event.  side='A' means buy-aggressor (lifts the ask)."""
    return MBOEvent(
        timestamp_ns=ts_ns,
        order_id=order_id,
        action="TRADE",
        side=side,
        price=price,
        size=size,
    )


def _add(ts_ns: int, order_id: int, side: str, price: float, size: int) -> MBOEvent:
    return MBOEvent(
        timestamp_ns=ts_ns,
        order_id=order_id,
        action="ADD",
        side=side,
        price=price,
        size=size,
    )


def _cancel(ts_ns: int, order_id: int, side: str, price: float, size: int) -> MBOEvent:
    return MBOEvent(
        timestamp_ns=ts_ns,
        order_id=order_id,
        action="CANCEL",
        side=side,
        price=price,
        size=size,
    )


def _run_events(extractor: MBOFeatureExtractor, events: list[MBOEvent]) -> np.ndarray:
    """Feed all events; return the feature vector from the last event."""
    vec = None
    for ev in events:
        vec = extractor.process_event(ev)
    assert vec is not None, "events list must not be empty"
    return vec.copy()


# ---------------------------------------------------------------------------
# Suite 1: Empty / no-trade window → all four slots exactly 0.0
# ---------------------------------------------------------------------------

class TestEmptyWindow:
    """No trade events → zero signal on all four new slots."""

    def test_all_four_slots_zero_on_add_only_stream(self):
        """Only ADD events: no trades, so slots 31-34 must all be exactly 0.0."""
        ext = _make_extractor()
        events = [
            _add(_BASE_TS + i * 1_000_000, i + 1, "B", 4500.0, 10)
            for i in range(10)
        ]
        vec = _run_events(ext, events)
        assert vec[FeatureIndex.CUTOFF_PRESSURE_SCORE] == 0.0
        assert vec[FeatureIndex.PROP_REENTRY_SCORE] == 0.0
        assert vec[FeatureIndex.NEWS_RESTRICTION_FLATTEN_SCORE] == 0.0
        assert vec[FeatureIndex.MAX_CONTRACT_TRADE_IMBALANCE] == 0.0

    def test_all_four_slots_zero_on_cancel_only_stream(self):
        """Only CANCEL events: no trades, so slots 31-34 must all be exactly 0.0."""
        ext = _make_extractor()
        # Need at least one prior ADD so the order_map has an entry to cancel.
        events = [_add(_BASE_TS, 1, "B", 4500.0, 20)]
        events += [
            _cancel(_BASE_TS + i * 1_000_000, 1, "B", 4500.0, 5)
            for i in range(1, 4)
        ]
        vec = _run_events(ext, events)
        assert vec[FeatureIndex.CUTOFF_PRESSURE_SCORE] == 0.0
        assert vec[FeatureIndex.PROP_REENTRY_SCORE] == 0.0
        assert vec[FeatureIndex.NEWS_RESTRICTION_FLATTEN_SCORE] == 0.0
        assert vec[FeatureIndex.MAX_CONTRACT_TRADE_IMBALANCE] == 0.0


# ---------------------------------------------------------------------------
# Suite 2: Slot 34 — MAX_CONTRACT_TRADE_IMBALANCE
# ---------------------------------------------------------------------------

class TestMaxContractTradeImbalance:
    """Size-squared-weighted imbalance: big prints dominate."""

    def test_positive_on_pure_buy_aggressor_trades(self):
        """All trades are buy-aggressor → slot 34 = +1.0."""
        ext = _make_extractor()
        events = [
            _trade(_BASE_TS + i * 1_000_000, i + 1, "A", 4500.0, 10)
            for i in range(5)
        ]
        vec = _run_events(ext, events)
        assert vec[FeatureIndex.MAX_CONTRACT_TRADE_IMBALANCE] == pytest.approx(1.0)

    def test_negative_on_pure_sell_aggressor_trades(self):
        """All trades are sell-aggressor (side='B') → slot 34 = -1.0."""
        ext = _make_extractor()
        events = [
            _trade(_BASE_TS + i * 1_000_000, i + 1, "B", 4500.0, 10)
            for i in range(5)
        ]
        vec = _run_events(ext, events)
        assert vec[FeatureIndex.MAX_CONTRACT_TRADE_IMBALANCE] == pytest.approx(-1.0)

    def test_zero_on_perfectly_balanced_equal_sizes(self):
        """Equal buy and sell trades of identical size → slot 34 = 0.0."""
        ext = _make_extractor()
        events = [
            _trade(_BASE_TS + 0, 1, "A", 4500.0, 10),
            _trade(_BASE_TS + 1_000_000, 2, "B", 4500.0, 10),
        ]
        vec = _run_events(ext, events)
        assert vec[FeatureIndex.MAX_CONTRACT_TRADE_IMBALANCE] == pytest.approx(0.0)

    def test_range_bounded_minus_one_to_one(self):
        """Slot 34 is always in [-1, 1] regardless of trade mix."""
        ext = _make_extractor()
        events = [
            _trade(_BASE_TS + 0, 1, "A", 4500.0, 100),
            _trade(_BASE_TS + 1_000_000, 2, "B", 4500.0, 30),
            _trade(_BASE_TS + 2_000_000, 3, "A", 4500.0, 5),
        ]
        vec = _run_events(ext, events)
        val = vec[FeatureIndex.MAX_CONTRACT_TRADE_IMBALANCE]
        assert -1.0 <= val <= 1.0

    def test_distinctness_from_slot0_on_block_vs_noise(self):
        """
        Distinctness proof: one huge sell among many small buys.

        Stream: nine buy-aggressor trades of size 1, then one sell-aggressor
        of size 100.

        Slot 0 (volume-weighted):  buy=9, sell=100, total=109
            → (9-100)/109 ≈ -0.835  (sell-dominated)

        Slot 34 (size^2-weighted): buy_sq=9*1=9, sell_sq=100^2=10000
            → (9 - 10000) / (9 + 10000) ≈ -0.999  (very strongly sell)

        Both slots are negative here (same sign) but their magnitudes differ
        materially, confirming independent information content.  We assert:
          abs(slot34) > abs(slot0) + 0.1  (slot 34 is more extreme)
        """
        ext = _make_extractor()
        events = [
            _trade(_BASE_TS + i * 500_000, i + 1, "A", 4500.0, 1)
            for i in range(9)
        ]
        # One large sell-aggressor (side='B') at the end
        events.append(_trade(_BASE_TS + 9 * 500_000, 100, "B", 4500.0, 100))
        vec = _run_events(ext, events)

        slot0 = vec[FeatureIndex.AGGRESSOR_VOLUME_IMBALANCE]
        slot34 = vec[FeatureIndex.MAX_CONTRACT_TRADE_IMBALANCE]

        # Both should be negative (sell-dominated in both metrics)
        assert slot0 < 0.0, f"slot0={slot0} should be negative"
        assert slot34 < 0.0, f"slot34={slot34} should be negative"

        # slot34 must be materially more extreme than slot0
        assert abs(slot34) > abs(slot0) + 0.1, (
            f"slot34={slot34:.4f} should be more extreme than slot0={slot0:.4f}"
        )


# ---------------------------------------------------------------------------
# Suite 3: Slot 32 — PROP_REENTRY_SCORE
# ---------------------------------------------------------------------------

class TestPropReentryScore:
    """Trade-rate acceleration × directional conviction."""

    def test_zero_with_no_prior_window(self):
        """First window: prev_trade_count=0, accel=(N-0)/(0+1)>0 but only if N>0.
        With no trades at all, slot 32 = 0."""
        ext = _make_extractor()
        events = [_add(_BASE_TS, 1, "B", 4500.0, 10)]
        vec = _run_events(ext, events)
        assert vec[FeatureIndex.PROP_REENTRY_SCORE] == 0.0

    def test_positive_on_accelerating_buy_flow_second_window(self):
        """
        Window 1: 2 buy-aggressor trades.
        Window 2: 10 buy-aggressor trades (acceleration = (10-2)/3 = 2.67).
        tanh(2.67) ≈ 0.99; imbalance = +1.0 → reentry score > 0.
        """
        ext = _make_extractor()
        # Window 1: 2 trades inside [0, 1s)
        w1_events = [
            _trade(_BASE_TS + i * 100_000_000, i + 1, "A", 4500.0, 10)
            for i in range(2)
        ]
        # Window 2: 10 trades inside [1s+1ns, 2s)  (ts gap > rolling_window_ns triggers reset)
        w2_start = _BASE_TS + _WINDOW_NS + 1
        w2_events = [
            _trade(w2_start + i * 10_000_000, 100 + i, "A", 4500.0, 10)
            for i in range(10)
        ]
        vec = _run_events(ext, w1_events + w2_events)

        val = vec[FeatureIndex.PROP_REENTRY_SCORE]
        assert val > 0.0, f"Expected positive reentry score, got {val}"

    def test_negative_on_accelerating_sell_flow(self):
        """Acceleration positive but directional conviction is sell → score < 0."""
        ext = _make_extractor()
        w1 = [_trade(_BASE_TS + i * 100_000_000, i + 1, "B", 4500.0, 10) for i in range(1)]
        w2_start = _BASE_TS + _WINDOW_NS + 1
        w2 = [_trade(w2_start + i * 10_000_000, 50 + i, "B", 4500.0, 10) for i in range(8)]
        vec = _run_events(ext, w1 + w2)
        val = vec[FeatureIndex.PROP_REENTRY_SCORE]
        assert val < 0.0, f"Expected negative reentry score, got {val}"

    def test_tanh_bounds_output(self):
        """Output is bounded to (-1, 1) because tanh * imbalance ∈ (-1, 1)."""
        ext = _make_extractor()
        # Large acceleration by having 0 trades in w1, 1000 in w2
        w2_start = _BASE_TS + _WINDOW_NS + 1
        # First event establishes last_ts_ns without any window-1 trades
        seed = [_add(_BASE_TS, 999, "B", 4500.0, 1)]
        trades = [
            _trade(w2_start + i * 1_000, i + 1, "A", 4500.0, 1)
            for i in range(200)
        ]
        vec = _run_events(ext, seed + trades)
        val = vec[FeatureIndex.PROP_REENTRY_SCORE]
        assert -1.0 <= val <= 1.0


# ---------------------------------------------------------------------------
# Suite 4: Slot 31 — CUTOFF_PRESSURE_SCORE
# ---------------------------------------------------------------------------

class TestCutoffPressureScore:
    """One-sided forced-exit pressure = directional persistence × intensity."""

    def test_positive_on_heavy_one_sided_buying(self):
        """Heavy buy-only flow → positive cutoff pressure."""
        ext = _make_extractor()
        events = [
            _trade(_BASE_TS + i * 1_000_000, i + 1, "A", 4500.0, 20)
            for i in range(20)
        ]
        vec = _run_events(ext, events)
        val = vec[FeatureIndex.CUTOFF_PRESSURE_SCORE]
        assert val > 0.5, f"Expected large positive cutoff pressure, got {val}"

    def test_negative_on_heavy_one_sided_selling(self):
        """Heavy sell-only flow → negative cutoff pressure."""
        ext = _make_extractor()
        events = [
            _trade(_BASE_TS + i * 1_000_000, i + 1, "B", 4500.0, 20)
            for i in range(20)
        ]
        vec = _run_events(ext, events)
        val = vec[FeatureIndex.CUTOFF_PRESSURE_SCORE]
        assert val < -0.5, f"Expected large negative cutoff pressure, got {val}"

    def test_near_zero_on_balanced_flow(self):
        """Equal buy and sell → one_sidedness ≈ 0 → cutoff pressure near 0."""
        ext = _make_extractor()
        events = []
        for i in range(10):
            events.append(_trade(_BASE_TS + i * 2_000_000, 2 * i + 1, "A", 4500.0, 10))
            events.append(_trade(_BASE_TS + i * 2_000_000 + 1, 2 * i + 2, "B", 4500.0, 10))
        vec = _run_events(ext, events)
        val = vec[FeatureIndex.CUTOFF_PRESSURE_SCORE]
        assert abs(val) < 0.05, f"Expected near-zero on balanced flow, got {val}"

    def test_bounded_range(self):
        """Slot 31 must stay in [-1, 1] because one_sidedness ∈ [0,1] and tanh ∈ (-1,1)."""
        ext = _make_extractor()
        events = [
            _trade(_BASE_TS + i * 100_000, i + 1, "A", 4500.0, 1000)
            for i in range(50)
        ]
        vec = _run_events(ext, events)
        val = vec[FeatureIndex.CUTOFF_PRESSURE_SCORE]
        assert -1.0 <= val <= 1.0

    def test_distinctness_from_slot0_at_moderate_imbalance(self):
        """
        Distinctness: moderate imbalance (slot0 non-trivial) vs thin vs heavy volume.

        Two extractors, same directional imbalance (buy/sell = 3:1) but different
        total volume.  Slot 0 is identical (same ratio).  Slot 31 must differ
        because it multiplies one_sidedness by tanh(total/100).
        """
        def _build_vec(n_buys: int, n_sells: int, size: int) -> np.ndarray:
            ext = _make_extractor()
            events = (
                [_trade(_BASE_TS + i * 1_000_000, i + 1, "A", 4500.0, size) for i in range(n_buys)]
                + [_trade(_BASE_TS + (n_buys + i) * 1_000_000, n_buys + i + 1, "B", 4500.0, size)
                   for i in range(n_sells)]
            )
            return _run_events(ext, events)

        # Same 3:1 buy/sell ratio but very thin volume vs heavier volume
        vec_thin = _build_vec(n_buys=3, n_sells=1, size=1)    # total_agg = 4
        vec_heavy = _build_vec(n_buys=30, n_sells=10, size=1)  # total_agg = 40

        s0_thin = vec_thin[FeatureIndex.AGGRESSOR_VOLUME_IMBALANCE]
        s0_heavy = vec_heavy[FeatureIndex.AGGRESSOR_VOLUME_IMBALANCE]
        s31_thin = vec_thin[FeatureIndex.CUTOFF_PRESSURE_SCORE]
        s31_heavy = vec_heavy[FeatureIndex.CUTOFF_PRESSURE_SCORE]

        # Slot 0 should be equal (same ratio)
        assert s0_thin == pytest.approx(s0_heavy, abs=1e-9)

        # Slot 31 should differ (intensity term makes heavy > thin)
        assert s31_heavy > s31_thin + 0.05, (
            f"Slot 31 thin={s31_thin:.4f} heavy={s31_heavy:.4f}: "
            "heavy flow should produce meaningfully higher pressure"
        )


# ---------------------------------------------------------------------------
# Suite 5: Slot 33 — NEWS_RESTRICTION_FLATTEN_SCORE
# ---------------------------------------------------------------------------

class TestNewsRestrictionFlattenScore:
    """Forced-flat: one-sided exits coupled with near-touch cancel pressure."""

    def _setup_near_touch_book(self) -> list[MBOEvent]:
        """
        Establish a minimal book so the extractor knows best bid/ask,
        allowing near-touch cancel detection to fire.
        bid=4499.75, ask=4500.00
        """
        return [
            _add(_BASE_TS - 2_000_000, 9001, "B", 4499.75, 50),  # best bid
            _add(_BASE_TS - 1_000_000, 9002, "A", 4500.00, 50),  # best ask
        ]

    def test_zero_with_no_cancels(self):
        """No near-touch cancels → slot 33 = 0.0 even with one-sided trades."""
        ext = _make_extractor()
        events = [
            _trade(_BASE_TS + i * 1_000_000, i + 1, "A", 4500.0, 10)
            for i in range(5)
        ]
        vec = _run_events(ext, events)
        assert vec[FeatureIndex.NEWS_RESTRICTION_FLATTEN_SCORE] == 0.0

    def test_zero_when_balanced_trades_with_cancels(self):
        """Near-touch cancels present but flow is balanced → slot 33 ≈ 0."""
        ext = _make_extractor()
        book = self._setup_near_touch_book()
        trades = []
        for i in range(5):
            trades.append(_trade(_BASE_TS + i * 2_000_000, i + 1, "A", 4500.0, 10))
            trades.append(_trade(_BASE_TS + i * 2_000_000 + 1, 100 + i, "B", 4500.0, 10))
        # Near-touch cancel on bid side (at best bid, within 3 ticks)
        cancels = [_cancel(_BASE_TS + 20_000_000, 9001, "B", 4499.75, 10)]
        vec = _run_events(ext, book + trades + cancels)
        val = vec[FeatureIndex.NEWS_RESTRICTION_FLATTEN_SCORE]
        assert abs(val) < 0.05, f"Expected near-zero with balanced flow, got {val}"

    def test_nonzero_positive_on_one_sided_buy_with_near_touch_cancel(self):
        """Buy-only trades + near-touch bid cancel → positive slot 33."""
        ext = _make_extractor()
        book = self._setup_near_touch_book()
        trades = [
            _trade(_BASE_TS + i * 1_000_000, i + 1, "A", 4500.0, 10)
            for i in range(8)
        ]
        # Cancel near the best bid (within 3 ticks = 0.75 price units of 4499.75)
        cancels = [_cancel(_BASE_TS + 10_000_000, 9001, "B", 4499.75, 20)]
        vec = _run_events(ext, book + trades + cancels)
        val = vec[FeatureIndex.NEWS_RESTRICTION_FLATTEN_SCORE]
        assert val > 0.0, f"Expected positive flatten score, got {val}"

    def test_nonzero_negative_on_one_sided_sell_with_near_touch_cancel(self):
        """Sell-only trades + near-touch ask cancel → negative slot 33."""
        ext = _make_extractor()
        # bid=4499.75, ask=4500.00
        book = self._setup_near_touch_book()
        trades = [
            _trade(_BASE_TS + i * 1_000_000, i + 1, "B", 4499.75, 10)
            for i in range(8)
        ]
        # Cancel near the best ask (within 3 ticks of 4500.00)
        cancels = [_cancel(_BASE_TS + 10_000_000, 9002, "A", 4500.00, 20)]
        vec = _run_events(ext, book + trades + cancels)
        val = vec[FeatureIndex.NEWS_RESTRICTION_FLATTEN_SCORE]
        assert val < 0.0, f"Expected negative flatten score, got {val}"

    def test_bounded_range(self):
        """Slot 33 stays in [-1, 1] at all times."""
        ext = _make_extractor()
        book = self._setup_near_touch_book()
        trades = [
            _trade(_BASE_TS + i * 500_000, i + 1, "A", 4500.0, 1000)
            for i in range(30)
        ]
        cancels = [_cancel(_BASE_TS + 20_000_000, 9001, "B", 4499.75, 50)]
        vec = _run_events(ext, book + trades + cancels)
        val = vec[FeatureIndex.NEWS_RESTRICTION_FLATTEN_SCORE]
        assert -1.0 <= val <= 1.0


# ---------------------------------------------------------------------------
# Suite 6: Window reset — accumulators clear on new window
# ---------------------------------------------------------------------------

class TestWindowReset:
    """
    Two-window test: confirm that slot values in window 2 reflect only the
    events in window 2, not the accumulated state from window 1.
    """

    def test_slot34_resets_between_windows(self):
        """
        Window 1: all sell-aggressor → slot34 < 0.
        Window 2 (after gap > rolling_window_ns): all buy-aggressor.
        After reset, slot34 must be +1.0 (pure buy in fresh window).
        """
        ext = _make_extractor()
        w1 = [
            _trade(_BASE_TS + i * 50_000_000, i + 1, "B", 4500.0, 10)
            for i in range(5)
        ]
        # Last w1 ts = _BASE_TS + 4*50_000_000; gap must be > _WINDOW_NS (1e9 ns).
        w2_start = _BASE_TS + 4 * 50_000_000 + _WINDOW_NS + 1
        w2 = [
            _trade(w2_start + i * 50_000_000, 100 + i, "A", 4500.0, 10)
            for i in range(5)
        ]
        # Run window 1 to verify sell-dominated
        for ev in w1:
            ext.process_event(ev)

        # Run window 2; last vector reflects only window-2 state
        vec = _run_events(ext, w2)
        assert vec[FeatureIndex.MAX_CONTRACT_TRADE_IMBALANCE] == pytest.approx(1.0), (
            "After window reset, slot 34 should be +1.0 (pure buy window)"
        )

    def test_slot31_resets_between_windows(self):
        """
        Window 1: heavy sell-aggressor → slot31 < 0.
        Window 2: single buy trade (very light volume, low pressure).
        After reset, slot31 must be small and positive (single buy).
        """
        ext = _make_extractor()
        w1 = [
            _trade(_BASE_TS + i * 20_000_000, i + 1, "B", 4500.0, 100)
            for i in range(10)
        ]
        # Last w1 ts = _BASE_TS + 9*20_000_000; gap must be > _WINDOW_NS (1e9 ns).
        w2_start = _BASE_TS + 9 * 20_000_000 + _WINDOW_NS + 1
        w2 = [_trade(w2_start, 200, "A", 4500.0, 1)]

        for ev in w1:
            ext.process_event(ev)
        vec = _run_events(ext, w2)

        val = vec[FeatureIndex.CUTOFF_PRESSURE_SCORE]
        # Must be positive (buy-side dominant in w2) and not carry over w1 sell pressure
        assert val > 0.0, f"After reset, slot 31 should be positive (buy only), got {val}"
        assert val < 0.5, (
            f"Single small buy should produce low pressure, got {val} — "
            "window may not have reset"
        )

    def test_slot32_uses_prev_window_count_for_acceleration(self):
        """
        Window 1: 4 trades (sets _prev_trade_count=4 on reset).
        Window 2: 12 trades → accel = (12-4)/(4+1) = 1.6, tanh(1.6) ≈ 0.921.
        Slot 32 must be positive and > what a single-window would produce.
        """
        ext = _make_extractor()
        w1 = [
            _trade(_BASE_TS + i * 100_000_000, i + 1, "A", 4500.0, 5)
            for i in range(4)
        ]
        w2_start = _BASE_TS + _WINDOW_NS + 1
        w2 = [
            _trade(w2_start + i * 10_000_000, 50 + i, "A", 4500.0, 5)
            for i in range(12)
        ]
        for ev in w1:
            ext.process_event(ev)
        vec = _run_events(ext, w2)

        val = vec[FeatureIndex.PROP_REENTRY_SCORE]
        # tanh(1.6) * 1.0 ≈ 0.921; allow generous tolerance for floating math
        assert val > 0.8, f"Expected strong re-entry score (~0.92), got {val}"

    def test_slot33_resets_near_touch_cancel_between_windows(self):
        """
        Window 1: one-sided buy + near-touch cancels → slot33 > 0.
        Window 2: only neutral ADD events, no trades or cancels → slot33 = 0.
        """
        ext = _make_extractor()
        # Establish book state
        book = [
            _add(_BASE_TS - 2_000_000, 9001, "B", 4499.75, 50),
            _add(_BASE_TS - 1_000_000, 9002, "A", 4500.00, 50),
        ]
        w1_trades = [
            _trade(_BASE_TS + i * 1_000_000, i + 1, "A", 4500.0, 10)
            for i in range(5)
        ]
        w1_cancels = [_cancel(_BASE_TS + 10_000_000, 9001, "B", 4499.75, 20)]

        for ev in book + w1_trades + w1_cancels:
            ext.process_event(ev)

        # Last w1 event at _BASE_TS+10_000_000; gap must be > _WINDOW_NS (1e9 ns).
        w2_start = _BASE_TS + 10_000_000 + _WINDOW_NS + 1
        w2 = [
            _add(w2_start + i * 1_000_000, 500 + i, "B", 4499.75, 5)
            for i in range(5)
        ]
        vec = _run_events(ext, w2)
        assert vec[FeatureIndex.NEWS_RESTRICTION_FLATTEN_SCORE] == 0.0, (
            "No trades or cancels in window 2 → slot 33 must be exactly 0"
        )
