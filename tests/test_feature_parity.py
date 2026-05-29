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
