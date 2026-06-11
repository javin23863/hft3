"""Python feature extractor golden vectors (C++ parity target)."""
import sys
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from features_engine.src.features.feature_index import FeatureIndex
from features_engine.src.features.mbo_features import MBOEvent, MBOFeatureExtractor


def _run_sequence() -> np.ndarray:
    ex = MBOFeatureExtractor(tick_size=0.25)
    events = [
        MBOEvent(1_000, 1, "ADD", "B", 5500.0, 10),
        MBOEvent(1_001, 2, "ADD", "A", 5501.0, 8),
        MBOEvent(1_002, 3, "ADD", "B", 5500.5, 5),
        MBOEvent(1_003, 4, "CANCEL", "B", 5500.5, 5),
        MBOEvent(1_004, 5, "TRADE", "A", 5501.0, 2),
    ]
    vec = None
    for ev in events:
        vec = ex.process_event(ev)
    return vec


def test_golden_agg_imb_and_mid():
    vec = _run_sequence()
    assert vec.shape == (64,)
    assert -1.0 <= vec[FeatureIndex.AGGRESSOR_VOLUME_IMBALANCE] <= 1.0
    assert vec[FeatureIndex.MID_PRICE] > 0
    assert vec[FeatureIndex.SPREAD] >= 0
    assert vec[FeatureIndex.REALIZED_VOL_STATE] > 0


def test_realized_vol_resets_on_window_gap():
    tick = 0.25
    ex = MBOFeatureExtractor(tick_size=tick, rolling_window_ns=1_000)
    ex.process_event(MBOEvent(0, 1, "ADD", "B", 5500.0, 10))
    ex.process_event(MBOEvent(100, 2, "ADD", "A", 5501.0, 8))
    ex.process_event(MBOEvent(200, 3, "ADD", "B", 5500.5, 8))
    ex.process_event(MBOEvent(250, 6, "ADD", "A", 5500.75, 8))
    v1 = ex.process_event(MBOEvent(300, 4, "TRADE", "A", 5501.0, 1))
    pre_gap_vol = v1[FeatureIndex.REALIZED_VOL_STATE]
    assert pre_gap_vol > 0
    v_gap = ex.process_event(MBOEvent(5_000, 5, "ADD", "B", 5501.0, 5))
    assert v_gap[FeatureIndex.REALIZED_VOL_STATE] == 0.0
    mid_after_gap = (5501.0 + 5500.75) / 2.0
    v2 = ex.process_event(MBOEvent(5_100, 7, "ADD", "A", 5500.5, 5))
    mid_after_move = (5501.0 + 5500.5) / 2.0
    single_return = abs((mid_after_move - mid_after_gap) / tick)
    np.testing.assert_allclose(
        v2[FeatureIndex.REALIZED_VOL_STATE], single_return, rtol=0, atol=1e-12
    )
    assert v2[FeatureIndex.REALIZED_VOL_STATE] != pre_gap_vol


def test_deterministic_replay():
    a = _run_sequence()
    b = _run_sequence()
    np.testing.assert_allclose(a, b, rtol=0, atol=1e-12)


def test_iceberg_absorption_cleared_on_window_gap():
    """Rolling-window gap must flush _trade_at_level and _reload_at_level.

    Pins the FIXED semantics for ICEBERG_RELOAD_SCORE (slot 22) and
    ABSORPTION_SCORE (slot 21): after a gap wider than rolling_window_ns,
    both slots must reflect a clean accumulator (no carries from the prior
    window).

    Sequence:
      Phase 1 (t=0..300): ADD + TRADE at same level → _reload_at_level and
        _trade_at_level both non-empty; ICEBERG_RELOAD_SCORE > 0.
      Gap: jump > rolling_window_ns.
      Phase 2 (t=gap..): single ADD at a new level → no trades in fresh window,
        so ICEBERG_RELOAD_SCORE == 0 (tanh(0) == 0) and ABSORPTION_SCORE == 0
        (no agg vol in fresh window).
    """
    tick = 0.25
    window_ns = 1_000
    ex = MBOFeatureExtractor(tick_size=tick, rolling_window_ns=window_ns)

    # Phase 1: build up iceberg / absorption signal
    ex.process_event(MBOEvent(0,   1, "ADD",   "B", 5500.0, 20))
    ex.process_event(MBOEvent(50,  2, "TRADE", "B", 5500.0, 5))
    ex.process_event(MBOEvent(100, 3, "ADD",   "B", 5500.0, 20))   # reload at same level
    ex.process_event(MBOEvent(150, 4, "TRADE", "B", 5500.0, 5))
    ex.process_event(MBOEvent(200, 5, "ADD",   "B", 5500.0, 20))   # second reload
    v_pre = ex.process_event(MBOEvent(300, 6, "TRADE", "A", 5500.25, 1))

    # ICEBERG_RELOAD_SCORE should be non-zero (2 reloads + trades at same level)
    assert v_pre[FeatureIndex.ICEBERG_RELOAD_SCORE] != 0.0, (
        "Expected non-zero ICEBERG_RELOAD_SCORE before gap"
    )

    # Phase 2 (gap > rolling_window_ns): window must flush
    t_gap = 300 + window_ns + 1
    ex.process_event(MBOEvent(t_gap,     7, "ADD", "A", 5500.50, 10))
    ex.process_event(MBOEvent(t_gap + 1, 8, "ADD", "B", 5499.75, 10))
    v_post = ex.process_event(MBOEvent(t_gap + 2, 9, "ADD", "A", 5500.25, 5))

    # After gap: no trades, no reloads-with-trades → scores must be zero
    assert v_post[FeatureIndex.ICEBERG_RELOAD_SCORE] == 0.0, (
        f"ICEBERG_RELOAD_SCORE not cleared after window gap: "
        f"{v_post[FeatureIndex.ICEBERG_RELOAD_SCORE]}"
    )
    assert v_post[FeatureIndex.ABSORPTION_SCORE] == 0.0, (
        f"ABSORPTION_SCORE not cleared after window gap: "
        f"{v_post[FeatureIndex.ABSORPTION_SCORE]}"
    )
