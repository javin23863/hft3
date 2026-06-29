"""Unit tests for research_pipeline.data_quality.check_npz_ohlcv."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from research_pipeline.data_quality import check_npz_ohlcv, NoOHLCVDataError

# MBO event dtype matching hftbacktest structured array format
_MBO_DTYPE = np.dtype([
    ("ev", "<i8"),
    ("local_ts", "<i8"),
    ("px", "<f8"),
    ("qty", "<f8"),
    ("order_id", "<i8"),
])


def _write_mbo_npz(path: Path, n_events: int) -> None:
    """Write a valid MBO NPZ with n_events rows."""
    data = np.zeros(n_events, dtype=_MBO_DTYPE)
    data["ev"] = 1
    data["local_ts"] = np.arange(n_events, dtype=np.int64) * 60_000_000_000
    data["px"] = 100.0
    data["qty"] = 1.0
    data["order_id"] = np.arange(n_events, dtype=np.int64)
    np.savez(path, data=data)


def test_valid_mbo_npz(tmp_path: Path) -> None:
    """A well-formed NPZ with >=2 MBO events passes."""
    npz = tmp_path / "valid_mbo.npz"
    _write_mbo_npz(npz, 10)
    ok, reason = check_npz_ohlcv(npz)
    assert ok is True
    assert reason == ""


def test_missing_file(tmp_path: Path) -> None:
    """A non-existent file returns (False, missing_npz)."""
    ok, reason = check_npz_ohlcv(tmp_path / "nonexistent.npz")
    assert ok is False
    assert reason == "missing_npz"


def test_empty_data_array(tmp_path: Path) -> None:
    """An NPZ with an empty data array is invalid (insufficient_events)."""
    npz = tmp_path / "empty.npz"
    data = np.zeros(0, dtype=_MBO_DTYPE)
    np.savez(npz, data=data)
    ok, reason = check_npz_ohlcv(npz)
    assert ok is False
    assert reason == "insufficient_events"


def test_single_event(tmp_path: Path) -> None:
    """An NPZ with only 1 event cannot build a bar (insufficient_events)."""
    npz = tmp_path / "one_event.npz"
    _write_mbo_npz(npz, 1)
    ok, reason = check_npz_ohlcv(npz)
    assert ok is False
    assert reason == "insufficient_events"


def test_missing_data_member(tmp_path: Path) -> None:
    """An NPZ without a 'data' member is invalid."""
    npz = tmp_path / "no_data.npz"
    np.savez(npz, other=np.array([1, 2, 3]))
    ok, reason = check_npz_ohlcv(npz)
    assert ok is False
    assert "missing_data_array" in reason


def test_missing_required_fields(tmp_path: Path) -> None:
    """An NPZ whose data array lacks required fields is invalid."""
    npz = tmp_path / "missing_fields.npz"
    bad_dtype = np.dtype([("ev", "<i8"), ("local_ts", "<i8")])
    data = np.zeros(5, dtype=bad_dtype)
    np.savez(npz, data=data)
    ok, reason = check_npz_ohlcv(npz)
    assert ok is False
    assert "missing_fields" in reason


def test_prebuilt_ohlcv_arrays(tmp_path: Path) -> None:
    """An NPZ with open/high/low/close/volume arrays passes."""
    npz = tmp_path / "ohlcv.npz"
    n = 100
    np.savez(
        npz,
        open=np.zeros(n),
        high=np.zeros(n),
        low=np.zeros(n),
        close=np.zeros(n),
        volume=np.zeros(n),
    )
    ok, reason = check_npz_ohlcv(npz)
    assert ok is True


def test_empty_ohlcv_array(tmp_path: Path) -> None:
    """An NPZ with empty OHLCV arrays is invalid."""
    npz = tmp_path / "empty_ohlcv.npz"
    np.savez(
        npz,
        open=np.array([]),
        high=np.array([]),
        low=np.array([]),
        close=np.array([]),
        volume=np.array([]),
    )
    ok, reason = check_npz_ohlcv(npz)
    assert ok is False
    assert "no_ohlcv_data" in reason


def test_corrupt_file_raises(tmp_path: Path) -> None:
    """A corrupt NPZ file raises NoOHLCVDataError, not swallowed."""
    npz = tmp_path / "corrupt.npz"
    npz.write_bytes(b"not a valid zip/npz file")
    with pytest.raises(NoOHLCVDataError):
        check_npz_ohlcv(npz)