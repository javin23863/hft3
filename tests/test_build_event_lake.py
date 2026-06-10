"""Tests for scripts/build_event_lake.py and packages/data_system/src/lake_manifest.py.

Coverage
--------
- manifest-only mode rescans a tmp dir with one synthetic NPZ and rewrites
  manifest.json correctly.
- Window UTC computation for a known CSV row matches the expected timestamps.
- Skip-existing logic: a pair whose NPZ already exists (and is valid) is not
  included in the "missing" list in dry-run.
- No-network guarantee for manifest-only: DatabentoResearchClient must never
  be constructed when running --manifest-only (monkeypatched to raise).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import pytz

# ---------------------------------------------------------------------------
# Ensure repo root is on sys.path (same pattern as other tests in this repo).
# ---------------------------------------------------------------------------
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------
from data_system.src.events_parser import load_and_parse_events  # noqa: E402
from data_system.src.lake_manifest import load_manifest, MANIFEST_REL_PATH  # noqa: E402
from data_system.src.npz_resolver import npz_path_for  # noqa: E402

# The script itself lives outside packages, so we add scripts/ to sys.path
# and import the public functions directly.
_SCRIPTS = _REPO / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from build_event_lake import (  # noqa: E402
    _npz_ok,
    _verify_npz,
    run_manifest_only,
    scan_existing_npz,
)

# ---------------------------------------------------------------------------
# Real events.csv (used for window computation test)
# ---------------------------------------------------------------------------
_EVENTS_CSV = _REPO / "packages" / "data_system" / "config" / "events.csv"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_synthetic_npz(path: Path, n_rows: int = 10) -> Path:
    """Write a minimal NPZ with a 'data' key containing *n_rows* uint64 entries.

    This is lighter than build_minimal_mbo_npz (no hftbacktest backtest run)
    and is all the lake code cares about: np.load(path)['data'] with len > 0.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.arange(n_rows, dtype=np.uint64)
    np.savez_compressed(str(path), data=data)
    return path


# ---------------------------------------------------------------------------
# Test: _verify_npz accepts valid NPZ and rejects bad ones
# ---------------------------------------------------------------------------


def test_verify_npz_valid(tmp_path: Path) -> None:
    p = tmp_path / "good.npz"
    _make_synthetic_npz(p, n_rows=7)
    assert _verify_npz(p) == 7


def test_verify_npz_missing_key(tmp_path: Path) -> None:
    p = tmp_path / "bad.npz"
    np.savez_compressed(str(p), wrong_key=np.array([1, 2, 3]))
    with pytest.raises(ValueError, match="missing 'data' key"):
        _verify_npz(p)


def test_verify_npz_empty_data(tmp_path: Path) -> None:
    p = tmp_path / "empty.npz"
    np.savez_compressed(str(p), data=np.array([]))
    with pytest.raises(ValueError, match="empty"):
        _verify_npz(p)


# ---------------------------------------------------------------------------
# Test: window UTC computation for a known CSV row
# ---------------------------------------------------------------------------


def test_window_utc_for_known_cpi_row() -> None:
    """CPI_2024_09_11_TIGHT: release 2024-09-11 08:30 ET, offsets -30/+300 s.

    Expected:
        anchor UTC = 2024-09-11 12:30:00 UTC  (08:30 ET = UTC-4 in September)
        start_utc  = 2024-09-11 12:29:30 UTC
        end_utc    = 2024-09-11 12:35:00 UTC
    """
    if not _EVENTS_CSV.is_file():
        pytest.skip("events.csv not present")

    df = load_and_parse_events(str(_EVENTS_CSV))
    row = df[df["event_id"] == "CPI_2024_09_11_TIGHT"]
    assert not row.empty, "CPI_2024_09_11_TIGHT not found in events.csv"

    r = row.iloc[0]
    start_utc: datetime = r["start_utc"]
    end_utc: datetime = r["end_utc"]

    # September is EDT = UTC-4
    expected_anchor = datetime(2024, 9, 11, 12, 30, 0, tzinfo=timezone.utc)
    expected_start = datetime(2024, 9, 11, 12, 29, 30, tzinfo=timezone.utc)
    expected_end = datetime(2024, 9, 11, 12, 35, 0, tzinfo=timezone.utc)

    # Normalise to UTC for comparison regardless of tzinfo object type
    assert start_utc.astimezone(timezone.utc) == expected_start
    assert end_utc.astimezone(timezone.utc) == expected_end

    # Confirm anchor (release moment) is midway
    anchor_utc: datetime = r["anchor_utc"]
    assert anchor_utc.astimezone(timezone.utc) == expected_anchor


