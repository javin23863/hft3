"""C++ golden parity for REALIZED_VOL (26) and regime slots (41-49)."""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from features_engine.src.features.feature_index import (
    REGIME_INDEX_MAP,
    FeatureIndex,
    vector_to_feature_dict,
)
from features_engine.src.features.mbo_features import MBOEvent, MBOFeatureExtractor
from features_engine.src.regime.regime_filter import RegimeFilter

_PARITY_SLOTS = [FeatureIndex.REALIZED_VOL_STATE, *range(41, 50)]


def _golden_exe() -> Path | None:
    from workbench.src.sim.cpp_binary import resolve_cpp_binary

    return resolve_cpp_binary(_REPO, "hft_feature_golden")


def _run_sequence_python() -> np.ndarray:
    ex = MBOFeatureExtractor(tick_size=0.25)
    rf = RegimeFilter()
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
        posterior = rf.update(vector_to_feature_dict(vec), "NORMAL")
        for regime, prob in posterior.items():
            idx = REGIME_INDEX_MAP.get(regime)
            if idx is not None:
                vec[idx] = prob
    assert vec is not None
    return np.array([vec[i] for i in _PARITY_SLOTS], dtype=np.float64)


def _run_sequence_cpp(exe: Path) -> np.ndarray:
    proc = subprocess.run(
        [str(exe)],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(proc.stdout.strip())
    return np.array([float(payload[str(i)]) for i in _PARITY_SLOTS], dtype=np.float64)


def test_cpp_feature_golden_realized_vol_and_regime():
    exe = _golden_exe()
    if exe is None:
        pytest.skip("hft_feature_golden not built (cmake --build build)")
    py = _run_sequence_python()
    cpp = _run_sequence_cpp(exe)
    np.testing.assert_allclose(py, cpp, rtol=0.0, atol=1e-9)
