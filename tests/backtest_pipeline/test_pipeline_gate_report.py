"""Tests for pipeline_gate_report finalize."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

import json
import importlib.util
from types import SimpleNamespace

from backtest_pipeline.src.pipeline_gate_report import finalize_catalog_models, write_catalog_artifacts


def test_finalize_smoke_fills_not_run_rows() -> None:
    executed = [
        {
            "model_id": "HYBRID_EXECUTION",
            "engine_kind": "pdf_hybrid_replay",
            "status": "PASS",
            "artifact_dir": "research_cards/HYBRID_EXECUTION_hybrid_replay",
            "num_trades": 5,
            "net_pnl_usd": 1.0,
            "backend_label": "ReplayRunner quote-engine (queue fills)",
        },
        {
            "model_id": "SECOND_WAVE_CONTINUATION",
            "engine_kind": "hyp_mbo",
            "status": "PASS",
            "artifact_dir": "research_cards/pipeline_runs/SECOND_WAVE_CONTINUATION_X",
            "num_trades": 1,
            "net_pnl_usd": 0.1,
            "backend_label": "SignalBacktester MBO pipeline (research path)",
        },
    ]
    models = finalize_catalog_models(executed, "smoke")
    assert len(models) == 56
    not_run = [m for m in models if m["status"] == "NOT_RUN_SMOKE"]
    assert len(not_run) == 54
    assert models[0]["model_id"]  # sorted by all_model_ids order


def test_write_catalog_artifacts_are_run_id_scoped(tmp_path: Path) -> None:
    models = [
        {
            "model_id": "HYBRID_EXECUTION",
            "engine_kind": "pdf_hybrid_replay",
            "status": "PASS",
            "artifact_dir": "research_cards/pipeline_runs/fresh/HYBRID_EXECUTION_X",
            "num_trades": 5,
            "net_pnl_usd": 1.0,
            "backend_label": "ReplayRunner quote-engine (queue fills)",
        }
    ]

    gate_path = write_catalog_artifacts(
        tmp_path,
        tier="catalog",
        event_id="EVENT_1",
        symbol="ES.v.0",
        models=models,
        steps=[{"step": "unit", "status": "PASS", "detail": "ok"}],
        overall_pass=True,
        run_id="fresh_all_lanes_1",
    )

    assert gate_path == tmp_path / "runtime" / "reports" / "full_pipeline_gate" / "fresh_all_lanes_1" / "manifest.json"
    assert not (tmp_path / "runtime" / "reports" / "full_pipeline_gate.json").exists()
    assert (tmp_path / "research_cards" / "pipeline_runs" / "fresh_all_lanes_1" / "result.json").is_file()
    assert (tmp_path / "research_cards" / "pipeline_runs" / "fresh_all_lanes_1" / "report.md").is_file()
    manifest = json.loads(gate_path.read_text(encoding="utf-8"))
    assert manifest["run_id"] == "fresh_all_lanes_1"
    assert manifest["artifact_reuse_policy"] == "active_run_id_only"
    assert manifest["artifact_dir"] == "research_cards/pipeline_runs/fresh_all_lanes_1"
    assert manifest["gate_report_path"] == "runtime/reports/full_pipeline_gate/fresh_all_lanes_1/manifest.json"


def test_full_gate_hybrid_block_uses_run_scoped_dirs(tmp_path: Path, monkeypatch) -> None:
    script = _REPO / "scripts" / "run_full_pipeline_gate.py"
    spec = importlib.util.spec_from_file_location("run_full_pipeline_gate_test", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.PIPELINE_RUNS = tmp_path / "research_cards" / "pipeline_runs" / "RUN_A"
    captured = {}

    class StubHybridGate:
        def run_defensive_ablation(self, event_id, npz, latency_ms, *, ablation_dir=None):
            captured["ablation_dir"] = ablation_dir
            return True, "ablation ok", {"matrix": {}}

        def run_hybrid_backtest(self, event_id, npz, latency_ms, *, card_dir=None):
            captured["card_dir"] = card_dir
            return True, "hybrid ok", {"result": {"num_trades": 1, "balance": 1.0}}

        def run_after_action_step(self, event_id, symbol, payload, ablation_bundle, latency_ms, *, aar_dir=None):
            captured["aar_dir"] = aar_dir
            return True, "aar ok", {}

    monkeypatch.setattr(module, "_load_hybrid_gate", lambda: StubHybridGate())
    monkeypatch.setattr(module, "route", lambda _model_id: SimpleNamespace(engine_kind="pdf_hybrid_replay", backend_label="hybrid"))

    ok, _detail, bundle = module._run_hybrid_block(
        "EVENT_1",
        tmp_path / "event.npz",
        "ES.v.0",
        None,
        skip_ablation=False,
        skip_after_action=False,
        min_trades=1,
    )

    assert ok is True
    assert captured["card_dir"] == module.PIPELINE_RUNS / "HYBRID_EXECUTION_EVENT_1"
    assert captured["ablation_dir"] == module.PIPELINE_RUNS / "HYBRID_EXECUTION_EVENT_1_ablation"
    assert captured["aar_dir"] == module.PIPELINE_RUNS / "HYBRID_EXECUTION_EVENT_1_aar"
    assert bundle["hybrid_row"]["artifact_dir"].endswith("research_cards/pipeline_runs/RUN_A/HYBRID_EXECUTION_EVENT_1")
