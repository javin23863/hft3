"""Temporal audit tests for MBOFeatureExtractor -- validates Ft filtration integrity."""
from __future__ import annotations

import numpy as np
import pytest

from features_engine.src.features.mbo_features import MBOEvent, MBOFeatureExtractor
from features_engine.src.features.feature_index import FEATURE_DIM, FeatureIndex


def _fi(fi_val):
    return FeatureIndex(fi_val)


def test_empty_book_start():
    ex = MBOFeatureExtractor(tick_size=0.25)
    ev = MBOEvent(1_700_000_000_000_000_000, 1, "ADD", "B", 100.0, 10)
    vec = ex.process_event(ev)
    assert vec.shape == (FEATURE_DIM,)
    assert vec[FeatureIndex.TOP_1_DEPTH_BID] == pytest.approx(10.0)
    assert vec[FeatureIndex.BUY_AGGRESSOR_VOLUME] == pytest.approx(0.0)
    assert vec[FeatureIndex.SELL_AGGRESSOR_VOLUME] == pytest.approx(0.0)
    assert vec[FeatureIndex.SPREAD] == pytest.approx(0.0)


def test_basic_add_and_bbo():
    ex = MBOFeatureExtractor(tick_size=0.25)
    events = [
        MBOEvent(1_000_000_000, 1, "ADD", "B", 100.0, 10),
        MBOEvent(1_000_000_001, 2, "ADD", "B", 99.5, 5),
        MBOEvent(1_000_000_002, 3, "ADD", "B", 99.0, 20),
        MBOEvent(1_000_000_003, 4, "ADD", "A", 100.5, 8),
        MBOEvent(1_000_000_004, 5, "ADD", "A", 101.0, 15),
        MBOEvent(1_000_000_005, 6, "ADD", "A", 101.5, 25),
    ]
    vec = None
    for ev in events:
        vec = ex.process_event(ev)
    best_bid = ex.book.get_best_bid()
    best_ask = ex.book.get_best_ask()
    assert best_bid == pytest.approx(100.0)
    assert best_ask == pytest.approx(100.5)
    assert vec[FeatureIndex.SPREAD] == pytest.approx(0.5)
    assert vec[FeatureIndex.TOP_1_DEPTH_BID] == pytest.approx(10.0)
    assert vec[FeatureIndex.TOP_3_DEPTH_BID] == pytest.approx(35.0)
    assert vec[FeatureIndex.TOP_1_DEPTH_ASK] == pytest.approx(8.0)
    assert vec[FeatureIndex.TOP_3_DEPTH_ASK] == pytest.approx(48.0)


def test_aggressor_volume_accumulation():
    ex = MBOFeatureExtractor(tick_size=0.25)
    ex.process_event(MBOEvent(1_000_000_000, 1, "ADD", "B", 100.0, 10))
    ex.process_event(MBOEvent(1_000_000_001, 2, "ADD", "A", 100.5, 10))
    ex.process_event(MBOEvent(1_000_000_002, 2, "TRADE", "A", 100.5, 3))
    ex.process_event(MBOEvent(1_000_000_003, 2, "TRADE", "A", 100.5, 2))
    vec = ex.process_event(MBOEvent(1_000_000_004, 1, "TRADE", "B", 100.0, 1))
    assert vec[FeatureIndex.BUY_AGGRESSOR_VOLUME] == pytest.approx(5.0)
    assert vec[FeatureIndex.SELL_AGGRESSOR_VOLUME] == pytest.approx(1.0)
    assert vec[FeatureIndex.AGGRESSOR_VOLUME_IMBALANCE] == pytest.approx(4.0 / 6.0)


def test_cancel_near_touch_pressure():
    ex = MBOFeatureExtractor(tick_size=0.25)
    ex.process_event(MBOEvent(1_000_000_000, 1, "ADD", "B", 99.75, 10))
    ex.process_event(MBOEvent(1_000_000_001, 2, "ADD", "A", 100.0, 10))
    ex.process_event(MBOEvent(1_000_000_002, 5, "ADD", "B", 99.5, 5))
    ex.process_event(MBOEvent(1_000_000_003, 3, "ADD", "B", 100.0, 5))
    ex.process_event(MBOEvent(1_000_000_004, 3, "CANCEL", "B", 100.0, 5))
    vec = ex.process_event(MBOEvent(1_000_000_005, 5, "CANCEL", "B", 99.5, 2))
    assert vec[FeatureIndex.NEAR_TOUCH_CANCEL_PRESSURE] > 0.0


def test_cancel_to_add_ratio():
    ex = MBOFeatureExtractor(tick_size=0.25)
    ex.process_event(MBOEvent(1_000_000_000, 1, "ADD", "B", 100.0, 10))
    ex.process_event(MBOEvent(1_000_000_001, 2, "ADD", "A", 100.25, 10))
    vec = ex.process_event(MBOEvent(1_000_000_002, 1, "CANCEL", "B", 100.0, 5))
    assert vec[FeatureIndex.CANCEL_TO_ADD_RATIO] == pytest.approx(0.25)


def test_queue_depletion():
    ex = MBOFeatureExtractor(tick_size=0.25)
    ex.process_event(MBOEvent(1_000_000_000, 1, "ADD", "B", 100.0, 10))
    vec = ex.process_event(MBOEvent(1_000_000_001, 1, "CANCEL", "B", 100.0, 8))
    assert vec[FeatureIndex.QUEUE_DEPLETION_RATE_BID] == pytest.approx(0.8)
    assert vec[FeatureIndex.QUEUE_DEPLETION_RATE_ASK] == pytest.approx(0.0)


