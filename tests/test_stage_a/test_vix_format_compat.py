"""VIX format compatibility tests for Stage A VIX loader.

Tests three scenarios:
  (a) Real per-column format (written by build_vix_feature_file) → has_vix True,
      columns sorted alphabetically, vix_X shape matches.
  (b) Legacy matrix format (columns list + X matrix) → still loads correctly.
  (c) File with only ts + audit columns → vix_load_error="no_feature_columns".
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

# ---------------------------------------------------------------------------
# Synthetic quotes / sensor builders (mirrors test_vix_loader_nan_honesty.py)
# ---------------------------------------------------------------------------

_DTYPE = np.dtype([
    ("ts_event", "i8"), ("ts_recv", "i8"),
    ("bid_px", "f8"), ("ask_px", "f8"),
    ("bid_sz", "f8"), ("ask_sz", "f8"),
    ("instrument_id", "u8"), ("publisher_id", "u4"),
])
_N = 200
_T0 = 1_681_900_000_000_000_000  # ~2023-04-19


def _make_quotes_npz(path: Path) -> Path:
    rng = np.random.default_rng(42)
    arr = np.zeros(_N, dtype=_DTYPE)
    dt = 100_000_000  # 100 ms in ns
    ts_base = np.arange(_N, dtype=np.int64) * dt + _T0
    arr["ts_event"] = ts_base
    arr["ts_recv"] = ts_base + 5_000_000
    arr["instrument_id"] = np.tile([1001, 1002], _N // 2).astype(np.uint64)
    arr["publisher_id"] = np.uint32(1)
    mid = 20.0 + rng.standard_normal(_N) * 0.1
    arr["bid_px"] = mid - 0.05
    arr["ask_px"] = mid + 0.05
    arr["bid_sz"] = rng.uniform(1.0, 10.0, _N)
    arr["ask_sz"] = rng.uniform(1.0, 10.0, _N)
    p = path / "VIX.OPT_COMPAT_TEST_quotes.npz"
    np.savez(p, quotes=arr)
    return p


def _make_sensors_parquet(path: Path) -> Path:
    offsets_s = [-10, -5, -1, 0, 1, 2, 5, 10]
    event_ns = _T0 + 10 * 1_000_000_000
    rows = [
        {
            "event_id": "COMPAT_TEST",
            "offset_sec": o,
            "sensor": "VIX_ATM_STRIKE",
            "level": 20.0 + o * 0.05,
            "ts_ns": event_ns + o * 1_000_000_000,
        }
        for o in offsets_s
    ]
    p = path / "COMPAT_TEST_sensors.parquet"
    pd.DataFrame(rows).to_parquet(p, index=False)
    return p


# ---------------------------------------------------------------------------
# Helper: invoke the VIX load block from process_event (extracted inline)
# ---------------------------------------------------------------------------

def _load_vix_npz(vix_sp: Path):
    """Mirror the exact VIX load logic from stage_a_screen.process_event.

    Returns (vix_ts, vix_cols, vix_X, vix_load_error).
    """
    _AUDIT_KEYS = {"ts", "ts_event_raw", "ts_recv_raw", "_attrs_json"}
    vix_ts = None
    vix_X = None
    vix_cols: list[str] = []
    vix_load_error = None

    try:
        with np.load(str(vix_sp), allow_pickle=True) as va:
            vix_ts = np.array(va["ts"], dtype=np.int64)
            if "columns" in va.files:
                vix_cols = list(va["columns"])
                vix_X = np.array(va["X"])
            elif "X" in va.files:
                vix_X = np.array(va["X"])
            else:
                n_ts = len(vix_ts)
                _feat_keys = sorted(
                    k for k in va.files
                    if k not in _AUDIT_KEYS
                    and va[k].ndim == 1
                    and va[k].dtype.kind == "f"
                    and len(va[k]) == n_ts
                )
                if not _feat_keys:
                    vix_ts = None
                    vix_load_error = "no_feature_columns"
                else:
                    vix_cols = _feat_keys
                    vix_X = np.column_stack([va[k] for k in _feat_keys])
    except Exception as exc:
        vix_ts = None
        vix_X = None
        vix_load_error = str(exc)

    has_vix = vix_ts is not None and vix_X is not None and len(vix_ts) > 0
    return vix_ts, vix_cols, vix_X, vix_load_error, has_vix


# ---------------------------------------------------------------------------
# (a) Real per-column format written by build_vix_feature_file
# ---------------------------------------------------------------------------

class TestRealPerColumnFormat:

    def test_has_vix_true(self, tmp_path):
        """Real per-column file → has_vix True."""
        from features_engine.src.features.vix_features import build_vix_feature_file

        quotes = _make_quotes_npz(tmp_path)
        sensors = _make_sensors_parquet(tmp_path)
        out = tmp_path / "vix_real.npz"
        build_vix_feature_file(quotes, sensors, out)

        _, vix_cols, vix_X, vix_load_error, has_vix = _load_vix_npz(out)

        assert has_vix, f"has_vix False; vix_load_error={vix_load_error!r}"
        assert vix_load_error is None

    def test_columns_sorted_alphabetically(self, tmp_path):
        """Real per-column file → vix_cols sorted alphabetically."""
        from features_engine.src.features.vix_features import build_vix_feature_file

        quotes = _make_quotes_npz(tmp_path)
        sensors = _make_sensors_parquet(tmp_path)
        out = tmp_path / "vix_sorted.npz"
        build_vix_feature_file(quotes, sensors, out)

        _, vix_cols, vix_X, _, has_vix = _load_vix_npz(out)

        assert has_vix
        assert vix_cols == sorted(vix_cols), (
            f"vix_cols not sorted: {vix_cols}"
        )

    def test_vix_X_shape_matches_cols(self, tmp_path):
        """vix_X columns count matches len(vix_cols)."""
        from features_engine.src.features.vix_features import build_vix_feature_file

        quotes = _make_quotes_npz(tmp_path)
        sensors = _make_sensors_parquet(tmp_path)
        out = tmp_path / "vix_shape.npz"
        build_vix_feature_file(quotes, sensors, out)

        vix_ts, vix_cols, vix_X, _, has_vix = _load_vix_npz(out)

        assert has_vix
        assert vix_X is not None
        assert vix_X.ndim == 2
        assert vix_X.shape[1] == len(vix_cols), (
            f"vix_X.shape={vix_X.shape} but len(vix_cols)={len(vix_cols)}"
        )
        assert vix_X.shape[0] == len(vix_ts)

    def test_expected_feature_columns_present(self, tmp_path):
        """Real file with sensors → expected feature keys present in vix_cols."""
        from features_engine.src.features.vix_features import build_vix_feature_file

        quotes = _make_quotes_npz(tmp_path)
        sensors = _make_sensors_parquet(tmp_path)
        out = tmp_path / "vix_keys.npz"
        build_vix_feature_file(quotes, sensors, out)

        _, vix_cols, _, _, has_vix = _load_vix_npz(out)

        assert has_vix
        required = [
            "vix_opt_quote_intensity",
            "vix_quote_arrival_accel",
            "vix_opt_spread_stress",
            "vix_opt_depth_imbalance",
            "vix_opt_bipower_var",
            "vix_opt_tsrv",
            "vix_atm_strike",  # sensors present
            "vix_atm_ramp",
        ]
        for key in required:
            assert key in vix_cols, f"Expected key {key!r} missing from vix_cols={vix_cols}"


# ---------------------------------------------------------------------------
# (b) Legacy matrix format (columns list + X matrix)
# ---------------------------------------------------------------------------

class TestLegacyMatrixFormat:

    def test_legacy_columns_X_loads(self, tmp_path):
        """Legacy format: columns array + X matrix → has_vix True, cols correct."""
        n = 20
        ts = np.arange(n, dtype=np.int64) * 1_000_000 + _T0
        X = np.ones((n, 3), dtype=np.float64) * 20.0
        columns = np.array(["vix_col_a", "vix_col_b", "vix_col_c"], dtype=object)
        out = tmp_path / "vix_legacy.npz"
        np.savez(out, ts=ts, X=X, columns=columns)

        _, vix_cols, vix_X, vix_load_error, has_vix = _load_vix_npz(out)

        assert has_vix, f"has_vix False for legacy format; error={vix_load_error!r}"
        assert vix_load_error is None
        assert vix_cols == ["vix_col_a", "vix_col_b", "vix_col_c"]
        assert vix_X is not None
        assert vix_X.shape == (n, 3)

    def test_legacy_X_only_loads(self, tmp_path):
        """Legacy format: X matrix without columns key → has_vix True."""
        n = 15
        ts = np.arange(n, dtype=np.int64) * 1_000_000 + _T0
        X = np.ones((n, 2), dtype=np.float64) * 15.0
        out = tmp_path / "vix_legacy_nokeys.npz"
        np.savez(out, ts=ts, X=X)

        _, vix_cols, vix_X, vix_load_error, has_vix = _load_vix_npz(out)

        assert has_vix, f"has_vix False for X-only legacy; error={vix_load_error!r}"
        assert vix_load_error is None
        assert vix_X is not None
        assert vix_X.shape == (n, 2)


# ---------------------------------------------------------------------------
# (c) Audit-only file → no_feature_columns
# ---------------------------------------------------------------------------

class TestNoFeatureColumns:

    def test_ts_and_audit_only(self, tmp_path):
        """File with only ts + audit columns → vix_load_error='no_feature_columns'."""
        n = 10
        ts = np.arange(n, dtype=np.int64) * 1_000_000 + _T0
        ts_event_raw = ts - 5_000_000
        ts_recv_raw = ts
        out = tmp_path / "vix_audit_only.npz"
        np.savez(
            out,
            ts=ts,
            ts_event_raw=ts_event_raw,
            ts_recv_raw=ts_recv_raw,
            _attrs_json=np.array(["{}"], dtype=object),
        )

        _, vix_cols, vix_X, vix_load_error, has_vix = _load_vix_npz(out)

        assert not has_vix, "has_vix should be False for audit-only file"
        assert vix_load_error == "no_feature_columns", (
            f"Expected vix_load_error='no_feature_columns', got {vix_load_error!r}"
        )
        assert vix_cols == []
        assert vix_X is None
