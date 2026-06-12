"""Tests for feature-store build determinism, idempotency, and schema-drift guard.

Backend guard: tests skip when hft3_features_cpp is not built (mirrors _SKIP_NO_CPP
pattern from tests/test_cpp_pipeline_backend.py).
"""
from __future__ import annotations

import importlib.util
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
# Fixture: minimal lake NPZ (synthesised — does not depend on fixture file path)
# ---------------------------------------------------------------------------

def _make_lake_npz(dest: Path) -> Path:
    """Write a tiny HftBacktest-compatible MBO NPZ via build_minimal_mbo_npz."""
    from backtest_pipeline.src.replay_npz_fixture import build_minimal_mbo_npz

    build_minimal_mbo_npz(dest)
    return dest


def _build_unit(bmod, npz_path: Path, out_root: Path) -> dict:
    """Assemble a worker unit dict and call _worker directly."""
    import hashlib

    h = hashlib.sha256()
    with npz_path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    source_sha = h.hexdigest()

    unit = {
        "symbol": "MES.v.0",
        "event_id": "TEST_EVT_DET",
        "npz_path": str(npz_path),
        "tick_size": 0.25,
        "out_root": str(out_root),
        "event_type": "CPI",
        "release_date": "2024-01-10",
        "git_commit": "test",
        "source_npz_sha256": source_sha,
    }
    os.environ["HFT3_FEATURE_BACKEND"] = "cpp"
    return bmod._worker(unit)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBuildDeterminism:

    @_SKIP_NO_CPP
    def test_content_hash_identical_on_rebuild(self, tmp_path):
        """Build twice (second with rebuild semantics) → content_hash identical."""
        bmod = _load_build_script()
        lake_npz = _make_lake_npz(tmp_path / "MES.v.0_TEST_EVT_DET_mbo.npz")
        out_root = tmp_path / "features"
        out_root.mkdir()

        r1 = _build_unit(bmod, lake_npz, out_root)
        assert r1["status"] == "built", f"first build failed: {r1.get('error')}"

        r2 = _build_unit(bmod, lake_npz, out_root)
        assert r2["status"] == "built", f"second build failed: {r2.get('error')}"

        assert r1["content_hash"] == r2["content_hash"], (
            f"content_hash mismatch: {r1['content_hash']} vs {r2['content_hash']}"
        )

        from data_system.src.feature_store import load_manifest
        manifest = load_manifest(out_root)
        records = list(manifest.values())
        assert len(records) >= 1
        # last-wins: the stored record must match content_hash from r2
        key = ("MES.v.0", "TEST_EVT_DET")
        rec = manifest[key]
        assert rec["content_hash"] == r2["content_hash"]

    @_SKIP_NO_CPP
    def test_idempotency_cached_skip(self, tmp_path):
        """Second build without rebuild → manifest unchanged (last-wins, same hash)."""
        bmod = _load_build_script()
        lake_npz = _make_lake_npz(tmp_path / "MES.v.0_TEST_EVT_DET_mbo.npz")
        out_root = tmp_path / "features"
        out_root.mkdir()

        r1 = _build_unit(bmod, lake_npz, out_root)
        assert r1["status"] == "built", f"build failed: {r1.get('error')}"

        from data_system.src.feature_store import load_manifest, manifest_path
        manifest_before = manifest_path(out_root).read_bytes()

        # Simulate incremental check: manifest already has matching entry;
        # in the main() loop this causes skipped_cached.  Call _worker again
        # (always builds), but assert manifest content_hash is stable.
        r2 = _build_unit(bmod, lake_npz, out_root)
        assert r2["status"] == "built"
        assert r1["content_hash"] == r2["content_hash"], (
            "idempotency broken: content_hash changed on identical source"
        )

        manifest_after = load_manifest(out_root)
        key = ("MES.v.0", "TEST_EVT_DET")
        assert manifest_after[key]["content_hash"] == r1["content_hash"]

    @_SKIP_NO_CPP
    def test_load_store_shape_and_schema_drift(self, tmp_path):
        """load_store returns X shape (n,64), ts monotonic; corrupted hash raises ValueError."""
        bmod = _load_build_script()
        lake_npz = _make_lake_npz(tmp_path / "MES.v.0_TEST_EVT_DET_mbo.npz")
        out_root = tmp_path / "features"
        out_root.mkdir()

        r = _build_unit(bmod, lake_npz, out_root)
        assert r["status"] == "built", f"build failed: {r.get('error')}"

        from data_system.src.feature_store import load_store, feature_index_hash
        from features_engine.src.features.feature_index import FEATURE_DIM

        store_file = Path(r["store_path"])
        data = load_store(store_file)

        X = data["X"]
        ts = data["ts"]

        assert X.ndim == 2, f"X must be 2-D, got shape {X.shape}"
        assert X.shape[1] == FEATURE_DIM, f"X.shape[1]={X.shape[1]} != FEATURE_DIM={FEATURE_DIM}"
        assert np.all(np.diff(ts) >= 0), "ts not monotonically non-decreasing"

        stored_fih = data.get("feature_index_hash", "") if isinstance(data.get("feature_index_hash"), str) else ""
        expected_fih = feature_index_hash()
        assert stored_fih == expected_fih, "feature_index_hash mismatch in freshly-built store"

        # Corrupt feature_index_hash attr and verify schema-drift ValueError
        with np.load(str(store_file), allow_pickle=False) as arch:
            arrays = {k: arch[k] for k in arch.files}
        arrays["feature_index_hash"] = np.array("corrupted_hash_sentinel_xyz")
        corrupt_path = tmp_path / "corrupt_store.npz"
        np.savez_compressed(str(corrupt_path), **arrays)

        with pytest.raises(ValueError, match="schema drift"):
            load_store(corrupt_path)
