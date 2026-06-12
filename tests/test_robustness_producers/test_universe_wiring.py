"""Tests that run_event_universe.py emits the robustness block in universe_result.json.

Mirrors the fixture setup from tests/test_run_event_universe.py::TestEndToEndSmoke
(same events CSV, same NPZ fixture, same mocked replay stub, same workers=1 / max-events=1
invocation) and additionally asserts that universe_result.json contains a
"robustness" block with producer_version="rp_v1".
"""
from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
if str(_REPO / "packages") not in sys.path:
    sys.path.insert(0, str(_REPO / "packages"))

from hft3_bootstrap import setup_repo_paths
setup_repo_paths()


# ---------------------------------------------------------------------------
# Helpers copied from test_run_event_universe.py (keep in sync)
# ---------------------------------------------------------------------------

_EVENTS_CSV_CONTENT = """\
event_id,event_type,release_date,release_time,timezone,window_name,start_offset_seconds,end_offset_seconds,symbols,priority,source,source_url,effective_date,notes,row_status
AAA_EVT_A,CPI,2024-01-10,08:30:00,America/New_York,TIGHT,-30,300,"MES.v.0",50,TEST,http://example.com,2024-01-01,test,SOURCED
BBB_EVT_B,NFP,2024-02-02,08:30:00,America/New_York,TIGHT,-30,300,"MES.v.0",50,TEST,http://example.com,2024-01-01,test,SOURCED
ZZZ_NO_NPZ,CPI,2024-03-13,08:30:00,America/New_York,TIGHT,-30,300,"MES.v.0",50,TEST,http://example.com,2024-01-01,test,SOURCED
"""


def _load_fresh_universe_mod(name: str):
    script = _REPO / "scripts" / "run_event_universe.py"
    spec = importlib.util.spec_from_file_location(name, script)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _fake_hyp_results_for(hypotheses, npz_path: str, *, latency_ms: float = 1.0, **kw) -> dict:
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def events_csv(tmp_path: Path) -> Path:
    p = tmp_path / "events.csv"
    p.write_text(_EVENTS_CSV_CONTENT, encoding="utf-8")
    return p


