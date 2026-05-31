"""T0: MBO book reconstruction golden vectors."""
from __future__ import annotations

import numpy as np

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
    assert vec is not None
    return vec


def test_golden_agg_imb_and_mid() -> None:
    vec = _run_sequence()
    assert vec.shape == (64,)
    assert -1.0 <= vec[FeatureIndex.AGGRESSOR_VOLUME_IMBALANCE] <= 1.0
    assert vec[FeatureIndex.MID_PRICE] > 0
    assert vec[FeatureIndex.SPREAD] >= 0
    assert vec[FeatureIndex.REALIZED_VOL_STATE] > 0


def test_deterministic_replay() -> None:
    a = _run_sequence()
    b = _run_sequence()
    np.testing.assert_allclose(a, b, rtol=0, atol=1e-12)
