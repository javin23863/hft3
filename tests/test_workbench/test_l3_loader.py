"""L3 loader quality checks."""

import numpy as np
import pytest

from workbench.src.data.l3_loader import L3Loader


def _synthetic_npz(n=10):
    dtype = np.dtype([
        ("ev", "u8"), ("local_ts", "i8"), ("px", "f8"),
        ("qty", "u4"), ("order_id", "u8"),
    ])
    rows = []
    for i in range(n):
        rows.append((1, i * 1_000_000, 100.0 + i * 0.25, 5, 1000 + i))
    return np.array(rows, dtype=dtype)


def test_loader_scan_clean():
    loader = L3Loader(gap_threshold_ns=10_000_000_000, require_snapshot_on_gap=False)
    raw = _synthetic_npz()
    loader._scan(raw)
    assert loader.report.event_count == 10
    assert loader.report.gap_count == 0


def test_loader_gap_requires_snapshot():
    loader = L3Loader(gap_threshold_ns=500, require_snapshot_on_gap=True)
    raw = _synthetic_npz(5)
    raw[2]["local_ts"] = raw[1]["local_ts"] + 1_000_000_000
    with pytest.raises(ValueError, match="Gap"):
        loader._scan(raw)
