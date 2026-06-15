"""Tests for scripts/run_event_universe.py.

Covers:
  - work-unit collection from a synthetic manifest + tmp NPZ
  - single-worker end-to-end smoke over 1 event × 1 band × active hypotheses
  - aggregation math on hand-built per-event results
  - p-value / correction plumbing with fabricated per-event expectancies

Multiprocessing: all tests use workers=1 to avoid spawn overhead in CI.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------

def _load_script(name: str = "run_event_universe"):
    script = _REPO / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def universe_mod():
    return _load_script()


# ---------------------------------------------------------------------------
# Minimal events.csv fixture
# ---------------------------------------------------------------------------

_EVENTS_CSV_CONTENT = """\
event_id,event_type,release_date,release_time,timezone,window_name,start_offset_seconds,end_offset_seconds,symbols,priority,source,source_url,effective_date,notes,row_status
AAA_EVT_A,CPI,2024-01-10,08:30:00,America/New_York,TIGHT,-30,300,"MES.v.0",50,TEST,http://example.com,2024-01-01,test,SOURCED
BBB_EVT_B,NFP,2024-02-02,08:30:00,America/New_York,TIGHT,-30,300,"MES.v.0",50,TEST,http://example.com,2024-01-01,test,SOURCED
ZZZ_NO_NPZ,CPI,2024-03-13,08:30:00,America/New_York,TIGHT,-30,300,"MES.v.0",50,TEST,http://example.com,2024-01-01,test,SOURCED
"""


@pytest.fixture()
def events_csv(tmp_path: Path) -> Path:
    p = tmp_path / "events.csv"
    p.write_text(_EVENTS_CSV_CONTENT, encoding="utf-8")
    return p


@pytest.fixture()
def minimal_npz(tmp_path: Path) -> Path:
    """Build a minimal MBO NPZ that HftBacktest can replay."""
    from backtest_pipeline.src.replay_npz_fixture import build_minimal_mbo_npz

    npz = tmp_path / "MES.v.0_AAA_EVT_A_mbo.npz"
    build_minimal_mbo_npz(npz)
    return npz


@pytest.fixture()
def minimal_npz_b(tmp_path: Path) -> Path:
    """Second event NPZ."""
    from backtest_pipeline.src.replay_npz_fixture import build_minimal_mbo_npz

    npz = tmp_path / "MES.v.0_BBB_EVT_B_mbo.npz"
    build_minimal_mbo_npz(npz)
    return npz


# ---------------------------------------------------------------------------
# 1. Work-unit collection
# ---------------------------------------------------------------------------

class TestBuildWorkUnits:
    def test_basic_collection(self, universe_mod, events_csv, minimal_npz, tmp_path):
        lake_index = {("MES.v.0", "AAA_EVT_A"): str(minimal_npz)}
        work, skipped = universe_mod.build_work_units(
            events_csv,
            lake_index,
            latency_bands=[1.0],
            event_type_filter=None,
            symbol_filter=["MES.v.0"],
            max_events=None,
        )
        event_ids = {u["event_id"] for u in work}
        assert "AAA_EVT_A" in event_ids
        # BBB_EVT_B and ZZZ_NO_NPZ have no NPZ — should be skipped
        skipped_ids = {s["event_id"] for s in skipped}
        assert "BBB_EVT_B" in skipped_ids
        assert "ZZZ_NO_NPZ" in skipped_ids

    def test_skipped_has_reason(self, universe_mod, events_csv, minimal_npz):
        lake_index = {("MES.v.0", "AAA_EVT_A"): str(minimal_npz)}
        _, skipped = universe_mod.build_work_units(
            events_csv,
            lake_index,
            latency_bands=[1.0],
            event_type_filter=None,
            symbol_filter=["MES.v.0"],
            max_events=None,
        )
        for s in skipped:
            assert s["reason"] == "npz_missing"
            assert s["event_type"] in {"CPI", "NFP"}
            assert s["release_date"] in {"2024-02-02", "2024-03-13"}
            assert s["symbol"] == "MES.v.0"
            assert s["latency_ms"] == 1.0

    def test_q001_no_market_window_skipped_with_accepted_reason(
        self, universe_mod, tmp_path, minimal_npz
    ):
        p = tmp_path / "q001_no_market_events.csv"
        p.write_text(
            "event_id,event_type,release_date,release_time,timezone,window_name,start_offset_seconds,"
            "end_offset_seconds,symbols,priority,source,source_url,effective_date,notes,row_status\n"
            "EIA_CRUDE_2024_12_25_TIGHT,EIA_CRUDE,2024-12-25,10:30:00,America/New_York,TIGHT,"
            "-30,300,\"MES.v.0\",50,TEST,http://example.com,2024-01-01,test,SOURCED\n",
            encoding="utf-8",
        )
        work, skipped = universe_mod.build_work_units(
            p,
            {("MES.v.0", "EIA_CRUDE_2024_12_25_TIGHT"): str(minimal_npz)},
            latency_bands=[1.0],
            event_type_filter=None,
            symbol_filter=["MES.v.0"],
            max_events=None,
        )
        assert work == []
        assert skipped == [{
            "event_id": "EIA_CRUDE_2024_12_25_TIGHT",
            "event_type": "EIA_CRUDE",
            "release_date": "2024-12-25",
            "symbol": "MES.v.0",
            "latency_ms": 1.0,
            "reason": "no_market_data",
        }]

    def test_q001_partial_window_skips_only_missing_symbols(
        self, universe_mod, tmp_path, minimal_npz
    ):
        p = tmp_path / "q001_partial_events.csv"
        p.write_text(
            "event_id,event_type,release_date,release_time,timezone,window_name,start_offset_seconds,"
            "end_offset_seconds,symbols,priority,source,source_url,effective_date,notes,row_status\n"
            "FED_H41_2024_06_19_TIGHT,FED_H41,2024-06-19,16:30:00,America/New_York,TIGHT,"
            "-30,300,\"MES.v.0,ES.v.0\",50,TEST,http://example.com,2024-01-01,test,SOURCED\n",
            encoding="utf-8",
        )
        lake_index = {
            ("MES.v.0", "FED_H41_2024_06_19_TIGHT"): str(minimal_npz),
            ("ES.v.0", "FED_H41_2024_06_19_TIGHT"): str(minimal_npz),
        }
        work, skipped = universe_mod.build_work_units(
            p,
            lake_index,
            latency_bands=[1.0],
            event_type_filter=None,
            symbol_filter=["MES.v.0", "ES.v.0"],
            max_events=None,
        )
        assert [(u["event_id"], u["symbol"]) for u in work] == [
            ("FED_H41_2024_06_19_TIGHT", "MES.v.0")
        ]
        assert skipped == [{
            "event_id": "FED_H41_2024_06_19_TIGHT",
            "event_type": "FED_H41",
            "release_date": "2024-06-19",
            "symbol": "ES.v.0",
            "latency_ms": 1.0,
            "reason": "symbol_absent_in_raw_after_redownload",
        }]

    @pytest.mark.parametrize("manifest_text", [None, "[]"])
    def test_default_q001_manifest_unavailable_fails_closed(
        self, universe_mod, events_csv, minimal_npz, tmp_path, manifest_text
    ):
        manifest = tmp_path / "missing_or_malformed_manifest.json"
        if manifest_text is not None:
            manifest.write_text(manifest_text, encoding="utf-8")
        with mock.patch.object(universe_mod, "Q001_MBO_PILOT_MANIFEST", manifest):
            with pytest.raises(RuntimeError, match="Q001 MBO pilot manifest"):
                universe_mod.build_work_units(
                    events_csv,
                    {("MES.v.0", "AAA_EVT_A"): str(minimal_npz)},
                    latency_bands=[1.0],
                    event_type_filter=None,
                    symbol_filter=["MES.v.0"],
                    max_events=None,
                )

    def test_event_type_filter(self, universe_mod, events_csv, minimal_npz, tmp_path):
        from backtest_pipeline.src.replay_npz_fixture import build_minimal_mbo_npz
        npz_b = tmp_path / "MES.v.0_BBB_EVT_B_mbo.npz"
        build_minimal_mbo_npz(npz_b)
        lake_index = {
            ("MES.v.0", "AAA_EVT_A"): str(minimal_npz),
            ("MES.v.0", "BBB_EVT_B"): str(npz_b),
        }
        work, _ = universe_mod.build_work_units(
            events_csv,
            lake_index,
            latency_bands=[1.0],
            event_type_filter="CPI",
            symbol_filter=["MES.v.0"],
            max_events=None,
        )
        assert all(u["event_type"] == "CPI" for u in work)
        assert any(u["event_id"] == "AAA_EVT_A" for u in work)
        assert not any(u["event_id"] == "BBB_EVT_B" for u in work)

    def test_max_events(self, universe_mod, events_csv, minimal_npz, tmp_path):
        from backtest_pipeline.src.replay_npz_fixture import build_minimal_mbo_npz
        npz_b = tmp_path / "MES.v.0_BBB_EVT_B_mbo.npz"
        npz_c = tmp_path / "MES.v.0_ZZZ_NO_NPZ_mbo.npz"
        build_minimal_mbo_npz(npz_b)
        build_minimal_mbo_npz(npz_c)
        lake_index = {
            ("MES.v.0", "AAA_EVT_A"): str(minimal_npz),
            ("MES.v.0", "BBB_EVT_B"): str(npz_b),
            ("MES.v.0", "ZZZ_NO_NPZ"): str(npz_c),
        }
        work, skipped = universe_mod.build_work_units(
            events_csv,
            lake_index,
            latency_bands=[1.0],
            event_type_filter=None,
            symbol_filter=["MES.v.0"],
            max_events=1,
        )
        # max_events=1 caps the rows consumed before NPZ matching
        total = len(work) + len(skipped)
        assert total == 1  # 1 event_row × 1 band × 1 symbol

    def test_multiple_bands_produce_multiple_units(self, universe_mod, events_csv, minimal_npz):
        lake_index = {("MES.v.0", "AAA_EVT_A"): str(minimal_npz)}
        work, _ = universe_mod.build_work_units(
            events_csv,
            lake_index,
            latency_bands=[0.5, 1.0, 2.0],
            event_type_filter="CPI",
            symbol_filter=["MES.v.0"],
            max_events=None,
        )
        bands = [u["latency_ms"] for u in work if u["event_id"] == "AAA_EVT_A"]
        assert sorted(bands) == [0.5, 1.0, 2.0]

    def test_deterministic_order(self, universe_mod, events_csv, minimal_npz):
        lake_index = {("MES.v.0", "AAA_EVT_A"): str(minimal_npz)}
        work1, _ = universe_mod.build_work_units(
            events_csv,
            lake_index,
            latency_bands=[1.0, 0.5],
            event_type_filter=None,
            symbol_filter=["MES.v.0"],
            max_events=None,
        )
        work2, _ = universe_mod.build_work_units(
            events_csv,
            lake_index,
            latency_bands=[0.5, 1.0],
            event_type_filter=None,
            symbol_filter=["MES.v.0"],
            max_events=None,
        )
        ids1 = [(u["event_id"], u["latency_ms"]) for u in work1]
        ids2 = [(u["event_id"], u["latency_ms"]) for u in work2]
        assert ids1 == ids2


# ---------------------------------------------------------------------------
# 2. NPZ discovery fallback
# ---------------------------------------------------------------------------

class TestLakeIndex:
    def test_scan_fallback_parses_npz_names(self, universe_mod, tmp_path):
        npz_dir = tmp_path / "npz"
        npz_dir.mkdir()
        (npz_dir / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz").touch()
        (npz_dir / "MNQ.v.0_NFP_2025_01_10_TIGHT_mbo.npz").touch()
        (npz_dir / "garbage.npz").touch()

        result = universe_mod._scan_npz_dir(npz_dir)
        assert ("MES.v.0", "CPI_2024_09_11_TIGHT") in result
        assert ("MNQ.v.0", "NFP_2025_01_10_TIGHT") in result
        # 'garbage' doesn't match the pattern
        assert all("garbage" not in k[1] for k in result)

    def test_load_lake_index_falls_back_gracefully(self, universe_mod, tmp_path):
        """load_lake_index with a repo root that has no manifest or npz dir returns empty dict."""
        result = universe_mod.load_lake_index(tmp_path)
        assert isinstance(result, dict)

    def test_load_lake_index_rescan_bypasses_manifest(
        self, universe_mod, tmp_path, monkeypatch
    ):
        """rescan=True must skip the manifest and scan the NPZ dir directly."""
        import json

        import numpy as np

        # Clear HFT3_NPZ_ROOT so the lake root resolves to tmp_path/data/npz.
        monkeypatch.delenv("HFT3_NPZ_ROOT", raising=False)

        # Plant a manifest that lists a *different* NPZ than what's on disk.
        # Without rescan, load_lake_index would return the manifest entry.
        # With rescan, it must return only what's on disk.
        npz_dir = tmp_path / "data" / "npz"
        npz_dir.mkdir(parents=True)
        npz_file = npz_dir / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz"
        data = np.arange(5, dtype=np.uint64)
        np.savez_compressed(str(npz_file), data=data)

        # Write a manifest with a ghost entry that does NOT exist on disk
        manifest_path = npz_dir / "manifest.json"
        ghost_record = [{
            "event_id": "GHOST_2099_01_01_TIGHT",
            "symbol": "MES.v.0",
            "npz_path": "data/npz/MES.v.0_GHOST_2099_01_01_TIGHT_mbo.npz",
            "event_count": 99,
            "sha256": "deadbeef",
            "created_utc": "2026-01-01T00:00:00+00:00",
        }]
        manifest_path.write_text(json.dumps(ghost_record), encoding="utf-8")

        # Without rescan: manifest is loaded → only ghost entry
        result_manifest = universe_mod.load_lake_index(tmp_path, rescan=False)
        assert ("MES.v.0", "GHOST_2099_01_01_TIGHT") in result_manifest
        assert ("MES.v.0", "CPI_2024_09_11_TIGHT") not in result_manifest

        # With rescan: NPZ dir is scanned → only the disk file
        result_scan = universe_mod.load_lake_index(tmp_path, rescan=True)
        assert ("MES.v.0", "CPI_2024_09_11_TIGHT") in result_scan
        assert ("MES.v.0", "GHOST_2099_01_01_TIGHT") not in result_scan


# ---------------------------------------------------------------------------
# 3. End-to-end smoke: 1 event × 1 band, workers=1
# ---------------------------------------------------------------------------

def _load_fresh_universe_mod(name: str):
    """Load run_event_universe as a fresh module instance for isolation."""
    script = _REPO / "scripts" / "run_event_universe.py"
    spec = importlib.util.spec_from_file_location(name, script)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _fake_hyp_results_for(hypotheses, npz_path: str, *, latency_ms: float = 1.0, **kw) -> dict:
    """Stub for run_all_hypotheses_replay — returns instant BacktestResult per hypothesis."""
    from backtest_pipeline.src.backtest_result import BacktestResult

    return {
        h.hyp_id: BacktestResult(
            hypothesis_id=h.hyp_id,
            net_pnl=0.1 * h.hyp_id,
            num_trades=2,
            win_rate=0.6,
            expectancy=0.05,
            adverse_selection_ticks=0.1,
            tail_loss=-0.02,
        )
        for h in hypotheses
    }


def _checkpoint_row(
    event_id: str,
    *,
    npz_path: str | None = None,
    npz_fingerprint: dict[str, Any] | None = None,
    event_type: str = "CPI",
    release_date: str = "2024-01-10",
    event_fingerprint: str | None = None,
    hyp_ids: list[int] | None = None,
    expected_hypothesis_ids: list[int] | None = None,
    sensor_feature_fingerprints: dict[str, Any] | None = None,
    error: str | None = None,
    skip_reason: str | None = None,
    hypotheses: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    row_npz_path = npz_path or f"/fake/{event_id}.npz"
    return {
        "run_id": f"run_{event_id}",
        "event_id": event_id,
        "symbol": "MES.v.0",
        "npz_path": row_npz_path,
        "npz_fingerprint": npz_fingerprint or {
            "path": row_npz_path,
            "size": 1000,
            "mtime_ns": 123456789,
            "sha256": f"sha:{event_id}:v1",
        },
        "sensor_feature_fingerprints": sensor_feature_fingerprints or {},
        "latency_ms": 1.0,
        "event_type": event_type,
        "release_date": release_date,
        "event_fingerprint": event_fingerprint or f"event-fp:{event_id}:v1",
        "hyp_ids": hyp_ids,
        "expected_hypothesis_ids": expected_hypothesis_ids or [1],
        "elapsed_s": 0.01,
        "error": error,
        "skip_reason": skip_reason,
        "hypotheses": hypotheses if hypotheses is not None else ([] if skip_reason or error else [{
            "hypothesis_id": 1,
            "hypothesis_name": "cached_hyp",
            "net_pnl_usd": 0.1,
            "num_trades": 2,
            "win_rate": 0.6,
            "expectancy_usd": 0.05,
            "adverse_selection_ticks": 0.1,
            "tail_loss_usd": -0.02,
            "fee_per_round_trip_usd": 1.24,
            "tick_value_usd": 1.25,
        }]),
    }


class TestResumeCheckpoint:
    def _unit(self, event_id: str, *, npz_path: str | None = None) -> dict[str, Any]:
        unit_npz_path = npz_path or f"/fake/{event_id}.npz"
        return {
            "event_id": event_id,
            "symbol": "MES.v.0",
            "npz_path": unit_npz_path,
            "npz_fingerprint": {
                "path": unit_npz_path,
                "size": 1000,
                "mtime_ns": 123456789,
                "sha256": f"sha:{event_id}:v1",
            },
            "sensor_feature_fingerprints": {},
            "latency_ms": 1.0,
            "event_type": "CPI",
            "release_date": "2024-01-10",
            "event_fingerprint": f"event-fp:{event_id}:v1",
            "hyp_ids": None,
            "expected_hypothesis_ids": [1],
        }

    def test_checkpoint_source_hashes_include_replay_modules(self, universe_mod):
        hashes = universe_mod._checkpoint_source_hashes()

        assert "packages/replay/replay_session.py" in hashes
        assert "packages/execution/adapter_factory.py" in hashes
        assert len(hashes["packages/replay/replay_session.py"]) == 64

    def test_embargoed_rows_do_not_resolve_sensor_features(self, universe_mod, tmp_path, monkeypatch):
        events = tmp_path / "events.csv"
        events.write_text(
            "event_id,event_type,release_date,release_time,timezone,window_name,start_offset_seconds,end_offset_seconds,symbols,priority,source,source_url,effective_date,notes,row_status\n"
            "FUTURE_EVT,CPI,2026-01-02,08:30:00,America/New_York,TIGHT,-30,300,\"MES.v.0\",50,TEST,http://example.com,2024-01-01,test,SOURCED\n",
            encoding="utf-8",
        )

        def _boom(_event_id):
            raise AssertionError("sensor lookup must not touch embargoed rows")

        monkeypatch.setattr(universe_mod, "_sensor_feature_fingerprints", _boom)
        work_units, skipped = universe_mod.build_work_units(
            events,
            {("MES.v.0", "FUTURE_EVT"): "/must/not/hash.npz"},
            latency_bands=[1.0],
            event_type_filter=None,
            symbol_filter=["MES.v.0"],
            max_events=None,
        )

        assert work_units == []
        assert skipped[0]["reason"] == "embargo_2026"

    def test_checkpoint_loader_reuses_only_current_non_errored_rows(self, universe_mod, tmp_path):
        checkpoint = tmp_path / "unit_results.jsonl"
        ok_row = _checkpoint_row("AAA_EVT_A")
        skip_row = _checkpoint_row("SKIP_EVT", skip_reason="empty_npz")
        error_row = _checkpoint_row("ERR_EVT", error="boom")
        mismatched_npz_path_row = _checkpoint_row("MISMATCH_NPZ_PATH_EVT", npz_path="/old/MISMATCH_EVT.npz")
        mismatched_npz_fp_row = _checkpoint_row(
            "MISMATCH_NPZ_FP_EVT",
            npz_fingerprint={
                "path": "/fake/MISMATCH_NPZ_FP_EVT.npz",
                "size": 1000,
                "mtime_ns": 123456789,
                "sha256": "sha:MISMATCH_NPZ_FP_EVT:old",
            },
        )
        mismatched_event_fp_row = _checkpoint_row(
            "MISMATCH_EVENT_FP_EVT",
            event_fingerprint="event-fp:MISMATCH_EVENT_FP_EVT:old",
        )
        mismatched_sensor_fp_row = _checkpoint_row(
            "MISMATCH_SENSOR_FP_EVT",
            sensor_feature_fingerprints={
                "VIX": {
                    "path": "/fake/VIX.OPT_MISMATCH_SENSOR_FP_EVT_features_v1.npz",
                    "size": 2000,
                    "mtime_ns": 10,
                    "sha256": "old-vix",
                }
            },
        )
        malformed_hyp_row = _checkpoint_row("BAD_HYP_EVT", hypotheses=[{"hypothesis_id": 1}])
        missing_expected_hyp_row = _checkpoint_row(
            "MISSING_EXPECTED_HYP_EVT",
            expected_hypothesis_ids=[1, 2],
        )
        duplicate_expected_hyp_row = _checkpoint_row(
            "DUP_EXPECTED_HYP_EVT",
            expected_hypothesis_ids=[1, 1],
        )
        stale_row = _checkpoint_row("STALE_EVT")
        checkpoint.write_text(
            "\n".join([
                json.dumps(ok_row),
                json.dumps(skip_row),
                json.dumps(error_row),
                json.dumps(mismatched_npz_path_row),
                json.dumps(mismatched_npz_fp_row),
                json.dumps(mismatched_event_fp_row),
                json.dumps(mismatched_sensor_fp_row),
                json.dumps(malformed_hyp_row),
                json.dumps(missing_expected_hyp_row),
                json.dumps(duplicate_expected_hyp_row),
                "{truncated",
                json.dumps({"event_id": "NO_SYMBOL"}),
                json.dumps({"event_id": "MISSING_EVT", "symbol": "MES.v.0", "latency_ms": 1.0}),
                json.dumps(stale_row),
            ]) + "\n",
            encoding="utf-8",
        )
        work_units = [
            self._unit("AAA_EVT_A"),
            self._unit("SKIP_EVT"),
            self._unit("ERR_EVT"),
            self._unit("MISMATCH_NPZ_PATH_EVT", npz_path="/new/MISMATCH_EVT.npz"),
            self._unit("MISMATCH_NPZ_FP_EVT"),
            self._unit("MISMATCH_EVENT_FP_EVT"),
            {
                **self._unit("MISMATCH_SENSOR_FP_EVT"),
                "sensor_feature_fingerprints": {
                    "VIX": {
                        "path": "/fake/VIX.OPT_MISMATCH_SENSOR_FP_EVT_features_v1.npz",
                        "size": 2000,
                        "mtime_ns": 10,
                        "sha256": "new-vix",
                    }
                },
            },
            self._unit("BAD_HYP_EVT"),
            {**self._unit("MISSING_EXPECTED_HYP_EVT"), "expected_hypothesis_ids": [1, 2]},
            {**self._unit("DUP_EXPECTED_HYP_EVT"), "expected_hypothesis_ids": [1, 1]},
            self._unit("MISSING_EVT"),
        ]

        loaded = universe_mod._load_checkpoint_results(checkpoint, work_units)

        assert set(loaded) == {"AAA_EVT_A|MES.v.0|1.0", "SKIP_EVT|MES.v.0|1.0"}
        assert loaded["AAA_EVT_A|MES.v.0|1.0"]["event_id"] == "AAA_EVT_A"
        assert loaded["SKIP_EVT|MES.v.0|1.0"]["skip_reason"] == "empty_npz"

    def test_checkpoint_context_rescan_mismatch_invalidates_cache(self, universe_mod, tmp_path):
        checkpoint = tmp_path / "unit_results.jsonl"
        checkpoint.write_text(json.dumps(_checkpoint_row("AAA_EVT_A")) + "\n", encoding="utf-8")
        old_context = universe_mod._checkpoint_context({
            "lane": "cme",
            "events_csv": "events.csv",
            "rescan": False,
        })
        current_context = universe_mod._checkpoint_context({
            "lane": "cme",
            "events_csv": "events.csv",
            "rescan": True,
        })
        universe_mod._checkpoint_context_path(checkpoint).write_text(
            json.dumps(old_context, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        loaded = universe_mod._prepare_checkpoint_results(
            checkpoint,
            [self._unit("AAA_EVT_A")],
            current_context,
        )

        assert loaded == {}
        assert checkpoint.read_text(encoding="utf-8") == ""
        stored = json.loads(universe_mod._checkpoint_context_path(checkpoint).read_text(encoding="utf-8"))
        assert stored["cli_args"]["rescan"] is True

    def test_main_reuses_checkpoint_and_reruns_error_or_malformed_entries(
        self, tmp_path, events_csv, minimal_npz, minimal_npz_b, monkeypatch
    ):
        import backtest_pipeline.src.replay_matrix as _rm
        _orig = _rm.run_all_hypotheses_replay

        mod = _load_fresh_universe_mod("run_event_universe_resume")
        monkeypatch.setattr(mod, "DEFAULT_CHI404_SUMMARY", tmp_path / "missing_chi404_summary.json")
        mod.load_lake_index = lambda _, rescan=False: {
            ("MES.v.0", "AAA_EVT_A"): str(minimal_npz),
            ("MES.v.0", "BBB_EVT_B"): str(minimal_npz_b),
        }
        argv = [
            "--events-csv", str(events_csv),
            "--symbols", "MES.v.0",
            "--workers", "1",
            "--bands", "1.0",
            "--max-events", "2",
        ]
        cli_args = {
            "lane": "cme",
            "bands_override": "1.0",
            "event_type": None,
            "symbols": "MES.v.0",
            "events_csv": str(events_csv),
            "rescan": False,
            "workers": 1,
            "max_events": 2,
            "from_stage_a": None,
            "from_stage_a_sha256": None,
            "cells": None,
            "shard": None,
            "shard_index": None,
            "shard_total": None,
        }
        out_dir = tmp_path / "resume_out"
        out_dir.mkdir()
        checkpoint = out_dir / "unit_results.jsonl"
        clean_dir = tmp_path / "clean_out"

        try:
            _rm.run_all_hypotheses_replay = _fake_hyp_results_for  # type: ignore[assignment]
            clean_rc = mod.main([*argv, "--out", str(clean_dir)])
            clean_payload = json.loads((clean_dir / "universe_result.json").read_text(encoding="utf-8"))
            cached_aaa = next(row for row in clean_payload["unit_results"] if row["event_id"] == "AAA_EVT_A")
            cached_bbb_error = next(row for row in clean_payload["unit_results"] if row["event_id"] == "BBB_EVT_B")
            cached_bbb_error = {
                **cached_bbb_error,
                "error": "previous failure",
                "hypotheses": [],
            }
            checkpoint.write_text(
                "\n".join([
                    json.dumps(cached_aaa),
                    json.dumps(cached_bbb_error),
                    "{truncated",
                ]) + "\n",
                encoding="utf-8",
            )
            mod._checkpoint_context_path(checkpoint).write_text(
                json.dumps(mod._checkpoint_context(cli_args), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            rc = mod.main([*argv, "--out", str(out_dir)])
        finally:
            _rm.run_all_hypotheses_replay = _orig

        assert clean_rc == 0
        assert rc == 0
        payload = json.loads((out_dir / "universe_result.json").read_text(encoding="utf-8"))
        by_event = {row["event_id"]: row for row in payload["unit_results"]}
        assert set(by_event) == {"AAA_EVT_A", "BBB_EVT_B"}
        assert by_event["AAA_EVT_A"]["run_id"] == cached_aaa["run_id"]
        assert by_event["BBB_EVT_B"]["error"] is None
        assert payload["aggregated"] == clean_payload["aggregated"]
        assert payload["checkpoint"]["reused_units"] == 1
        assert payload["checkpoint"]["new_units"] == 1

        lines = checkpoint.read_text(encoding="utf-8").splitlines()
        completed_rows = [
            json.loads(line) for line in lines
            if line.startswith("{") and not line.startswith("{truncated")
        ]
        assert [row["event_id"] for row in completed_rows].count("BBB_EVT_B") == 2

    def test_reused_ok_does_not_hide_fresh_failfast(
        self, tmp_path, events_csv, minimal_npz, minimal_npz_b, monkeypatch
    ):
        import backtest_pipeline.src.replay_matrix as _rm
        _orig = _rm.run_all_hypotheses_replay

        mod = _load_fresh_universe_mod("run_event_universe_failfast_resume")
        monkeypatch.setattr(mod, "DEFAULT_CHI404_SUMMARY", tmp_path / "missing_chi404_summary.json")
        monkeypatch.setenv("HFT3_UNIVERSE_FAILFAST_ERRORS", "1")
        mod.load_lake_index = lambda _, rescan=False: {
            ("MES.v.0", "AAA_EVT_A"): str(minimal_npz),
            ("MES.v.0", "BBB_EVT_B"): str(minimal_npz_b),
        }
        argv = [
            "--events-csv", str(events_csv),
            "--symbols", "MES.v.0",
            "--workers", "1",
            "--bands", "1.0",
            "--max-events", "2",
        ]
        cli_args = {
            "lane": "cme",
            "bands_override": "1.0",
            "event_type": None,
            "symbols": "MES.v.0",
            "events_csv": str(events_csv),
            "rescan": False,
            "workers": 1,
            "max_events": 2,
            "from_stage_a": None,
            "from_stage_a_sha256": None,
            "cells": None,
            "shard": None,
            "shard_index": None,
            "shard_total": None,
        }
        clean_dir = tmp_path / "clean_failfast_seed"
        out_dir = tmp_path / "resume_failfast"
        out_dir.mkdir()
        checkpoint = out_dir / "unit_results.jsonl"

        def _failing_replay(*args, **kwargs):
            raise RuntimeError("fresh replay failure")

        try:
            _rm.run_all_hypotheses_replay = _fake_hyp_results_for  # type: ignore[assignment]
            clean_rc = mod.main([*argv, "--out", str(clean_dir)])
            clean_payload = json.loads((clean_dir / "universe_result.json").read_text(encoding="utf-8"))
            cached_aaa = next(row for row in clean_payload["unit_results"] if row["event_id"] == "AAA_EVT_A")
            checkpoint.write_text(json.dumps(cached_aaa) + "\n", encoding="utf-8")
            mod._checkpoint_context_path(checkpoint).write_text(
                json.dumps(mod._checkpoint_context(cli_args), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            _rm.run_all_hypotheses_replay = _failing_replay  # type: ignore[assignment]
            rc = mod.main([*argv, "--out", str(out_dir)])
        finally:
            _rm.run_all_hypotheses_replay = _orig

        assert clean_rc == 0
        assert rc == 2
        payload = json.loads((out_dir / "universe_result.json").read_text(encoding="utf-8"))
        assert payload["status"] == "ABORTED_NO_PROGRESS"
        assert payload["units_processed"] == 1
        assert payload["units_errored"] == 1

    def test_worker_rejects_incomplete_hypothesis_results(self, tmp_path, minimal_npz, monkeypatch):
        import backtest_pipeline.src.replay_matrix as _rm
        _orig = _rm.run_all_hypotheses_replay

        from backtest_pipeline.src.backtest_result import BacktestResult

        mod = _load_fresh_universe_mod("run_event_universe_incomplete_hyps")

        def _one_result(_hyps, _npz_path, *, latency_ms=1.0, **_kw):
            return {
                1: BacktestResult(
                    hypothesis_id=1,
                    net_pnl=0.1,
                    num_trades=2,
                    win_rate=0.6,
                    expectancy=0.05,
                    adverse_selection_ticks=0.1,
                    tail_loss=-0.02,
                )
            }

        try:
            _rm.run_all_hypotheses_replay = _one_result  # type: ignore[assignment]
            row = mod._worker({
                "event_id": "AAA_EVT_A",
                "symbol": "MES.v.0",
                "npz_path": str(minimal_npz),
                "npz_fingerprint": mod._npz_fingerprint(str(minimal_npz)),
                "sensor_feature_fingerprints": {},
                "latency_ms": 1.0,
                "event_type": "CPI",
                "release_date": "2024-01-10",
                "event_fingerprint": "event-fp",
                "hyp_ids": [1, 2],
                "expected_hypothesis_ids": [1, 2],
            })
        finally:
            _rm.run_all_hypotheses_replay = _orig

        assert row["error"]
        assert "expected [1, 2]" in row["error"]
        assert row["hypotheses"] == []


class TestEndToEndSmoke:
    def test_main_single_event_mocked_replay(self, tmp_path, events_csv, minimal_npz):
        """Call main() with mocked run_all_hypotheses_replay; verify JSON/MD outputs and schema.

        The mock keeps the test fast (< 1 s) by bypassing the 45-session replay
        while still exercising the full CLI → worker → aggregation → correction → output path.

        _worker does a local import:
            from backtest_pipeline.src.replay_matrix import run_all_hypotheses_replay
        With workers=1 the worker runs in the same process. We ensure
        backtest_pipeline.src.replay_matrix is already imported before the worker
        runs, then swap the function on that cached module object so the worker's
        local-import alias also picks up the stub.
        """
        import backtest_pipeline.src.replay_matrix as _rm
        _orig_fn = _rm.run_all_hypotheses_replay

        out_dir = tmp_path / "out"
        mod = _load_fresh_universe_mod("run_event_universe_smoke")
        mod.load_lake_index = lambda _, rescan=False: {("MES.v.0", "AAA_EVT_A"): str(minimal_npz)}

        try:
            # Swap the function on the already-cached module so that any
            # "from backtest_pipeline.src.replay_matrix import run_all_hypotheses_replay"
            # inside _worker will resolve the stubbed version (workers=1, same process).
            _rm.run_all_hypotheses_replay = _fake_hyp_results_for  # type: ignore[assignment]

            # Also swap inside the universe module's own worker path if it cached it
            if hasattr(mod, "_rm"):
                mod._rm.run_all_hypotheses_replay = _fake_hyp_results_for  # type: ignore[assignment]

            rc = mod.main([
                "--events-csv", str(events_csv),
                "--symbols", "MES.v.0",
                "--event-type", "CPI",
                "--out", str(out_dir),
                "--workers", "1",
                "--max-events", "1",
                "--bands", "1.0",
            ])
        finally:
            _rm.run_all_hypotheses_replay = _orig_fn

        assert rc == 0
        result_path = out_dir / "universe_result.json"
        report_path = out_dir / "universe_report.md"
        assert result_path.exists(), f"universe_result.json missing in {out_dir}"
        assert report_path.exists(), f"universe_report.md missing in {out_dir}"

        payload = json.loads(result_path.read_text(encoding="utf-8"))
        assert payload["schema"] == "universe_result_v1"
        assert isinstance(payload["units_run"], int)
        assert payload["units_run"] >= 1
        assert "certification_stamp" in payload
        assert "aggregated" in payload
        assert "corrections" in payload
        # Confirm at least one unit ran without error and has hypotheses
        non_errored = [u for u in payload["unit_results"] if u.get("error") is None]
        assert len(non_errored) >= 1, f"All units errored: {[u.get('error') for u in payload['unit_results']]}"
        assert len(non_errored[0]["hypotheses"]) > 0

    def test_main_no_npz_writes_skipped(self, tmp_path, events_csv):
        """When no NPZ exists, all units are skipped; result JSON still written."""
        out_dir = tmp_path / "out_skip"
        mod = _load_fresh_universe_mod("run_event_universe_skip")
        mod.load_lake_index = lambda _, rescan=False: {}  # empty — everything skipped

        rc = mod.main([
            "--events-csv", str(events_csv),
            "--symbols", "MES.v.0",
            "--out", str(out_dir),
            "--workers", "1",
            "--bands", "1.0",
        ])
        assert rc == 0
        result_path = out_dir / "universe_result.json"
        assert result_path.exists()
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        assert payload["units_run"] == 0
        assert payload["units_skipped"] > 0
        assert payload["skip_reason_counts"] == {"npz_missing": payload["units_skipped"]}
        assert sum(payload["skip_reason_counts"].values()) == payload["units_skipped"]
        for row in payload["skipped"]:
            assert row["reason"] == "npz_missing"
            assert row["event_type"] in {"CPI", "NFP"}
            assert row["release_date"] in {"2024-01-10", "2024-02-02", "2024-03-13"}
            assert row["symbol"] == "MES.v.0"

    def test_stage_a_pass_through_overlap_is_union_not_duplicate(self, tmp_path, events_csv, minimal_npz):
        """A BH survivor can also be queue-sensitive pass_through; that is one allowed cell."""
        stage_a = tmp_path / "stage_a_survivors.json"
        stage_a.write_text(json.dumps({
            "band_ms": 1.0,
            "tested_cells": [
                {"hyp_id": 42, "event_type": "CPI", "band_ms": 1.0, "p": 0.01},
                {"hyp_id": 42, "event_type": "NFP", "band_ms": 1.0, "p": 1.0},
            ],
            "survivors": [
                {"hyp_id": 42, "event_type": "CPI", "p": 0.01},
            ],
            "pass_through": [42],
        }), encoding="utf-8")

        out_dir = tmp_path / "out_stage_a_union"
        mod = _load_fresh_universe_mod("run_event_universe_stage_a_union")
        mod.load_lake_index = lambda _, rescan=False: {("MES.v.0", "AAA_EVT_A"): str(minimal_npz)}
        import backtest_pipeline.src.replay_matrix as _rm
        _orig_fn = _rm.run_all_hypotheses_replay

        try:
            _rm.run_all_hypotheses_replay = _fake_hyp_results_for  # type: ignore[assignment]
            rc = mod.main([
                "--events-csv", str(events_csv),
                "--symbols", "MES.v.0",
                "--from-stage-a", str(stage_a),
                "--out", str(out_dir),
                "--workers", "1",
                "--bands", "1.0",
            ])
        finally:
            _rm.run_all_hypotheses_replay = _orig_fn

        assert rc == 0
        payload = json.loads((out_dir / "universe_result.json").read_text(encoding="utf-8"))
        assert payload["units_run"] >= 1
        assert payload["stage_a_filter"]["allowed_cells_count"] == 2

    def test_stage_a_duplicate_same_source_cells_still_fail(self, tmp_path, events_csv):
        """Malformed Stage A JSON with repeated survivor cells remains fail-closed."""
        stage_a = tmp_path / "stage_a_survivors_duplicate.json"
        stage_a.write_text(json.dumps({
            "band_ms": 1.0,
            "tested_cells": [
                {"hyp_id": 42, "event_type": "CPI", "band_ms": 1.0, "p": 0.01},
            ],
            "survivors": [
                {"hyp_id": 42, "event_type": "CPI", "p": 0.01},
                {"hyp_id": 42, "event_type": "CPI", "p": 0.01},
            ],
            "pass_through": [],
        }), encoding="utf-8")

        out_dir = tmp_path / "out_stage_a_duplicate"
        mod = _load_fresh_universe_mod("run_event_universe_stage_a_duplicate")
        mod.load_lake_index = lambda _, rescan=False: {}

        with pytest.raises(SystemExit) as excinfo:
            mod.main([
                "--events-csv", str(events_csv),
                "--symbols", "MES.v.0",
                "--from-stage-a", str(stage_a),
                "--out", str(out_dir),
                "--workers", "1",
                "--bands", "1.0",
            ])

        assert excinfo.value.code == 2

    def test_stage_a_duplicate_cli_cells_are_idempotent(self, tmp_path, events_csv, minimal_npz):
        """Repeated manual --cells additions should not make Stage A look malformed."""
        stage_a = tmp_path / "stage_a_survivors.json"
        stage_a.write_text(json.dumps({
            "band_ms": 1.0,
            "tested_cells": [
                {"hyp_id": 42, "event_type": "CPI", "band_ms": 1.0, "p": 0.01},
            ],
            "survivors": [],
            "pass_through": [],
        }), encoding="utf-8")

        out_dir = tmp_path / "out_stage_a_cli_cells"
        mod = _load_fresh_universe_mod("run_event_universe_stage_a_cli_cells")
        mod.load_lake_index = lambda _, rescan=False: {("MES.v.0", "AAA_EVT_A"): str(minimal_npz)}
        import backtest_pipeline.src.replay_matrix as _rm
        _orig_fn = _rm.run_all_hypotheses_replay

        try:
            _rm.run_all_hypotheses_replay = _fake_hyp_results_for  # type: ignore[assignment]
            rc = mod.main([
                "--events-csv", str(events_csv),
                "--symbols", "MES.v.0",
                "--from-stage-a", str(stage_a),
                "--cells", "42:CPI,42:CPI",
                "--out", str(out_dir),
                "--workers", "1",
                "--bands", "1.0",
            ])
        finally:
            _rm.run_all_hypotheses_replay = _orig_fn

        assert rc == 0
        payload = json.loads((out_dir / "universe_result.json").read_text(encoding="utf-8"))
        assert payload["stage_a_filter"]["allowed_cells_count"] == 1

    def test_result_and_report_count_worker_skip_reasons(self, universe_mod, tmp_path):
        unit_results = [{
            "event_id": "EMPTY_EVT",
            "event_type": "CPI",
            "release_date": "2024-04-10",
            "symbol": "MES.v.0",
            "latency_ms": 1.0,
            "elapsed_s": 0.01,
            "error": None,
            "skip_reason": "empty_npz",
            "hypotheses": [],
        }]
        result_path = universe_mod.write_universe_result(
            tmp_path,
            unit_results=unit_results,
            skipped=[],
            aggregated={},
            corrections={},
            robustness=None,
            latency_bands=[1.0],
            cli_args={},
            stamp={},
            run_start_utc="2026-06-15T00:00:00+00:00",
            run_end_utc="2026-06-15T00:00:01+00:00",
            total_elapsed_s=1.0,
        )
        report_path = universe_mod.write_universe_report(
            tmp_path,
            unit_results=unit_results,
            skipped=[],
            aggregated={},
            corrections={},
            latency_bands=[1.0],
            stamp={},
            run_start_utc="2026-06-15T00:00:00+00:00",
            total_elapsed_s=1.0,
        )
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        assert payload["units_skipped"] == 1
        assert payload["skip_reason_counts"] == {"empty_npz": 1}
        assert payload["skipped"] == [{
            "event_id": "EMPTY_EVT",
            "event_type": "CPI",
            "release_date": "2024-04-10",
            "symbol": "MES.v.0",
            "latency_ms": 1.0,
            "reason": "empty_npz",
        }]
        report = report_path.read_text(encoding="utf-8")
        assert "| empty_npz | 1 |" in report
        assert "| CPI | 0 | 1 |" in report


# ---------------------------------------------------------------------------
# 4. Aggregation math
# ---------------------------------------------------------------------------

class TestAggregation:
    """Verify _aggregate_results math on hand-built per-event unit_results."""

    def _make_unit_result(
        self,
        event_id: str,
        etype: str,
        band: float,
        *,
        hyp_id: int = 1,
        expectancy: float = 0.5,
        win_rate: float = 0.6,
        num_trades: int = 10,
        adverse: float = 0.1,
    ) -> dict[str, Any]:
        return {
            "event_id": event_id,
            "event_type": etype,
            "latency_ms": band,
            "symbol": "MES.v.0",
            "npz_path": "x",
            "error": None,
            "hypotheses": [
                {
                    "hypothesis_id": hyp_id,
                    "hypothesis_name": f"H{hyp_id}",
                    "net_pnl_usd": expectancy * num_trades,
                    "num_trades": num_trades,
                    "win_rate": win_rate,
                    "expectancy_usd": expectancy,
                    "adverse_selection_ticks": adverse,
                    "tail_loss_usd": -0.1,
                }
            ],
        }

    def test_mean_expectancy(self, universe_mod):
        units = [
            self._make_unit_result("E1", "CPI", 1.0, expectancy=0.5),
            self._make_unit_result("E2", "CPI", 1.0, expectancy=1.5),
        ]
        agg = universe_mod._aggregate_results(units)
        cell = agg["1"]["CPI"][1.0]
        assert abs(cell["mean_expectancy_usd"] - 1.0) < 1e-9

    def test_n_events_count(self, universe_mod):
        units = [
            self._make_unit_result("E1", "CPI", 1.0),
            self._make_unit_result("E2", "CPI", 1.0),
            self._make_unit_result("E3", "CPI", 1.0),
        ]
        agg = universe_mod._aggregate_results(units)
        assert agg["1"]["CPI"][1.0]["n_events"] == 3

    def test_total_trades_sum(self, universe_mod):
        units = [
            self._make_unit_result("E1", "CPI", 1.0, num_trades=7),
            self._make_unit_result("E2", "CPI", 1.0, num_trades=13),
        ]
        agg = universe_mod._aggregate_results(units)
        assert agg["1"]["CPI"][1.0]["total_trades"] == 20

    def test_p5_tail_is_below_mean_for_mixed_values(self, universe_mod):
        units = [
            self._make_unit_result("E1", "CPI", 1.0, expectancy=1.0),
            self._make_unit_result("E2", "CPI", 1.0, expectancy=-2.0),
        ]
        agg = universe_mod._aggregate_results(units)
        cell = agg["1"]["CPI"][1.0]
        # 5th percentile of [1.0, -2.0] via numpy linear interp is -1.85
        # It must be below the mean (-0.5) and the minimum value (-2.0) ≤ p5 ≤ mean
        assert cell["p5_expectancy_tail_usd"] < cell["mean_expectancy_usd"]
        assert cell["p5_expectancy_tail_usd"] >= -2.0

    def test_errored_unit_excluded(self, universe_mod):
        units = [
            {**self._make_unit_result("E1", "CPI", 1.0), "error": "crash", "hypotheses": []},
            self._make_unit_result("E2", "CPI", 1.0, expectancy=0.5),
        ]
        agg = universe_mod._aggregate_results(units)
        assert agg["1"]["CPI"][1.0]["n_events"] == 1

    def test_multiple_event_types_separated(self, universe_mod):
        units = [
            self._make_unit_result("E1", "CPI", 1.0, expectancy=0.5),
            self._make_unit_result("E2", "NFP", 1.0, expectancy=1.5),
        ]
        agg = universe_mod._aggregate_results(units)
        assert "CPI" in agg["1"]
        assert "NFP" in agg["1"]
        assert agg["1"]["CPI"][1.0]["mean_expectancy_usd"] == pytest.approx(0.5)
        assert agg["1"]["NFP"][1.0]["mean_expectancy_usd"] == pytest.approx(1.5)

    def test_aggregation_note_present(self, universe_mod):
        units = [self._make_unit_result("E1", "CPI", 1.0)]
        agg = universe_mod._aggregate_results(units)
        note = agg["1"]["CPI"][1.0]["aggregation_note"]
        assert "arithmetic mean" in note
        assert "worst-event" in note


# ---------------------------------------------------------------------------
# 5. p-value / correction plumbing
# ---------------------------------------------------------------------------

class TestPValueAndCorrection:
    def test_p_value_below_1_for_strong_signal(self, universe_mod):
        # All positive expectancies — strong signal, p < 1
        expecs = [1.0, 1.2, 0.9, 1.1, 1.3, 0.8]
        p = universe_mod._derive_p_value(expecs)
        assert 0.0 < p < 0.5

    def test_p_value_near_1_for_zero_signal(self, universe_mod):
        # Centred on zero — p should be large
        expecs = [0.01, -0.01, 0.005, -0.005, 0.002]
        p = universe_mod._derive_p_value(expecs)
        assert p > 0.5

    def test_p_value_is_1_for_fewer_than_3(self, universe_mod):
        assert universe_mod._derive_p_value([]) == 1.0
        assert universe_mod._derive_p_value([0.5]) == 1.0
        assert universe_mod._derive_p_value([0.5, 0.6]) == 1.0

    def test_corrections_structure(self, universe_mod):
        """_apply_corrections returns dict with 'holm' and 'benjamini_hochberg' per event_type."""
        # Build a minimal aggregated dict
        aggregated = {
            "1": {
                "CPI": {
                    1.0: {
                        "hypothesis_id": 1,
                        "hypothesis_name": "H1",
                        "n_events": 5,
                        "total_trades": 50,
                        "mean_expectancy_usd": 0.4,
                        "mean_win_rate": 0.6,
                        "mean_adverse_selection_ticks": 0.1,
                        "p5_expectancy_tail_usd": 0.1,
                        "per_event_expectancies": [0.3, 0.4, 0.5, 0.4, 0.4],
                        "aggregation_note": "",
                    }
                }
            }
        }
        corrections = universe_mod._apply_corrections(aggregated)
        assert "CPI" in corrections
        assert "holm" in corrections["CPI"]
        assert "benjamini_hochberg" in corrections["CPI"]
        assert "p_value_method" in corrections["CPI"]

    def test_holm_passes_strong_hypothesis(self, universe_mod):
        """A hypothesis with clearly positive per-event expectancies should survive Holm."""
        # Large, consistent positive expectancies → small p → should pass
        per_event = [5.0, 5.5, 4.8, 5.2, 5.1, 4.9, 5.3]
        aggregated = {
            "1": {
                "CPI": {
                    1.0: {
                        "hypothesis_id": 1,
                        "hypothesis_name": "H1",
                        "n_events": len(per_event),
                        "total_trades": 70,
                        "mean_expectancy_usd": float(np.mean(per_event)),
                        "mean_win_rate": 0.8,
                        "mean_adverse_selection_ticks": 0.05,
                        "p5_expectancy_tail_usd": float(np.percentile(per_event, 5)),
                        "per_event_expectancies": per_event,
                        "aggregation_note": "",
                    }
                }
            }
        }
        corrections = universe_mod._apply_corrections(aggregated)
        holm = corrections["CPI"]["holm"]
        assert len(holm["passed_slugs"]) >= 1

    def test_holm_fails_weak_hypothesis(self, universe_mod):
        """A hypothesis with near-zero expectancies (noise) should not survive Holm."""
        per_event = [0.01, -0.02, 0.005, -0.008, 0.003, -0.001]
        aggregated = {
            "99": {
                "CPI": {
                    2.0: {
                        "hypothesis_id": 99,
                        "hypothesis_name": "noise",
                        "n_events": len(per_event),
                        "total_trades": 10,
                        "mean_expectancy_usd": float(np.mean(per_event)),
                        "mean_win_rate": 0.5,
                        "mean_adverse_selection_ticks": 0.0,
                        "p5_expectancy_tail_usd": float(np.percentile(per_event, 5)),
                        "per_event_expectancies": per_event,
                        "aggregation_note": "",
                    }
                }
            }
        }
        corrections = universe_mod._apply_corrections(aggregated)
        holm = corrections["CPI"]["holm"]
        assert "hyp_99_band_2.0" in holm["failed_slugs"]

    def test_bh_less_conservative_than_holm(self, universe_mod):
        """BH (FDR) should pass at least as many as Holm (FWER) in general."""
        # Borderline: moderate but consistent signal
        per_event = [0.3, 0.25, 0.28, 0.22, 0.31, 0.27]
        aggregated = {
            "2": {
                "NFP": {
                    1.0: {
                        "hypothesis_id": 2,
                        "hypothesis_name": "H2",
                        "n_events": len(per_event),
                        "total_trades": 30,
                        "mean_expectancy_usd": float(np.mean(per_event)),
                        "mean_win_rate": 0.65,
                        "mean_adverse_selection_ticks": 0.05,
                        "p5_expectancy_tail_usd": float(np.percentile(per_event, 5)),
                        "per_event_expectancies": per_event,
                        "aggregation_note": "",
                    }
                }
            }
        }
        corrections = universe_mod._apply_corrections(aggregated)
        holm_passed = len(corrections["NFP"]["holm"]["passed_slugs"])
        bh_passed = len(corrections["NFP"]["benjamini_hochberg"]["passed_slugs"])
        # BH is always at least as permissive as Holm on a single hypothesis
        assert bh_passed >= holm_passed


# ---------------------------------------------------------------------------
# 6. max-events semantics: cap counts events WITH data, not raw CSV rows
# ---------------------------------------------------------------------------

class TestMaxEventsWithDataSemantics:
    """Verify max_events counts only events that actually have NPZ data.

    The events.csv fixture has three rows sorted alphabetically:
        AAA_EVT_A  (CPI)
        BBB_EVT_B  (NFP)
        ZZZ_NO_NPZ (CPI)

    If the alphabetically-first events lack NPZ, they must not consume
    the max_events budget — later events that DO have NPZ should be included.
    """

    def test_first_event_no_npz_later_event_included(
        self, universe_mod, events_csv, tmp_path
    ):
        """AAA_EVT_A has no NPZ; BBB_EVT_B has NPZ.
        With max_events=1, BBB_EVT_B (the first event WITH data) must be
        included in work units even though it's alphabetically second.
        """
        from backtest_pipeline.src.replay_npz_fixture import build_minimal_mbo_npz

        npz_b = tmp_path / "MES.v.0_BBB_EVT_B_mbo.npz"
        build_minimal_mbo_npz(npz_b)

        # Only BBB_EVT_B has an NPZ; AAA_EVT_A and ZZZ_NO_NPZ are absent
        lake_index = {("MES.v.0", "BBB_EVT_B"): str(npz_b)}

        work, skipped = universe_mod.build_work_units(
            events_csv,
            lake_index,
            latency_bands=[1.0],
            event_type_filter=None,
            symbol_filter=["MES.v.0"],
            max_events=1,
        )

        # BBB_EVT_B must appear in work units — it is the ONLY event with data
        work_ids = {u["event_id"] for u in work}
        assert "BBB_EVT_B" in work_ids, (
            "BBB_EVT_B should be a work unit even though alphabetically-first "
            "AAA_EVT_A lacks an NPZ"
        )
        # AAA_EVT_A and ZZZ_NO_NPZ have no NPZ → skipped with npz_missing
        skipped_ids = {s["event_id"] for s in skipped}
        assert "AAA_EVT_A" in skipped_ids
        # ZZZ_NO_NPZ may or may not appear; it's after the cap; the important
        # invariant is BBB_EVT_B (the first event with data) is a work unit.
        assert len(work) == 1  # exactly 1 event × 1 band

    def test_max_events_cap_excludes_events_after_budget_exhausted(
        self, universe_mod, events_csv, tmp_path
    ):
        """Once max_events unique events-with-data have been collected,
        subsequent events with NPZ are excluded (not skipped with npz_missing).
        """
        from backtest_pipeline.src.replay_npz_fixture import build_minimal_mbo_npz

        npz_a = tmp_path / "MES.v.0_AAA_EVT_A_mbo.npz"
        npz_b = tmp_path / "MES.v.0_BBB_EVT_B_mbo.npz"
        build_minimal_mbo_npz(npz_a)
        build_minimal_mbo_npz(npz_b)

        lake_index = {
            ("MES.v.0", "AAA_EVT_A"): str(npz_a),
            ("MES.v.0", "BBB_EVT_B"): str(npz_b),
        }

        work, skipped = universe_mod.build_work_units(
            events_csv,
            lake_index,
            latency_bands=[1.0],
            event_type_filter=None,
            symbol_filter=["MES.v.0"],
            max_events=1,
        )

        # Only the first alphabetical event with data (AAA_EVT_A) should appear
        work_ids = {u["event_id"] for u in work}
        assert "AAA_EVT_A" in work_ids
        assert "BBB_EVT_B" not in work_ids, (
            "BBB_EVT_B should be excluded by max_events=1 after AAA_EVT_A consumed the budget"
        )
        # Total work units: exactly 1 event × 1 band
        assert len(work) == 1


# ---------------------------------------------------------------------------
# 7. npz_resolver: npz_root override via HFT3_NPZ_ROOT
# ---------------------------------------------------------------------------

class TestNpzResolverRoot:
    """Unit tests for npz_root() respecting HFT3_NPZ_ROOT env var."""

    def test_npz_root_default_is_repo_data_npz(self, tmp_path):
        """Without HFT3_NPZ_ROOT set, npz_root returns <repo>/data/npz."""
        import os
        from data_system.src.npz_resolver import npz_root

        # Remove override if present
        old = os.environ.pop("HFT3_NPZ_ROOT", None)
        try:
            result = npz_root(tmp_path)
            assert result == tmp_path / "data" / "npz"
        finally:
            if old is not None:
                os.environ["HFT3_NPZ_ROOT"] = old

    def test_npz_root_overridden_by_env_var(self, tmp_path, monkeypatch):
        """When HFT3_NPZ_ROOT is set, npz_root returns that path."""
        from data_system.src.npz_resolver import npz_root

        external = tmp_path / "external_lake"
        external.mkdir()
        monkeypatch.setenv("HFT3_NPZ_ROOT", str(external))

        result = npz_root(tmp_path)
        assert result == external

    def test_npz_root_override_empty_string_falls_back_to_default(
        self, tmp_path, monkeypatch
    ):
        """An empty HFT3_NPZ_ROOT behaves like the override not being set."""
        from data_system.src.npz_resolver import npz_root

        monkeypatch.setenv("HFT3_NPZ_ROOT", "   ")  # whitespace only → stripped to ""
        result = npz_root(tmp_path)
        assert result == tmp_path / "data" / "npz"

    def test_npz_path_for_external_root(self, tmp_path, monkeypatch):
        """npz_path_for constructs the canonical path under the external root."""
        from data_system.src.npz_resolver import npz_path_for

        external = tmp_path / "lake"
        external.mkdir()
        monkeypatch.setenv("HFT3_NPZ_ROOT", str(external))

        path = npz_path_for(tmp_path, "CPI_2024_09_11_TIGHT", "MES.v.0")
        assert path == external / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz"


# ---------------------------------------------------------------------------
# 8. 2026 embargo enforcement
# ---------------------------------------------------------------------------

_EVENTS_CSV_WITH_2026 = """\
event_id,event_type,release_date,release_time,timezone,window_name,start_offset_seconds,end_offset_seconds,symbols,priority,source,source_url,effective_date,notes,row_status
EMBARGO_EVT,CPI,2026-01-15,08:30:00,America/New_York,TIGHT,-30,300,"MES.v.0",50,TEST,http://example.com,2026-01-01,test,SOURCED
PRE_EVT,CPI,2025-12-31,08:30:00,America/New_York,TIGHT,-30,300,"MES.v.0",50,TEST,http://example.com,2025-12-01,test,SOURCED
"""


class TestEmbargoEnforcement:
    @pytest.fixture()
    def embargo_events_csv(self, tmp_path: Path) -> Path:
        p = tmp_path / "embargo_events.csv"
        p.write_text(_EVENTS_CSV_WITH_2026, encoding="utf-8")
        return p

    @pytest.fixture()
    def embargo_npz(self, tmp_path: Path) -> Path:
        """NPZ for the embargoed event — guard must fire BEFORE this is read."""
        from backtest_pipeline.src.replay_npz_fixture import build_minimal_mbo_npz

        npz = tmp_path / "MES.v.0_EMBARGO_EVT_mbo.npz"
        build_minimal_mbo_npz(npz)
        return npz

    @pytest.fixture()
    def pre_npz(self, tmp_path: Path) -> Path:
        """NPZ for the pre-embargo event."""
        from backtest_pipeline.src.replay_npz_fixture import build_minimal_mbo_npz

        npz = tmp_path / "MES.v.0_PRE_EVT_mbo.npz"
        build_minimal_mbo_npz(npz)
        return npz

    def test_2026_row_skipped_with_embargo_reason_even_when_npz_exists(
        self, universe_mod, embargo_events_csv, embargo_npz, pre_npz
    ):
        """release_date 2026-01-15 → skipped reason=embargo_2026; NOT in work units."""
        lake_index = {
            ("MES.v.0", "EMBARGO_EVT"): str(embargo_npz),
            ("MES.v.0", "PRE_EVT"): str(pre_npz),
        }
        work, skipped = universe_mod.build_work_units(
            embargo_events_csv,
            lake_index,
            latency_bands=[1.0],
            event_type_filter=None,
            symbol_filter=["MES.v.0"],
            max_events=None,
        )
        work_ids = {u["event_id"] for u in work}
        skipped_ids = {s["event_id"] for s in skipped}
        assert "EMBARGO_EVT" not in work_ids, "embargoed event must not appear in work units"
        assert "EMBARGO_EVT" in skipped_ids, "embargoed event must appear in skipped"
        embargo_entries = [s for s in skipped if s["event_id"] == "EMBARGO_EVT"]
        assert all(s["reason"] == "embargo_2026" for s in embargo_entries)

    def test_2025_12_31_row_not_embargo_skipped(
        self, universe_mod, embargo_events_csv, pre_npz
    ):
        """release_date 2025-12-31 is before embargo start → not embargo-skipped."""
        lake_index = {("MES.v.0", "PRE_EVT"): str(pre_npz)}
        work, skipped = universe_mod.build_work_units(
            embargo_events_csv,
            lake_index,
            latency_bands=[1.0],
            event_type_filter=None,
            symbol_filter=["MES.v.0"],
            max_events=None,
        )
        work_ids = {u["event_id"] for u in work}
        assert "PRE_EVT" in work_ids, "2025-12-31 event must not be embargo-skipped"
        embargo_skipped_ids = {s["event_id"] for s in skipped if s["reason"] == "embargo_2026"}
        assert "PRE_EVT" not in embargo_skipped_ids

    def test_universe_result_json_contains_embargo_block(
        self, tmp_path, embargo_events_csv, embargo_npz, pre_npz
    ):
        """universe_result.json must contain embargo block with correct count."""
        import backtest_pipeline.src.replay_matrix as _rm

        _orig_fn = _rm.run_all_hypotheses_replay
        out_dir = tmp_path / "embargo_out"
        mod = _load_fresh_universe_mod("run_event_universe_embargo")
        mod.load_lake_index = lambda _, rescan=False: {
            ("MES.v.0", "EMBARGO_EVT"): str(embargo_npz),
            ("MES.v.0", "PRE_EVT"): str(pre_npz),
        }

        try:
            _rm.run_all_hypotheses_replay = _fake_hyp_results_for  # type: ignore[assignment]
            rc = mod.main([
                "--events-csv", str(embargo_events_csv),
                "--symbols", "MES.v.0",
                "--out", str(out_dir),
                "--workers", "1",
                "--bands", "1.0",
            ])
        finally:
            _rm.run_all_hypotheses_replay = _orig_fn

        assert rc == 0
        result_path = out_dir / "universe_result.json"
        assert result_path.exists()
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        assert "embargo" in payload, "universe_result.json must contain 'embargo' key"
        embargo_block = payload["embargo"]
        assert embargo_block["start"] == "2026-01-01"
        # 1 embargoed event × N bands = N skipped units (runner appends CHI404 measured band)
        assert embargo_block["units_skipped_embargo"] == len(payload["latency_bands_ms"])
        # EMBARGO_EVT must not appear in unit_results
        run_ids = {u["event_id"] for u in payload.get("unit_results", [])}
        assert "EMBARGO_EVT" not in run_ids


# ---------------------------------------------------------------------------
# 9. Shard tests
# ---------------------------------------------------------------------------

class TestShard:
    """Verify the --shard I/N hash-partitioning logic."""

    # ---- parse_shard ----

    def test_parse_valid(self, universe_mod):
        assert universe_mod.parse_shard("0/2") == (0, 2)
        assert universe_mod.parse_shard("1/2") == (1, 2)
        assert universe_mod.parse_shard("3/10") == (3, 10)

    def test_parse_malformed_format(self, universe_mod):
        """Non-'I/N' strings must raise ValueError."""
        for bad in ("0", "0-2", "0/2/3", "half", ""):
            with pytest.raises(ValueError, match="I/N"):
                universe_mod.parse_shard(bad)

    def test_parse_non_integer_parts(self, universe_mod):
        with pytest.raises(ValueError):
            universe_mod.parse_shard("a/2")
        with pytest.raises(ValueError):
            universe_mod.parse_shard("0/b")

    def test_parse_i_equals_n_error(self, universe_mod):
        """I == N must be rejected (valid range: 0 <= I < N)."""
        with pytest.raises(ValueError, match="0 <= I < N"):
            universe_mod.parse_shard("2/2")

    def test_parse_i_greater_than_n_error(self, universe_mod):
        with pytest.raises(ValueError, match="0 <= I < N"):
            universe_mod.parse_shard("5/2")

    def test_parse_n_zero_error(self, universe_mod):
        """N == 0 must be rejected."""
        with pytest.raises(ValueError, match="N must be >= 1"):
            universe_mod.parse_shard("0/0")

    # ---- apply_shard: partition correctness ----

    def _make_units(self, n: int) -> list[dict]:
        """Produce n synthetic work units with distinct stable keys."""
        return [
            {
                "event_id": f"EVT_{i:04d}",
                "symbol": "MES.v.0",
                "latency_ms": 1.0,
                "npz_path": f"/fake/EVT_{i:04d}.npz",
                "event_type": "CPI",
                "release_date": "2024-01-01",
            }
            for i in range(n)
        ]

    def test_two_shards_partition_exactly(self, universe_mod):
        """Shards 0/2 and 1/2 must be disjoint and their union must be all units."""
        units = self._make_units(20)
        shard0 = universe_mod.apply_shard(units, 0, 2)
        shard1 = universe_mod.apply_shard(units, 1, 2)

        keys_all = {universe_mod._unit_shard_key(u) for u in units}
        keys0 = {universe_mod._unit_shard_key(u) for u in shard0}
        keys1 = {universe_mod._unit_shard_key(u) for u in shard1}

        # Disjoint
        assert keys0.isdisjoint(keys1), "Shards must not overlap"
        # Union = all
        assert keys0 | keys1 == keys_all, "Union of shards must equal full set"

    def test_shard_assignment_deterministic(self, universe_mod):
        """Same units → same shard assignment across repeated calls."""
        units = self._make_units(30)
        shard_a = [universe_mod._unit_shard_key(u) for u in universe_mod.apply_shard(units, 0, 3)]
        shard_b = [universe_mod._unit_shard_key(u) for u in universe_mod.apply_shard(units, 0, 3)]
        assert shard_a == shard_b, "Shard assignment must be deterministic"

    def test_shard_assignment_order_independent(self, universe_mod):
        """Shuffling the input list must not change which units land in a shard."""
        import random
        units = self._make_units(40)
        expected = {universe_mod._unit_shard_key(u) for u in universe_mod.apply_shard(units, 1, 4)}

        shuffled = units[:]
        random.shuffle(shuffled)
        got = {universe_mod._unit_shard_key(u) for u in universe_mod.apply_shard(shuffled, 1, 4)}

        assert expected == got, "Shard membership must not depend on input order"

    def test_shard_0_of_1_returns_all(self, universe_mod):
        """A single shard (N=1, I=0) must contain all units."""
        units = self._make_units(15)
        result = universe_mod.apply_shard(units, 0, 1)
        assert len(result) == len(units)

    def test_four_shards_partition_exactly(self, universe_mod):
        """All four shards of 4 partition the set exactly."""
        units = self._make_units(40)
        all_keys = {universe_mod._unit_shard_key(u) for u in units}
        union: set[str] = set()
        seen: list[set[str]] = []
        for i in range(4):
            part = {universe_mod._unit_shard_key(u) for u in universe_mod.apply_shard(units, i, 4)}
            # Each prior shard must be disjoint from this one
            for prev in seen:
                assert part.isdisjoint(prev), f"Shard {i}/4 overlaps a previous shard"
            union |= part
            seen.append(part)
        assert union == all_keys, "Union of 4 shards must equal full set"

    # ---- CLI integration: shard metadata recorded in JSON ----

    # ---- OPT 2: smallest-first ordering ----

    def test_work_units_sorted_smallest_first(self, universe_mod, tmp_path, events_csv):
        """After build_work_units + OPT-2 sort, units appear in ascending NPZ size order.

        Creates three NPZ files of distinct sizes, builds work units, then verifies
        that the sorted list is ordered by file size ascending with a stable tiebreak
        by (event_id, symbol, latency_ms).
        """
        import json as _json
        from backtest_pipeline.src.replay_npz_fixture import build_minimal_mbo_npz

        # Create three NPZ files; pad to different sizes by varying n_levels.
        npz_a = tmp_path / "MES.v.0_AAA_EVT_A_mbo.npz"
        npz_b = tmp_path / "MES.v.0_BBB_EVT_B_mbo.npz"

        build_minimal_mbo_npz(npz_a, n_levels=2)   # small  (~327 bytes)
        build_minimal_mbo_npz(npz_b, n_levels=10)  # larger (~506 bytes)

        # Write extra events.csv with both events
        extra_csv = tmp_path / "extra_events.csv"
        extra_csv.write_text(
            "event_id,event_type,release_date,release_time,timezone,"
            "window_name,start_offset_seconds,end_offset_seconds,symbols,"
            "priority,source,source_url,effective_date,notes,row_status\n"
            "AAA_EVT_A,CPI,2024-01-10,08:30:00,America/New_York,TIGHT,-30,300,"
            "\"MES.v.0\",50,TEST,http://example.com,2024-01-01,test,SOURCED\n"
            "BBB_EVT_B,NFP,2024-02-02,08:30:00,America/New_York,TIGHT,-30,300,"
            "\"MES.v.0\",50,TEST,http://example.com,2024-01-01,test,SOURCED\n",
            encoding="utf-8",
        )

        lake_index = {
            ("MES.v.0", "AAA_EVT_A"): str(npz_a),
            ("MES.v.0", "BBB_EVT_B"): str(npz_b),
        }
        work, _ = universe_mod.build_work_units(
            extra_csv,
            lake_index,
            latency_bands=[1.0],
            event_type_filter=None,
            symbol_filter=["MES.v.0"],
            max_events=None,
        )
        assert len(work) == 2

        # Apply the OPT-2 sort key (same logic as run_event_universe.main)
        import os as _os

        def _unit_sort_key(u):
            npz = u.get("npz_path", "")
            try:
                sz = _os.path.getsize(npz) if npz else 0
            except OSError:
                sz = 0
            return (sz, u["event_id"], u["symbol"], float(u["latency_ms"]))

        sorted_units = sorted(work, key=_unit_sort_key)

        sizes = [_os.path.getsize(u["npz_path"]) for u in sorted_units]
        assert sizes == sorted(sizes), (
            f"Work units not in ascending NPZ size order: {sizes}"
        )
        # Tiebreak: same-size units must be ordered by event_id
        # (verified by construction here since sizes differ)
        assert sorted_units[0]["event_id"] == "AAA_EVT_A"
        assert sorted_units[1]["event_id"] == "BBB_EVT_B"

    def test_main_shard_metadata_in_output(self, tmp_path, events_csv, minimal_npz):
        """Running with --shard records shard_index and shard_total in cli_args.

        AAA_EVT_A|MES.v.0|1.0 hashes to shard 1/2 (verified: SHA-256 mod 2 == 1),
        so we use --shard 1/2 to ensure at least one unit is processed and the
        full result path (not the early-exit no-work-units path) is exercised.
        """
        import backtest_pipeline.src.replay_matrix as _rm
        _orig = _rm.run_all_hypotheses_replay

        out_dir = tmp_path / "shard_out"
        mod = _load_fresh_universe_mod("run_event_universe_shard_meta")
        mod.load_lake_index = lambda _, rescan=False: {("MES.v.0", "AAA_EVT_A"): str(minimal_npz)}

        try:
            _rm.run_all_hypotheses_replay = _fake_hyp_results_for  # type: ignore[assignment]
            rc = mod.main([
                "--events-csv", str(events_csv),
                "--symbols", "MES.v.0",
                "--event-type", "CPI",
                "--out", str(out_dir),
                "--workers", "1",
                "--bands", "1.0",
                "--shard", "1/2",  # AAA_EVT_A hashes to shard 1/2
            ])
        finally:
            _rm.run_all_hypotheses_replay = _orig

        assert rc == 0
        payload = json.loads((out_dir / "universe_result.json").read_text(encoding="utf-8"))
        assert payload["cli_args"]["shard"] == "1/2"
        assert payload["cli_args"]["shard_index"] == 1
        assert payload["cli_args"]["shard_total"] == 2
        # Confirm the shard ran units (not the zero-units early-exit path)
        assert payload.get("units_run", 0) >= 1