def test_spread_stress():
    ex = MBOFeatureExtractor(tick_size=0.25)
    ex.process_event(MBOEvent(1_000_000_000, 1, "ADD", "B", 100.0, 10))
    ex.process_event(MBOEvent(1_000_000_001, 2, "ADD", "A", 100.5, 10))
    ex.process_event(MBOEvent(1_000_000_002, 3, "ADD", "B", 100.0, 5))
    ex.process_event(MBOEvent(1_000_000_003, 4, "ADD", "A", 102.0, 10))
    vec = ex.process_event(MBOEvent(1_000_000_004, 2, "CANCEL", "A", 100.5, 10))
    assert vec[FeatureIndex.SPREAD_STRESS] > 0.0


def test_iceberg_reload_score():
    ex = MBOFeatureExtractor(tick_size=0.25)
    ex.process_event(MBOEvent(1_000_000_000, 1, "ADD", "B", 100.0, 10))
    ex.process_event(MBOEvent(1_000_000_001, 2, "ADD", "A", 100.5, 10))
    ex.process_event(MBOEvent(1_000_000_002, 1, "TRADE", "B", 100.0, 3))
    ex.process_event(MBOEvent(1_000_000_003, 3, "ADD", "B", 100.0, 5))
    vec = ex.process_event(MBOEvent(1_000_000_004, 4, "ADD", "B", 100.0, 5))
    assert vec[FeatureIndex.ICEBERG_RELOAD_SCORE] > 0.0


def test_window_reset_on_gap():
    window_ns = 1_000_000_000
    t0 = 10_000_000_000_000_000_000
    ex = MBOFeatureExtractor(tick_size=0.25, rolling_window_ns=window_ns)
    ex.process_event(MBOEvent(t0, 1, "ADD", "B", 100.0, 10))
    ex.process_event(MBOEvent(t0 + 10_000, 2, "ADD", "A", 100.5, 8))
    ex.process_event(MBOEvent(t0 + 20_000, 3, "ADD", "B", 99.5, 5))
    gap_ts = t0 + 2_000_000_000
    gap_vec = ex.process_event(MBOEvent(gap_ts, 1, "MODIFY", "B", 100.0, 7))
    assert gap_vec[FeatureIndex.BUY_AGGRESSOR_VOLUME] == pytest.approx(0.0)
    assert gap_vec[FeatureIndex.SELL_AGGRESSOR_VOLUME] == pytest.approx(0.0)
    bid1 = ex.book.top_k_depth(1)[0]
    assert bid1 > 0


def test_temporal_ordering_no_leak():
    events = []
    t0 = 10_000_000_000_000_000_000
    prices_bid = [100.0, 99.75, 99.5, 99.25, 99.0, 98.75, 98.5, 98.0, 97.5, 97.0,
                  96.5, 100.25, 100.5, 100.75, 101.0, 99.0, 99.5, 100.0, 100.25, 100.5]
    prices_ask = [100.5, 100.75, 101.0, 101.25, 101.5, 101.0, 100.5, 101.5, 102.0, 102.5]
    side_cycle = ["B", "A", "B", "A", "B", "A", "B", "A", "B", "A",
                  "B", "A", "B", "A", "B", "A", "B", "A", "B", "A"]
    for i in range(20):
        side = side_cycle[i]
        price = prices_bid[i] if side == "B" else prices_ask[i % len(prices_ask)]
        events.append(MBOEvent(t0 + i * 1_000_000, i + 1, "ADD", side, price, 10))
    mid = 9
    ex_a = MBOFeatureExtractor(tick_size=0.25)
    ex_b = MBOFeatureExtractor(tick_size=0.25)
    ex_c = MBOFeatureExtractor(tick_size=0.25)
    vec_a = None
    vec_c = None
    for i in range(mid + 1):
        vec_a = ex_a.process_event(events[i])
        vec_c = ex_c.process_event(events[i])
    vec_b = None
    for i in range(mid + 2):
        vec_b = ex_b.process_event(events[i])
    np.testing.assert_allclose(vec_a, vec_c, rtol=0, atol=1e-12)
    assert not np.allclose(vec_a, vec_b, rtol=0, atol=1e-12)


def test_feature_vector_shape_and_range():
    ex = MBOFeatureExtractor(tick_size=0.25)
    events = [
        MBOEvent(1_000_000_000, 1, "ADD", "B", 100.0, 10),
        MBOEvent(1_000_000_001, 2, "ADD", "A", 100.5, 10),
        MBOEvent(1_000_000_002, 1, "CANCEL", "B", 100.0, 3),
        MBOEvent(1_000_000_003, 2, "CANCEL", "A", 100.5, 3),
        MBOEvent(1_000_000_004, 3, "ADD", "B", 99.75, 5),
        MBOEvent(1_000_000_005, 4, "ADD", "A", 100.75, 5),
        MBOEvent(1_000_000_006, 2, "TRADE", "A", 100.5, 2),
        MBOEvent(1_000_000_007, 1, "TRADE", "B", 100.0, 2),
        MBOEvent(1_000_000_008, 5, "ADD", "B", 100.0, 8),
        MBOEvent(1_000_000_009, 6, "ADD", "A", 100.5, 8),
    ]
    for ev in events:
        vec = ex.process_event(ev)
        assert vec.shape == (FEATURE_DIM,)
        assert not np.any(np.isnan(vec))
        assert not np.any(np.isinf(vec))
        assert np.all(vec >= -1e12) and np.all(vec <= 1e12)
        assert vec[FeatureIndex.MID_PRICE] >= 0.0