@pytest.fixture()
def minimal_npz(tmp_path: Path) -> Path:
    from backtest_pipeline.src.replay_npz_fixture import build_minimal_mbo_npz

    npz = tmp_path / "MES.v.0_AAA_EVT_A_mbo.npz"
    build_minimal_mbo_npz(npz)
    return npz


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRobustnessBlockPresent:
    def test_robustness_block_in_result_json(self, tmp_path, events_csv, minimal_npz):
        """universe_result.json must contain a 'robustness' block with producer_version."""
        import backtest_pipeline.src.replay_matrix as _rm
        _orig_fn = _rm.run_all_hypotheses_replay

        out_dir = tmp_path / "out_robustness"
        mod = _load_fresh_universe_mod("run_eu_robustness_wiring")
        mod.load_lake_index = lambda _, rescan=False: {("MES.v.0", "AAA_EVT_A"): str(minimal_npz)}

        try:
            _rm.run_all_hypotheses_replay = _fake_hyp_results_for  # type: ignore[assignment]

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
        assert result_path.exists()

        payload = json.loads(result_path.read_text(encoding="utf-8"))

        # Top-level keys still intact
        assert payload["schema"] == "universe_result_v1"
        assert "aggregated" in payload
        assert "corrections" in payload

        # Robustness block present
        assert "robustness" in payload, (
            f"Expected 'robustness' key in universe_result.json; got keys: {list(payload.keys())}"
        )
        robustness = payload["robustness"]
        # rp_v2 adds R6 fee/slippage stress; rp_v1 is the baseline DSR/PBO/bootstrap
        assert robustness.get("producer_version") in ("rp_v1", "rp_v2"), (
            f"Expected producer_version in ('rp_v1','rp_v2'), got: {robustness.get('producer_version')}"
        )

    def test_robustness_block_has_required_sub_keys(self, tmp_path, events_csv, minimal_npz):
        """robustness block must contain dsr_by_cell, bootstrap_by_cell, and pbo."""
        import backtest_pipeline.src.replay_matrix as _rm
        _orig_fn = _rm.run_all_hypotheses_replay

        out_dir = tmp_path / "out_robustness_keys"
        mod = _load_fresh_universe_mod("run_eu_robustness_keys")
        mod.load_lake_index = lambda _, rescan=False: {("MES.v.0", "AAA_EVT_A"): str(minimal_npz)}

        try:
            _rm.run_all_hypotheses_replay = _fake_hyp_results_for  # type: ignore[assignment]

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
        payload = json.loads((out_dir / "universe_result.json").read_text(encoding="utf-8"))
        robustness = payload["robustness"]

        for required_key in ("dsr_by_cell", "bootstrap_by_cell", "pbo", "producer_version"):
            assert required_key in robustness, (
                f"robustness block missing key '{required_key}'; got: {list(robustness.keys())}"
            )

    def test_dsr_by_cell_has_correct_schema(self, tmp_path, events_csv, minimal_npz):
        """Each entry in dsr_by_cell must have the expected DSR field names."""
        import backtest_pipeline.src.replay_matrix as _rm
        _orig_fn = _rm.run_all_hypotheses_replay

        out_dir = tmp_path / "out_dsr_schema"
        mod = _load_fresh_universe_mod("run_eu_dsr_schema")
        mod.load_lake_index = lambda _, rescan=False: {("MES.v.0", "AAA_EVT_A"): str(minimal_npz)}

        try:
            _rm.run_all_hypotheses_replay = _fake_hyp_results_for  # type: ignore[assignment]

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
        payload = json.loads((out_dir / "universe_result.json").read_text(encoding="utf-8"))
        dsr_by_cell = payload["robustness"]["dsr_by_cell"]

        # At least one cell should be present (we ran one event × one hypothesis × one band)
        assert len(dsr_by_cell) >= 1, "Expected at least one DSR entry"

        dsr_required_fields = {"sharpe", "dsr_cdf", "dsr_pass", "one_sided_p", "n_obs", "n_trials", "skew", "kurt", "reason"}
        for slug, entry in dsr_by_cell.items():
            missing = dsr_required_fields - set(entry.keys())
            assert not missing, f"DSR entry '{slug}' missing fields: {missing}"

    def test_bootstrap_by_cell_has_correct_schema(self, tmp_path, events_csv, minimal_npz):
        """Each entry in bootstrap_by_cell must have mean, ci_lo_95, ci_hi_95, n."""
        import backtest_pipeline.src.replay_matrix as _rm
        _orig_fn = _rm.run_all_hypotheses_replay

        out_dir = tmp_path / "out_boot_schema"
        mod = _load_fresh_universe_mod("run_eu_boot_schema")
        mod.load_lake_index = lambda _, rescan=False: {("MES.v.0", "AAA_EVT_A"): str(minimal_npz)}

        try:
            _rm.run_all_hypotheses_replay = _fake_hyp_results_for  # type: ignore[assignment]

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
        payload = json.loads((out_dir / "universe_result.json").read_text(encoding="utf-8"))
        bootstrap_by_cell = payload["robustness"]["bootstrap_by_cell"]

        boot_required_fields = {"mean", "ci_lo_95", "ci_hi_95", "n"}
        for slug, entry in bootstrap_by_cell.items():
            missing = boot_required_fields - set(entry.keys())
            assert not missing, f"bootstrap entry '{slug}' missing fields: {missing}"

    def test_pbo_block_has_correct_schema(self, tmp_path, events_csv, minimal_npz):
        """pbo block must have pbo, n_splits, n_configs, n_partitions, reason keys."""
        import backtest_pipeline.src.replay_matrix as _rm
        _orig_fn = _rm.run_all_hypotheses_replay

        out_dir = tmp_path / "out_pbo_schema"
        mod = _load_fresh_universe_mod("run_eu_pbo_schema")
        mod.load_lake_index = lambda _, rescan=False: {("MES.v.0", "AAA_EVT_A"): str(minimal_npz)}

        try:
            _rm.run_all_hypotheses_replay = _fake_hyp_results_for  # type: ignore[assignment]

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
        payload = json.loads((out_dir / "universe_result.json").read_text(encoding="utf-8"))
        pbo_block = payload["robustness"]["pbo"]

        pbo_required_fields = {"n_splits", "n_configs", "n_partitions", "reason"}
        for field in pbo_required_fields:
            assert field in pbo_block, f"pbo block missing field '{field}'; got: {list(pbo_block.keys())}"
        # pbo key itself present (may be None when <8 events)
        assert "pbo" in pbo_block

    def test_existing_keys_not_disturbed(self, tmp_path, events_csv, minimal_npz):
        """Robustness wiring must not alter any existing top-level keys."""
        import backtest_pipeline.src.replay_matrix as _rm
        _orig_fn = _rm.run_all_hypotheses_replay

        out_dir = tmp_path / "out_existing_keys"
        mod = _load_fresh_universe_mod("run_eu_existing_keys")
        mod.load_lake_index = lambda _, rescan=False: {("MES.v.0", "AAA_EVT_A"): str(minimal_npz)}

        try:
            _rm.run_all_hypotheses_replay = _fake_hyp_results_for  # type: ignore[assignment]

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
        payload = json.loads((out_dir / "universe_result.json").read_text(encoding="utf-8"))

        # All original keys must still be present
        for key in ("schema", "units_run", "units_skipped", "units_errored",
                    "aggregated", "corrections", "certification_stamp",
                    "embargo", "unit_results"):
            assert key in payload, f"Original key '{key}' missing from universe_result.json"
