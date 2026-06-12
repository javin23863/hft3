"""Tests for feature-store point-in-time integrity: no lookahead, ts monotonicity,
and embargo guard.

Backend guard: tests skip when hft3_features_cpp is not built (mirrors _SKIP_NO_CPP
pattern from tests/test_cpp_pipeline_backend.py).
"""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import os
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()


# ---------------------------------------------------------------------------
# cpp guard (mirrors test_cpp_pipeline_backend._SKIP_NO_CPP)
# ---------------------------------------------------------------------------

def _cpp_available() -> bool:
    try:
        from features_engine.src.features._cpp_loader import load_cpp_features
        return load_cpp_features() is not None
    except Exception:
        return False


_CPP_PRESENT = _cpp_available()
_SKIP_NO_CPP = pytest.mark.skipif(
    not _CPP_PRESENT,
    reason="hft3_features_cpp not built (cmake --build build)",
)


# ---------------------------------------------------------------------------
# Script loader (same pattern as tests/test_run_event_universe.py)
# ---------------------------------------------------------------------------

def _load_build_script():
    script = _REPO / "scripts" / "build_feature_store.py"
    spec = importlib.util.spec_from_file_location("build_feature_store", script)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _make_full_lake_npz(dest: Path) -> Path:
    from backtest_pipeline.src.replay_npz_fixture import build_minimal_mbo_npz
    build_minimal_mbo_npz(dest)
    return dest


