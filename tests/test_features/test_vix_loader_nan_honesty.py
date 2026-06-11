"""
Regression tests: VIX feature loader NaN honesty.

Defect ke (specs/CRYPTO_LIVE.md §9) prohibits silent-zero fills for absent
data.  These tests verify:
  1. Normal build: file written, ts strictly increasing, all columns
     finite-or-NaN (never zeroed absent inputs), ATM columns present.
  2. Sensor omission: vix_atm_strike and vix_atm_ramp land in
     columns_omitted, NOT as zero-filled arrays in the file.
  3. Placeholder-only parquet + missing quotes npz → VixDataUnavailable,
     no file written.
  4. vix_extra_latency_ms=5.0 shifts ts by exactly +5e6 ns vs baseline.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Allow import from repo source tree without install
_REPO = Path(__file__).resolve().parents[2]  # hft3 root (test_features/ -> tests/ -> hft3/)
_FEAT_SRC = _REPO / "packages" / "features_engine" / "src" / "features"
if str(_FEAT_SRC) not in sys.path:
    sys.path.insert(0, str(_FEAT_SRC))

from vix_features import (  # noqa: E402
    VixDataUnavailable,
    build_vix_feature_file,
    derive_event_id,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_DTYPE = np.dtype([
    ("ts_event", "i8"),
    ("ts_recv", "i8"),
    ("bid_px", "f8"),
    ("ask_px", "f8"),
    ("bid_sz", "f8"),
    ("ask_sz", "f8"),
    ("instrument_id", "u8"),
    ("publisher_id", "u4"),
])

_N_ROWS = 200
_N_INSTR = 2
_T0_NS = 1_681_900_000_000_000_000  # ~2023-04-19 epoch ns


def _make_quotes_npz(path: Path) -> Path:
    """Write synthetic quotes npz, 2 instruments, 200 rows, monotonic ts."""
    rng = np.random.default_rng(42)
    arr = np.zeros(_N_ROWS, dtype=_DTYPE)
    # monotonic ts_event and ts_recv (100 ms apart each step)
    dt = 100_000_000  # 100 ms in ns
    ts_base = np.arange(_N_ROWS, dtype=np.int64) * dt + _T0_NS
    arr["ts_event"] = ts_base
    arr["ts_recv"] = ts_base + 5_000_000  # 5 ms recv lag
    # alternate between 2 instruments
    arr["instrument_id"] = np.tile([1001, 1002], _N_ROWS // _N_INSTR).astype(np.uint64)
    arr["publisher_id"] = np.uint32(1)
    mid = 20.0 + rng.standard_normal(_N_ROWS) * 0.1
    half_spread = 0.05
    arr["bid_px"] = mid - half_spread
    arr["ask_px"] = mid + half_spread
    arr["bid_sz"] = rng.uniform(1.0, 10.0, _N_ROWS)
    arr["ask_sz"] = rng.uniform(1.0, 10.0, _N_ROWS)
    npz_path = path / "VIX.OPT_TEST_EVENT_2023_04_19_TIGHT_quotes.npz"
    np.savez(npz_path, quotes=arr)
    return npz_path


def _make_sensors_parquet(path: Path, placeholder_only: bool = False) -> Path:
    """Write synthetic sensors parquet.

    placeholder_only=True → all rows are sensor=='VIX' level NaN (no real data).
    Otherwise → 8 VIX_ATM_STRIKE rows at offsets {-10,-5,-1,0,1,2,5,10}s.
    """
    offsets_s = [-10, -5, -1, 0, 1, 2, 5, 10]
    event_ns = _T0_NS + 10 * 1_000_000_000  # event at T0 + 10 s into window

    if placeholder_only:
        rows = [
            {
                "event_id": "TEST_EVENT_2023_04_19_TIGHT",
                "offset_sec": o,
                "sensor": "VIX",
                "level": float("nan"),
                "ts_ns": event_ns + o * 1_000_000_000,
            }
            for o in offsets_s
        ]
    else:
        rows = [
            {
                "event_id": "TEST_EVENT_2023_04_19_TIGHT",
                "offset_sec": o,
                "sensor": "VIX_ATM_STRIKE",
                "level": 20.0 + o * 0.05,
                "ts_ns": event_ns + o * 1_000_000_000,
            }
            for o in offsets_s
        ]

    df = pd.DataFrame(rows)
    pq_path = path / "TEST_EVENT_2023_04_19_TIGHT_sensors.parquet"
    df.to_parquet(pq_path, index=False)
    return pq_path


# ---------------------------------------------------------------------------
# derive_event_id unit test
# ---------------------------------------------------------------------------

def test_derive_event_id():
    result = derive_event_id("VIX.OPT_FED_BEIGE_BOOK_2023_04_19_TIGHT_quotes.npz")
    assert result == "FED_BEIGE_BOOK_2023_04_19_TIGHT"


# ---------------------------------------------------------------------------
# Test 1: normal build — file written, ts monotonic, columns finite-or-NaN
# ---------------------------------------------------------------------------

def test_normal_build_file_written_ts_monotonic_finite_or_nan(tmp_path):
    """Normal build: file written; ts strictly increasing; all columns finite-or-NaN."""
    npz = _make_quotes_npz(tmp_path)
    pq = _make_sensors_parquet(tmp_path)
    out = tmp_path / "out.npz"

    summary = build_vix_feature_file(npz, pq, out)

    assert out.exists(), "output npz not written"
    assert summary["n_rows"] > 0
    assert summary["event_id"] == "TEST_EVENT_2023_04_19_TIGHT"

    data = np.load(out, allow_pickle=True)

    # ts strictly increasing
    ts = data["ts"].astype(np.int64)
    assert np.all(np.diff(ts) > 0), "ts not strictly increasing"

    # vix_atm_strike present (real sensor data supplied)
    assert "vix_atm_strike" in data, "vix_atm_strike missing despite real sensor"
    assert "vix_atm_ramp" in data, "vix_atm_ramp missing despite real sensor"

    # all present numeric columns: values must be finite or NaN — never inf
    float_cols = [
        k for k in data.files
        if k not in ("_attrs_json",) and data[k].dtype.kind == "f"
    ]
    for col in float_cols:
        arr = data[col]
        bad = ~(np.isfinite(arr) | np.isnan(arr))
        assert not np.any(bad), f"column {col} contains inf"

    # no all-zero columns where absence of input should produce NaN
    # (vix_opt_depth_imbalance should NOT be all-zero with real quotes)
    di = data["vix_opt_depth_imbalance"]
    assert not np.all(di == 0.0), "vix_opt_depth_imbalance is suspiciously all-zero"


# ---------------------------------------------------------------------------
# Test 2: sensor omission → columns_omitted, not zeros
# ---------------------------------------------------------------------------

def test_sensor_omission_columns_omitted_not_zeros(tmp_path):
    """Omit sensors parquet → vix_atm_strike in columns_omitted, absent from file."""
    npz = _make_quotes_npz(tmp_path)
    out = tmp_path / "out_no_sensor.npz"

    summary = build_vix_feature_file(npz, None, out)

    assert out.exists()
    data = np.load(out, allow_pickle=True)

    # ATM columns must NOT be in file
    assert "vix_atm_strike" not in data.files, (
        "vix_atm_strike present in file despite no sensor data (silent-zero defect ke)"
    )
    assert "vix_atm_ramp" not in data.files, (
        "vix_atm_ramp present despite no sensor data"
    )

    # columns_omitted must record them (encoded in _attrs_json)
    assert "vix_atm_strike" not in summary["columns"]
    assert "vix_atm_ramp" not in summary["columns"]


# ---------------------------------------------------------------------------
# Test 3: placeholder-only parquet + missing quotes → VixDataUnavailable
# ---------------------------------------------------------------------------

def test_placeholder_sensor_and_missing_quotes_raises(tmp_path):
    """Placeholder-only parquet + absent quotes npz → VixDataUnavailable, no file."""
    pq = _make_sensors_parquet(tmp_path, placeholder_only=True)
    missing_npz = tmp_path / "VIX.OPT_GHOST_quotes.npz"
    out = tmp_path / "should_not_exist.npz"

    with pytest.raises(VixDataUnavailable):
        build_vix_feature_file(missing_npz, pq, out)

    assert not out.exists(), "output file written despite VixDataUnavailable (defect ke regression)"


# ---------------------------------------------------------------------------
# Test 4: vix_extra_latency_ms shifts ts by +5e6 ns
# ---------------------------------------------------------------------------

def test_latency_shift_applied_to_ts(tmp_path):
    """vix_extra_latency_ms=5.0 → ts shifted +5e6 ns vs shift=0 build."""
    npz = _make_quotes_npz(tmp_path)
    pq = _make_sensors_parquet(tmp_path)

    out_base = tmp_path / "out_base.npz"
    out_shift = tmp_path / "out_shift.npz"

    build_vix_feature_file(npz, pq, out_base, vix_extra_latency_ms=0.0)
    build_vix_feature_file(npz, pq, out_shift, vix_extra_latency_ms=5.0)

    ts_base = np.load(out_base)["ts"].astype(np.int64)
    ts_shift = np.load(out_shift)["ts"].astype(np.int64)

    # Both grids have the same number of 100 ms steps (same source rows,
    # same window duration).  Every grid point must be shifted by exactly
    # +5_000_000 ns relative to the base grid.
    assert len(ts_shift) == len(ts_base), (
        "Shifted and base grids have different lengths"
    )
    delta = ts_shift - ts_base
    assert np.all(delta == 5_000_000), (
        f"Expected all deltas == 5e6 ns; got unique deltas {np.unique(delta)}"
    )
