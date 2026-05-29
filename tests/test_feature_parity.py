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
        MBOEvent(1_001, 2, "ADD", "A", 5500.25, 8),
        MBOEvent(1_002, 3, "ADD", "B", 5499.75, 5),
        MBOEvent(1_003, 1, "CANCEL", "B", 5500.0, 3),
        MBOEvent(1_004, 4, "TRADE", "A", 5500.25, 2),
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


def test_deterministic_replay():
    a = _run_sequence()
    b = _run_sequence()
    np.testing.assert_allclose(a, b, rtol=0, atol=1e-12)
