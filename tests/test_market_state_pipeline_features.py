"""
Tests for market_state_pipeline.py feature correctness:
  - VWAP from trade events
  - Session high/low breaking (IS_BREAKING_SESSION_LEVEL)
  - Round-number distance (DISTANCE_TO_ROUND_NUMBER)
  - SPREAD_STRESS_ELEVATED (renamed from IS_BREAKING_LEVEL)
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from features_engine.src.features.feature_index import FeatureIndex
from features_engine.src.features.mbo_features import MBOEvent
from features_engine.src.pipeline.market_state_pipeline import MarketStatePipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add(ts: int, order_id: int, side: str, price: float, size: int) -> MBOEvent:
    return MBOEvent(ts, order_id, "ADD", side, price, size)


def _trade(ts: int, order_id: int, side: str, price: float, size: int) -> MBOEvent:
    return MBOEvent(ts, order_id, "TRADE", side, price, size)


def _build_book(pipe: MarketStatePipeline, bid: float, ask: float, ts: int = 1000) -> None:
    """Place a single order on each side to establish BBO."""
    pipe.process_event(_add(ts, 1, "B", bid, 10))
    pipe.process_event(_add(ts + 1, 2, "A", ask, 10))


# ---------------------------------------------------------------------------
# VWAP tests
# ---------------------------------------------------------------------------

class TestVWAP:
    def test_no_trades_vwap_is_zero(self):
        """Before any TRADE event, DISTANCE_TO_VWAP must be 0."""
        pipe = MarketStatePipeline(tick_size=0.25)
        _build_book(pipe, 5000.0, 5000.25, ts=1000)
        state = pipe.process_event(_add(2000, 3, "B", 4999.75, 5))
        assert state.feature_vector[FeatureIndex.DISTANCE_TO_VWAP] == 0.0

    def test_single_trade_vwap_equals_trade_price(self):
        """After a single trade at price P, VWAP = P and distance = (mid - P)/tick."""
        pipe = MarketStatePipeline(tick_size=0.25)
        _build_book(pipe, 5000.0, 5000.25, ts=1000)
        # Trade at 5000.25 (ask side), size 4
        state = pipe.process_event(_trade(2000, 99, "A", 5000.25, 4))
        vwap = 5000.25
        mid = (5000.0 + 5000.25) / 2.0  # 5000.125
        expected = (mid - vwap) / 0.25
        assert abs(state.feature_vector[FeatureIndex.DISTANCE_TO_VWAP] - expected) < 1e-10

    def test_multiple_trades_vwap_correct(self):
        """VWAP is price-weighted average of trade prices."""
        pipe = MarketStatePipeline(tick_size=0.25)
        _build_book(pipe, 5000.0, 5000.25, ts=1000)
        # Trade 1: price=5000.25, size=4
        pipe.process_event(_trade(2000, 10, "A", 5000.25, 4))
        # Trade 2: price=5000.50, size=6
        state = pipe.process_event(_trade(3000, 11, "A", 5000.50, 6))
        vwap = (5000.25 * 4 + 5000.50 * 6) / (4 + 6)
        mid = (5000.0 + 5000.25) / 2.0  # bid/ask still 5000/5000.25 after trades
        expected = (mid - vwap) / 0.25
        assert abs(state.feature_vector[FeatureIndex.DISTANCE_TO_VWAP] - expected) < 1e-10

    def test_vwap_signed_positive_when_mid_above(self):
        """DISTANCE_TO_VWAP is positive when mid > VWAP."""
        pipe = MarketStatePipeline(tick_size=0.25)
        _build_book(pipe, 5000.0, 5000.25, ts=1000)
        # Trade at a low price so VWAP < mid
        state = pipe.process_event(_trade(2000, 5, "B", 4999.0, 100))
        dist = state.feature_vector[FeatureIndex.DISTANCE_TO_VWAP]
        mid = (5000.0 + 5000.25) / 2.0
        # VWAP = 4999.0, mid = 5000.125, mid > VWAP → positive
        assert dist > 0

    def test_vwap_signed_negative_when_mid_below(self):
        """DISTANCE_TO_VWAP is negative when mid < VWAP."""
        pipe = MarketStatePipeline(tick_size=0.25)
        _build_book(pipe, 5000.0, 5000.25, ts=1000)
        # Trade at a high price so VWAP > mid
        state = pipe.process_event(_trade(2000, 5, "A", 5010.0, 100))
        dist = state.feature_vector[FeatureIndex.DISTANCE_TO_VWAP]
        assert dist < 0

    def test_reset_session_clears_vwap(self):
        """reset_session() clears the VWAP accumulator."""
        pipe = MarketStatePipeline(tick_size=0.25)
        _build_book(pipe, 5000.0, 5000.25, ts=1000)
        pipe.process_event(_trade(2000, 5, "A", 5005.0, 10))
        # Confirm VWAP is non-zero before reset
        state = pipe.process_event(_add(3000, 6, "B", 4999.75, 1))
        assert state.feature_vector[FeatureIndex.DISTANCE_TO_VWAP] != 0.0

        pipe.reset_session()
        # After reset, next event with no trade should give 0 again
        state2 = pipe.process_event(_add(4000, 7, "A", 5000.50, 1))
        assert state2.feature_vector[FeatureIndex.DISTANCE_TO_VWAP] == 0.0


# ---------------------------------------------------------------------------
# Session high/low tests
# ---------------------------------------------------------------------------

class TestSessionHighLow:
    def test_first_event_no_break(self):
        """First event initialises session range without triggering a break."""
        pipe = MarketStatePipeline(tick_size=0.25)
        _build_book(pipe, 5000.0, 5000.25, ts=1000)
        state = pipe.process_event(_add(2000, 3, "B", 4999.75, 1))
        assert state.feature_vector[FeatureIndex.IS_BREAKING_SESSION_LEVEL] == 0.0

    def test_new_session_high_gives_plus_one(self):
        """Mid above prior session high → IS_BREAKING_SESSION_LEVEL = +1."""
        pipe = MarketStatePipeline(tick_size=0.25)
        # Establish initial BBO at 5000/5000.25, mid=5000.125
        pipe.process_event(_add(1000, 1, "B", 5000.0, 10))
        pipe.process_event(_add(1001, 2, "A", 5000.25, 10))
        # Force an initialisation by processing one more non-breaking event
        pipe.process_event(_add(1002, 3, "B", 4999.75, 5))
        # Now move the ask up to create a higher mid
        pipe.process_event(_add(1003, 4, "A", 5001.25, 10))
        # Cancel the old ask so BBO moves up
        pipe.process_event(MBOEvent(1004, 2, "CANCEL", "A", 5000.25, 10))
        state = pipe.process_event(_add(1005, 5, "B", 5000.50, 5))
        # mid is now (5000.50 + 5001.25)/2 = 5000.875 > 5000.125
        assert state.feature_vector[FeatureIndex.IS_BREAKING_SESSION_LEVEL] == 1.0

    def test_new_session_low_gives_minus_one(self):
        """Mid below prior session low → IS_BREAKING_SESSION_LEVEL = -1.

        Strategy: initialise the book at a high mid, then cancel the best bid
        and add a new bid at a lower price.  When the resulting mid drops below
        the session low, the feature must return -1.
        """
        pipe = MarketStatePipeline(tick_size=0.25)
        # Establish initial BBO at 5010.0 / 5010.25 → mid = 5010.125 (init)
        pipe.process_event(_add(1000, 10, "B", 5010.0, 10))
        pipe.process_event(_add(1001, 20, "A", 5010.25, 10))
        # Cancel the best bid; BBO becomes one-sided (mid = 0) — no break
        pipe.process_event(MBOEvent(1002, 10, "CANCEL", "B", 5010.0, 10))
        # Add a new bid at a much lower price → mid = (5000.0 + 5010.25)/2 = 5005.125
        # This is below the session low (5010.125) → IS_BREAKING_SESSION_LEVEL = -1
        state = pipe.process_event(_add(1003, 30, "B", 5000.0, 10))
        assert state.feature_vector[FeatureIndex.IS_BREAKING_SESSION_LEVEL] == -1.0
        assert pipe._session_low < pipe._session_high

    def test_inside_range_gives_zero(self):
        """Mid stays inside the established range → 0."""
        pipe = MarketStatePipeline(tick_size=0.25)
        pipe.process_event(_add(1000, 1, "B", 5000.0, 10))
        pipe.process_event(_add(1001, 2, "A", 5000.25, 10))
        # Two events to init, then one that stays inside
        pipe.process_event(_add(1002, 3, "B", 4999.75, 5))
        state = pipe.process_event(_add(1003, 4, "B", 4999.50, 3))
        # mid still 5000.125, within range [5000.125, 5000.125]... after init
        # no break yet; second event also doesn't break
        assert state.feature_vector[FeatureIndex.IS_BREAKING_SESSION_LEVEL] == 0.0

    def test_reset_session_resets_high_low(self):
        """reset_session() resets the range so the next mid re-initialises."""
        pipe = MarketStatePipeline(tick_size=0.25)
        pipe.process_event(_add(1000, 1, "B", 5000.0, 10))
        pipe.process_event(_add(1001, 2, "A", 5000.25, 10))
        pipe.process_event(_add(1002, 3, "B", 4999.75, 5))
        # Force a high break first
        pipe.process_event(_add(1003, 4, "A", 5002.25, 10))
        pipe.process_event(MBOEvent(1004, 2, "CANCEL", "A", 5000.25, 10))
        pipe.process_event(_add(1005, 5, "B", 5001.0, 5))
        pipe.reset_session()
        # After reset the next event should NOT break (it re-initialises)
        state = pipe.process_event(_add(1006, 6, "B", 5001.25, 3))
        assert state.feature_vector[FeatureIndex.IS_BREAKING_SESSION_LEVEL] == 0.0


# ---------------------------------------------------------------------------
# Round-number distance tests
# ---------------------------------------------------------------------------

class TestRoundNumberDistance:
    def test_at_round_level_is_zero(self):
        """When mid is exactly at a round-number level, distance is 0."""
        # mid = 5000.0 → exactly a 10-pt multiple → distance = 0 ticks
        pipe = MarketStatePipeline(tick_size=0.25, round_number_increment=10.0)
        pipe.process_event(_add(1000, 1, "B", 4999.875, 10))
        pipe.process_event(_add(1001, 2, "A", 5000.125, 10))
        state = pipe.process_event(_add(1002, 3, "B", 4999.75, 5))
        # mid = (4999.875 + 5000.125) / 2 = 5000.0; remainder = 0
        assert state.feature_vector[FeatureIndex.DISTANCE_TO_ROUND_NUMBER] == pytest.approx(0.0, abs=1e-10)

    def test_halfway_between_round_levels(self):
        """Mid halfway between two 10-pt levels should give 5 pts = 20 ticks."""
        # mid = 5005.0 → halfway between 5000 and 5010 → dist = 5 pts = 20 ticks
        pipe = MarketStatePipeline(tick_size=0.25, round_number_increment=10.0)
        pipe.process_event(_add(1000, 1, "B", 5004.875, 10))
        pipe.process_event(_add(1001, 2, "A", 5005.125, 10))
        state = pipe.process_event(_add(1002, 3, "B", 4999.75, 5))
        dist_ticks = state.feature_vector[FeatureIndex.DISTANCE_TO_ROUND_NUMBER]
        # dist_pts = min(5.0, 5.0) = 5.0; dist_ticks = 5.0 / 0.25 = 20
        assert dist_ticks == pytest.approx(20.0, abs=1e-10)

    def test_one_tick_below_round_level(self):
        """Mid one tick (0.25 pt) below a 10-pt level → distance = 1 tick."""
        # mid = 4999.75 → remainder = 9.75; dist = min(9.75, 0.25) = 0.25 pt = 1 tick
        pipe = MarketStatePipeline(tick_size=0.25, round_number_increment=10.0)
        pipe.process_event(_add(1000, 1, "B", 4999.625, 10))
        pipe.process_event(_add(1001, 2, "A", 4999.875, 10))
        state = pipe.process_event(_add(1002, 3, "B", 4999.50, 5))
        dist_ticks = state.feature_vector[FeatureIndex.DISTANCE_TO_ROUND_NUMBER]
        assert dist_ticks == pytest.approx(1.0, abs=1e-10)

    def test_custom_increment(self):
        """Custom round_number_increment is respected."""
        # With 5-pt increments, mid=5002.5 is at a level (5000+2.5? no: 2.5 pt increment)
        # mid=5002.5: remainder = 2502.5 % 5.0 = 2.5; dist = min(2.5, 2.5) = 2.5 pts = 10 ticks
        pipe = MarketStatePipeline(tick_size=0.25, round_number_increment=5.0)
        pipe.process_event(_add(1000, 1, "B", 5002.375, 10))
        pipe.process_event(_add(1001, 2, "A", 5002.625, 10))
        state = pipe.process_event(_add(1002, 3, "B", 5000.0, 5))
        dist_ticks = state.feature_vector[FeatureIndex.DISTANCE_TO_ROUND_NUMBER]
        # mid = 5002.5; remainder = 5002.5 % 5.0 = 2.5; dist_pts = 2.5; ticks = 10
        assert dist_ticks == pytest.approx(10.0, abs=1e-10)

    def test_disabled_increment_emits_zero(self):
        """round_number_increment=None emits 0."""
        pipe = MarketStatePipeline(tick_size=0.25, round_number_increment=None)
        pipe.process_event(_add(1000, 1, "B", 5000.0, 10))
        pipe.process_event(_add(1001, 2, "A", 5000.25, 10))
        state = pipe.process_event(_add(1002, 3, "B", 4999.75, 5))
        assert state.feature_vector[FeatureIndex.DISTANCE_TO_ROUND_NUMBER] == 0.0


# ---------------------------------------------------------------------------
# SPREAD_STRESS_ELEVATED (renamed from IS_BREAKING_LEVEL) tests
# ---------------------------------------------------------------------------

class TestSpreadStressElevated:
    def test_index_27_is_spread_stress_elevated(self):
        """FeatureIndex.SPREAD_STRESS_ELEVATED is at index 27."""
        assert int(FeatureIndex.SPREAD_STRESS_ELEVATED) == 27

    def test_old_name_gone(self):
        """IS_BREAKING_LEVEL no longer exists on FeatureIndex."""
        assert not hasattr(FeatureIndex, "IS_BREAKING_LEVEL")

    def test_feature_name_map_updated(self):
        """The FEATURE_NAME_TO_INDEX map uses the new name."""
        from features_engine.src.features.feature_index import FEATURE_NAME_TO_INDEX
        assert "spread_stress_elevated" in FEATURE_NAME_TO_INDEX
        assert "is_breaking_level" not in FEATURE_NAME_TO_INDEX
        assert FEATURE_NAME_TO_INDEX["spread_stress_elevated"] == 27