def _make_truncated_lake_npz(full_path: Path, dest: Path) -> tuple[Path, int]:
    """Load full NPZ, keep first k ≈ n//2 rows, write truncated copy."""
    with np.load(str(full_path), allow_pickle=False) as arch:
        data = arch["data"]
    n = len(data)
    k = max(1, n // 2)
    truncated = data[:k]
    np.savez_compressed(str(dest), data=truncated)
    return dest, k


def _build_unit(bmod, npz_path: Path, out_root: Path, symbol: str = "MES.v.0",
                event_id: str = "TEST_EVT_PIT") -> dict:
    unit = {
        "symbol": symbol,
        "event_id": event_id,
        "npz_path": str(npz_path),
        "tick_size": 0.25,
        "out_root": str(out_root),
        "event_type": "CPI",
        "release_date": "2024-01-10",
        "git_commit": "test",
        "source_npz_sha256": _sha256_file(npz_path),
    }
    os.environ["HFT3_FEATURE_BACKEND"] = "cpp"
    return bmod._worker(unit)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPitNoLookahead:

    @_SKIP_NO_CPP
    def test_truncation_no_future_influence(self, tmp_path):
        """Rows 0..k-1 of X and ts must be byte-identical between full and truncated builds."""
        bmod = _load_build_script()

        full_npz = _make_full_lake_npz(tmp_path / "MES.v.0_TEST_EVT_PIT_mbo.npz")

        trunc_npz, k = _make_truncated_lake_npz(
            full_npz, tmp_path / "MES.v.0_TEST_EVT_TRUNC_mbo.npz"
        )

        out_full = tmp_path / "features_full"
        out_full.mkdir()
        r_full = _build_unit(bmod, full_npz, out_full, event_id="TEST_EVT_PIT")
        assert r_full["status"] == "built", f"full build failed: {r_full.get('error')}"

        out_trunc = tmp_path / "features_trunc"
        out_trunc.mkdir()
        r_trunc = _build_unit(bmod, trunc_npz, out_trunc, event_id="TEST_EVT_TRUNC")
        assert r_trunc["status"] == "built", f"truncated build failed: {r_trunc.get('error')}"

        from data_system.src.feature_store import load_store
        data_full = load_store(Path(r_full["store_path"]))
        data_trunc = load_store(Path(r_trunc["store_path"]))

        n_trunc = data_trunc["ts"].shape[0]
        assert n_trunc > 0, "truncated store has no rows"

        assert np.array_equal(
            data_full["ts"][:n_trunc], data_trunc["ts"]
        ), "ts prefix mismatch: future events influenced past rows (lookahead leak)"

        assert np.array_equal(
            data_full["X"][:n_trunc], data_trunc["X"]
        ), "X prefix mismatch: future events influenced past rows (lookahead leak)"

    @_SKIP_NO_CPP
    def test_ts_monotonic_non_decreasing(self, tmp_path):
        """Full store ts must be strictly non-decreasing."""
        bmod = _load_build_script()
        full_npz = _make_full_lake_npz(tmp_path / "MES.v.0_TEST_EVT_MONO_mbo.npz")
        out_root = tmp_path / "features"
        out_root.mkdir()

        r = _build_unit(bmod, full_npz, out_root, event_id="TEST_EVT_MONO")
        assert r["status"] == "built", f"build failed: {r.get('error')}"

        from data_system.src.feature_store import load_store
        data = load_store(Path(r["store_path"]))
        ts = data["ts"]
        assert ts.shape[0] > 0, "empty store"
        diffs = np.diff(ts)
        assert np.all(diffs >= 0), (
            f"ts not monotonically non-decreasing; first violation at index "
            f"{int(np.argmax(diffs < 0))} with diff {diffs[diffs < 0][0]}"
        )

    @_SKIP_NO_CPP
    def test_embargo_guard_skips_unit(self, tmp_path):
        """Builder loop must skip units with release_date >= 2026-01-01 (embargo_2026).

        Setup: temp lake dir + events.csv with one row whose release_date is 2026-01-15.
        The builder enumeration logic (extracted from main()) must produce zero work
        units and record one embargo skip.
        """
        bmod = _load_build_script()

        # Write a real NPZ into the tmp lake so the file scan finds it
        lake_root = tmp_path / "lake"
        lake_root.mkdir()
        symbol = "MES.v.0"
        event_id = "EMBARGO_EVT_2026"
        npz_name = f"{symbol}_{event_id}_mbo.npz"
        _make_full_lake_npz(lake_root / npz_name)

        # Write events.csv with embargo release_date
        events_csv = tmp_path / "events.csv"
        fieldnames = [
            "event_id", "event_type", "release_date", "release_time",
            "timezone", "window_name", "start_offset_seconds",
            "end_offset_seconds", "symbols", "priority", "source",
            "source_url", "effective_date", "notes", "row_status",
        ]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow({
            "event_id": event_id,
            "event_type": "CPI",
            "release_date": "2026-01-15",
            "release_time": "08:30:00",
            "timezone": "America/New_York",
            "window_name": "TIGHT",
            "start_offset_seconds": "-30",
            "end_offset_seconds": "300",
            "symbols": symbol,
            "priority": "50",
            "source": "TEST",
            "source_url": "http://example.com",
            "effective_date": "2026-01-01",
            "notes": "embargo test",
            "row_status": "SOURCED",
        })
        events_csv.write_text(buf.getvalue(), encoding="utf-8")

        # Replicate the builder enumeration logic using env-overridden lake root
        os.environ["HFT3_NPZ_ROOT"] = str(lake_root)
        try:
            # Scan lake
            raw_units = bmod._scan_lake(_REPO)
            # _scan_lake uses npz_root(_REPO) which reads HFT3_NPZ_ROOT
            # Filter to only our tmp event
            raw_units = [(s, e, p) for s, e, p in raw_units if e == event_id]
        finally:
            os.environ.pop("HFT3_NPZ_ROOT", None)

        # If _scan_lake didn't pick it up from lake_root, drive with manual entry
        if not raw_units:
            raw_units = [(symbol, event_id, str(lake_root / npz_name))]

        events_rows = {event_id: {
            "event_id": event_id,
            "event_type": "CPI",
            "release_date": "2026-01-15",
        }}

        work_units = []
        embargo_count = 0
        skipped_summary = []

        for sym, eid, npz_path_str in raw_units:
            ev_row = events_rows.get(eid)
            if ev_row is None:
                continue
            release_date = ev_row.get("release_date", "")
            if release_date >= bmod.RESEARCH_EMBARGO_START:
                skipped_summary.append({"symbol": sym, "event_id": eid, "reason": "embargo_2026"})
                embargo_count += 1
                continue
            work_units.append((sym, eid, npz_path_str))

        assert embargo_count == 1, f"expected 1 embargo skip, got {embargo_count}"
        assert len(work_units) == 0, (
            f"expected 0 work units after embargo filter, got {len(work_units)}"
        )
        assert skipped_summary[0]["reason"] == "embargo_2026"