def test_window_utc_for_prop_flatten_topstep_row() -> None:
    """PROP_FLATTEN_TOPSTEP uses America/Chicago and has large offsets (-1500/+600 s)."""
    if not _EVENTS_CSV.is_file():
        pytest.skip("events.csv not present")

    df = load_and_parse_events(str(_EVENTS_CSV))
    row = df[df["event_id"] == "PROP_FLATTEN_TOPSTEP_2024_09_18_MAIN"]
    assert not row.empty

    r = row.iloc[0]
    start_utc: datetime = r["start_utc"].astimezone(timezone.utc)
    end_utc: datetime = r["end_utc"].astimezone(timezone.utc)

    # 2024-09-18 15:10 CDT (UTC-5) = 20:10 UTC
    # start = 20:10 - 1500 s = 20:10 - 25 min = 19:45 UTC
    # end   = 20:10 + 600 s  = 20:10 + 10 min = 20:20 UTC
    assert start_utc == datetime(2024, 9, 18, 19, 45, 0, tzinfo=timezone.utc)
    assert end_utc == datetime(2024, 9, 18, 20, 20, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Test: _npz_ok / skip-existing logic
# ---------------------------------------------------------------------------


def test_npz_ok_returns_count_for_valid_file(tmp_path: Path) -> None:
    p = tmp_path / "ok.npz"
    _make_synthetic_npz(p, n_rows=5)
    assert _npz_ok(p) == 5


def test_npz_ok_returns_none_for_absent_file(tmp_path: Path) -> None:
    p = tmp_path / "nonexistent.npz"
    assert _npz_ok(p) is None


def test_npz_ok_returns_none_for_corrupt_file(tmp_path: Path) -> None:
    p = tmp_path / "corrupt.npz"
    p.write_bytes(b"not a zip")
    assert _npz_ok(p) is None


def test_scan_existing_skips_missing_pairs(tmp_path: Path) -> None:
    """scan_existing_npz should only return records for NPZs that exist."""
    if not _EVENTS_CSV.is_file():
        pytest.skip("events.csv not present")

    df = load_and_parse_events(str(_EVENTS_CSV))

    # Pretend tmp_path is the repo_root – no NPZ files exist yet.
    records = scan_existing_npz(
        repo_root=tmp_path,
        events_df=df,
        target_symbols=("MES.v.0", "ES.v.0"),
    )
    assert records == [], "expected empty list when no NPZ files exist"


def test_scan_existing_finds_planted_npz(tmp_path: Path) -> None:
    """scan_existing_npz returns a record when an NPZ is planted at the canonical path."""
    if not _EVENTS_CSV.is_file():
        pytest.skip("events.csv not present")

    event_id = "CPI_2024_09_11_TIGHT"
    symbol = "MES.v.0"
    target_path = npz_path_for(tmp_path, event_id, symbol)
    _make_synthetic_npz(target_path, n_rows=12)

    df = load_and_parse_events(str(_EVENTS_CSV))
    records = scan_existing_npz(
        repo_root=tmp_path,
        events_df=df,
        target_symbols=(symbol,),
    )

    assert len(records) == 1
    rec = records[0]
    assert rec["event_id"] == event_id
    assert rec["symbol"] == symbol
    assert rec["event_count"] == 12
    assert rec["sha256"]  # non-empty
    assert rec["npz_path"].startswith("data/npz/")


# ---------------------------------------------------------------------------
# Test: manifest-only mode (the full run_manifest_only entrypoint)
# ---------------------------------------------------------------------------


def test_manifest_only_writes_manifest_for_one_npz(tmp_path: Path) -> None:
    """run_manifest_only should write manifest.json listing the planted NPZ."""
    if not _EVENTS_CSV.is_file():
        pytest.skip("events.csv not present")

    event_id = "NFP_2025_01_10_TIGHT"
    symbol = "ZN.v.0"
    npz = npz_path_for(tmp_path, event_id, symbol)
    _make_synthetic_npz(npz, n_rows=20)

    rc = run_manifest_only(
        repo_root=tmp_path,
        events_csv=_EVENTS_CSV,
        target_symbols=(symbol,),
        event_type_filter=None,
    )
    assert rc == 0

    manifest_path = tmp_path / MANIFEST_REL_PATH
    assert manifest_path.is_file(), "manifest.json was not written"

    records = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert any(
        r["event_id"] == event_id and r["symbol"] == symbol and r["event_count"] == 20
        for r in records
    ), f"expected record not found in manifest: {records}"


def test_manifest_only_empty_lake(tmp_path: Path) -> None:
    """run_manifest_only on an empty data dir writes an empty manifest list."""
    if not _EVENTS_CSV.is_file():
        pytest.skip("events.csv not present")

    rc = run_manifest_only(
        repo_root=tmp_path,
        events_csv=_EVENTS_CSV,
        target_symbols=("MES.v.0",),
        event_type_filter=None,
    )
    assert rc == 0
    manifest_path = tmp_path / MANIFEST_REL_PATH
    records = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert records == []


# ---------------------------------------------------------------------------
# Test: no-network guarantee – DatabentoResearchClient must NOT be constructed
# in manifest-only mode even when monkeypatched to raise on construction.
# ---------------------------------------------------------------------------


def test_manifest_only_never_constructs_databento_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Monkeypatch DatabentoResearchClient to raise; manifest-only must not hit it."""
    if not _EVENTS_CSV.is_file():
        pytest.skip("events.csv not present")

    class _NeverAllowed:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise AssertionError(
                "DatabentoResearchClient was constructed in manifest-only mode; "
                "this means a network code path was taken."
            )

    # Patch the class in the data_system module AND in build_event_lake's
    # module namespace so any lazy import inside the script is also intercepted.
    import data_system.src.databento_client as _dc_mod

    monkeypatch.setattr(_dc_mod, "DatabentoResearchClient", _NeverAllowed)

    # Also patch in the scripts module if it was already imported.
    import build_event_lake as _bel

    # The script does a lazy `from data_system.src.databento_client import
    # DatabentoResearchClient` inside _try_estimate_cost and
    # _download_and_convert.  Those code paths must not be reached in
    # manifest-only mode, so we don't need to patch the function-local import
    # directly – but we do patch the module attribute as a belt-and-suspenders
    # guard.
    monkeypatch.setattr(
        _dc_mod, "DatabentoResearchClient", _NeverAllowed, raising=True
    )

    event_id = "CPI_2025_01_15_TIGHT"
    symbol = "MES.v.0"
    npz = npz_path_for(tmp_path, event_id, symbol)
    _make_synthetic_npz(npz, n_rows=3)

    # Must complete without raising AssertionError.
    rc = run_manifest_only(
        repo_root=tmp_path,
        events_csv=_EVENTS_CSV,
        target_symbols=(symbol,),
        event_type_filter=None,
    )
    assert rc == 0
    manifest_path = tmp_path / MANIFEST_REL_PATH
    records = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(records) == 1
    assert records[0]["event_id"] == event_id


# ---------------------------------------------------------------------------
# Test: load_manifest helper from lake_manifest.py
# ---------------------------------------------------------------------------


def test_load_manifest_returns_empty_list_when_no_file(tmp_path: Path) -> None:
    result = load_manifest(tmp_path)
    assert result == []


def test_load_manifest_round_trips_records(tmp_path: Path) -> None:
    manifest_path = tmp_path / MANIFEST_REL_PATH
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    expected: list[dict[str, Any]] = [
        {
            "event_id": "CPI_2024_09_11_TIGHT",
            "symbol": "MES.v.0",
            "npz_path": "data/npz/MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz",
            "event_count": 42,
            "sha256": "abc123",
            "created_utc": "2026-01-01T00:00:00+00:00",
        }
    ]
    manifest_path.write_text(json.dumps(expected), encoding="utf-8")
    result = load_manifest(tmp_path)
    assert result == expected


# ---------------------------------------------------------------------------
# Test: event_type filter narrows the universe
# ---------------------------------------------------------------------------


def test_event_type_filter_narrows_scan(tmp_path: Path) -> None:
    """scan_existing_npz with event_type=CPI should not find NFP NPZ files."""
    if not _EVENTS_CSV.is_file():
        pytest.skip("events.csv not present")

    from build_event_lake import _load_events  # noqa: PLC0415

    # Plant an NFP NPZ
    nfp_id = "NFP_2025_01_10_TIGHT"
    sym = "MES.v.0"
    npz = npz_path_for(tmp_path, nfp_id, sym)
    _make_synthetic_npz(npz)

    # Load with CPI filter – should see 0 records for the planted NFP file
    df_cpi = _load_events(_EVENTS_CSV, event_type_filter="CPI")
    records = scan_existing_npz(
        repo_root=tmp_path,
        events_df=df_cpi,
        target_symbols=(sym,),
    )
    ids = {r["event_id"] for r in records}
    assert nfp_id not in ids, "NFP event leaked through CPI filter"
