"""Workbench history gate fail-open semantics."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backtest_pipeline.src.signal_backtester import BacktestResult
from workbench.src.core.composition import ModelComposition
from workbench.src.core.protocol import ModelConfig
from workbench.src.data.manifest import DatasetManifest


class _FakeContext:
    def __init__(self, artifact_dir: Path):
        self.run_id = "TEST_RUN"
        self.artifact_dir = artifact_dir
        self.metadata = {}

    def write_reproducibility_files(self) -> None:
        self.artifact_dir.mkdir(parents=True, exist_ok=True)


class _FakeLoader:
    duplicate_order_ids = 0
    monotonic_violations = 0

    def __init__(self, *args, **kwargs):
        self.report = SimpleNamespace(
            event_count=1,
            gap_count=0,
            duplicate_order_ids=self.duplicate_order_ids,
            monotonic_violations=self.monotonic_violations,
        )

    def mark_snapshot_available(self) -> None:
        pass

    def load(self, path: str):
        return []


class _FakeModel:
    def validate_inputs(self, ctx):
        return []

    def run_backtest(self, ctx):
        return BacktestResult(
            hypothesis_id=1,
            net_pnl=100.0,
            num_trades=1,
            win_rate=1.0,
            expectancy=100.0,
            adverse_selection_ticks=0.0,
            tail_loss=0.0,
        )

    def produce_diagnostics(self, ctx, result):
        return SimpleNamespace(metrics={})


def _patch_engine(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    duplicate_order_ids: int = 0,
    monotonic_violations: int = 0,
) -> _FakeContext:
    from workbench.src.run import engine as engine_mod

    artifact_dir = tmp_path / "artifacts"
    ctx = _FakeContext(artifact_dir)
    cfg = ModelConfig(
        model_id="TEST_MODEL",
        kind="pdf",
        name="Test Model",
        min_history_years=10,
        latency_lane="sub_10ms",
        robustness_window="test",
    )
    composition = ModelComposition(primary_model_id="TEST_MODEL")
    profile = SimpleNamespace(
        measured_production_p99_ms=1.0,
        measured_production_p99_us=1000.0,
        injection_sweep_us=[0, 1000],
        to_report_dict=lambda: {"measured_production_p99_us": 1000.0},
    )
    latency_summary = tmp_path / "runtime" / "latency_reports" / "latency_summary.json"
    latency_summary.parent.mkdir(parents=True, exist_ok=True)
    latency_summary.write_text('{"run_id": "test_chi404_latency"}\n', encoding="utf-8")
    viability = SimpleNamespace(
        survives_cpp_execution_delay=True,
        simulated_latency_adjusted_pnl=100.0,
        measured_production_p99_ms=1.0,
        measured_production_p99_us=1000.0,
        cpp_hot_path_runtime_us=1000.0,
        breakeven_us=5000.0,
        breakeven_ms=5.0,
        latency_profitability_buffer_us=4000.0,
        latency_buffer_ms=4.0,
        lane_required="sub_10ms",
        lane_measured="sub_10ms",
        lane_pass=True,
        recommendation="VIABLE",
        pnl_by_latency={0.0: 100.0},
        cpp_latency_profile={"measured_production_p99_us": 1000.0},
    )
    robustness = SimpleNamespace(passed=True, overfit_risk="LOW")
    loader_cls = type(
        "ConfiguredFakeLoader",
        (_FakeLoader,),
        {
            "duplicate_order_ids": duplicate_order_ids,
            "monotonic_violations": monotonic_violations,
        },
    )

    monkeypatch.setattr(engine_mod, "resolve_model_id", lambda model_id: model_id)
    monkeypatch.setattr(engine_mod.CompositionOrchestrator, "default_composition", lambda model_id: composition)
    monkeypatch.setattr(engine_mod, "get_model_config", lambda model_id: cfg)
    monkeypatch.setattr(engine_mod, "L3Loader", loader_cls)
    monkeypatch.setattr(engine_mod.CppLatencyProfile, "from_chi404_summary", lambda path: profile)
    monkeypatch.setattr(engine_mod.LatencyPolicy, "from_cpp_profile", lambda cpp_profile: SimpleNamespace())
    monkeypatch.setattr(engine_mod.RunContext, "build", lambda *args, **kwargs: ctx)
    monkeypatch.setattr(engine_mod, "get_cached_stack_verify", lambda repo: SimpleNamespace(
        stack_verified=True,
        checks={},
        reason="ok",
    ))
    monkeypatch.setattr(engine_mod, "get_model_by_id", lambda model_id: _FakeModel())
    monkeypatch.setattr(engine_mod, "summarize_phase5_timestamp_schema", lambda *args, **kwargs: {
        "complete": True,
        "monotonic_non_decreasing": True,
    })
    monkeypatch.setattr(engine_mod, "sweep_injection_pnl", lambda *args, **kwargs: {0: 100.0, 1000: 100.0})
    monkeypatch.setattr(engine_mod, "analyze_latency_viability", lambda *args, **kwargs: viability)
    monkeypatch.setattr(engine_mod, "load_current_low_latency_status", lambda repo: "PASS")
    monkeypatch.setattr(engine_mod, "build_latency_operating_envelope", lambda *args, **kwargs: {
        "status": "PASS",
        "checks": {
            "operating_envelope_generated": {"passed": True},
            "low_latency_execution_path_audit": {"passed": True},
            "placement_speed_sensitivity": {"passed": True},
            "async_ack_state_risk": {"passed": True},
            "pending_exposure_guardrails": {"passed": True},
            "composition_latency_feasibility": {"passed": True},
            "competitor_speed_sensitivity": {"passed": True},
        },
        "promotion_blockers": [],
    })
    monkeypatch.setattr(engine_mod, "write_latency_operating_envelope", lambda *args, **kwargs: None)
    monkeypatch.setattr(engine_mod, "compact_envelope_fields", lambda envelope: {"status": envelope["status"]})
    monkeypatch.setattr(engine_mod, "run_robustness_pack", lambda *args, **kwargs: robustness)
    monkeypatch.setattr(engine_mod, "build_certification_stamp", lambda **kwargs: {"promotion_eligible": True})
    monkeypatch.setattr(engine_mod, "format_stamp_footer", lambda stamp: "certified")
    monkeypatch.setattr(engine_mod, "render_markdown_report", lambda *args, **kwargs: "# report")
    monkeypatch.setattr(engine_mod, "generate_pdf_research_card", lambda *args, **kwargs: {})

    return ctx


def test_manifest_gate_error_prioritizes_l3_quality_before_history_and_coverage():
    coverage_summary = {
        "coverage_status": "BELOW_MINIMUM",
        "minimum_required_days": 250,
        "valid_trading_days": 12,
    }

    monotonic_manifest = DatasetManifest(
        npz_path="event.npz",
        event_id="TEST_EVENT",
        event_count=10,
        gap_count=0,
        duplicate_order_ids=2,
        monotonic_violations=1,
        min_history_years_required=10,
        history_years_available=0.0,
        data_sufficient=False,
        extra={"coverage_summary": coverage_summary},
    )
    duplicate_manifest = DatasetManifest(
        npz_path="event.npz",
        event_id="TEST_EVENT",
        event_count=10,
        gap_count=0,
        duplicate_order_ids=2,
        monotonic_violations=0,
        min_history_years_required=10,
        history_years_available=0.0,
        data_sufficient=False,
        extra={"coverage_summary": coverage_summary},
    )

    assert monotonic_manifest.gate_error() == "DATA_QUALITY_MONOTONIC_VIOLATIONS: 1"
    assert duplicate_manifest.gate_error() == "DATA_QUALITY_DUPLICATE_ADD_ORDER_IDS: 2"


def test_skip_history_gate_does_not_force_data_sufficient(tmp_path, monkeypatch):
    from workbench.src.run.engine import WorkbenchEngine

    ctx = _patch_engine(monkeypatch, tmp_path)
    npz_path = tmp_path / "event.npz"
    npz_path.write_bytes(b"npz")

    out = WorkbenchEngine(tmp_path).run(
        "TEST_MODEL",
        "TEST_EVENT",
        npz_path=npz_path,
        skip_history_gate=True,
        coverage_summary={
            "coverage_status": "BELOW_MINIMUM",
            "minimum_required_days": 250,
            "valid_trading_days": 12,
        },
    )

    assert out["promote_candidate"] is False
    assert out["report"]["promote_candidate"] is False
    assert out["report"]["data_sufficient"] is False
    assert out["report"]["history_gate_skipped"] is True
    assert out["report"]["data_gate_error"] == "DATA_INSUFFICIENT: need 250 valid trading days, have 12"
    assert ctx.metadata["data_sufficient"] is False
    assert ctx.metadata["history_gate_skipped"] is True
    assert ctx.metadata["data_gate_error"] == out["report"]["data_gate_error"]

    manifest = json.loads((ctx.artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["data_sufficient"] is False


def test_skip_history_gate_allows_run_but_blocks_l3_quality_promotion(tmp_path, monkeypatch):
    from workbench.src.run.engine import WorkbenchEngine

    ctx = _patch_engine(
        monkeypatch,
        tmp_path,
        duplicate_order_ids=2,
        monotonic_violations=1,
    )
    npz_path = tmp_path / "event.npz"
    npz_path.write_bytes(b"npz")

    out = WorkbenchEngine(tmp_path).run(
        "TEST_MODEL",
        "TEST_EVENT",
        npz_path=npz_path,
        skip_history_gate=True,
        coverage_summary={
            "coverage_status": "PASS",
            "minimum_required_days": 250,
            "valid_trading_days": 250,
        },
    )

    assert out["promote_candidate"] is False
    assert out["report"]["promote_candidate"] is False
    assert out["report"]["data_sufficient"] is False
    assert out["report"]["data_gate_error"] == "DATA_QUALITY_MONOTONIC_VIOLATIONS: 1"
    assert ctx.metadata["data_sufficient"] is False
    assert ctx.metadata["data_gate_error"] == out["report"]["data_gate_error"]

    manifest = json.loads((ctx.artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["data_sufficient"] is False
    assert manifest["duplicate_order_ids"] == 2
    assert manifest["monotonic_violations"] == 1


@pytest.mark.parametrize("wfc_status", ["SKIPPED", "PENDING", "FAIL"])
def test_non_pass_wfc_status_blocks_promotion(tmp_path, monkeypatch, wfc_status):
    from workbench.src.run.engine import WorkbenchEngine

    _patch_engine(monkeypatch, tmp_path)
    npz_path = tmp_path / "event.npz"
    npz_path.write_bytes(b"npz")

    out = WorkbenchEngine(tmp_path).run(
        "TEST_MODEL",
        "TEST_EVENT",
        npz_path=npz_path,
        wfc_status=wfc_status,
        coverage_summary={
            "coverage_status": "PASS",
            "minimum_required_days": 250,
            "valid_trading_days": 250,
        },
    )

    assert out["promote_candidate"] is False
    assert out["report"]["promote_candidate"] is False
    assert out["report"]["wfc_status"] == wfc_status


def test_missing_chi404_latency_summary_blocks_run(tmp_path, monkeypatch):
    from workbench.src.run.engine import WorkbenchEngine

    _patch_engine(monkeypatch, tmp_path)
    latency_summary = tmp_path / "runtime" / "latency_reports" / "latency_summary.json"
    latency_summary.unlink()
    npz_path = tmp_path / "event.npz"
    npz_path.write_bytes(b"npz")

    with pytest.raises(FileNotFoundError, match="CHI404 latency summary missing"):
        WorkbenchEngine(tmp_path).run(
            "TEST_MODEL",
            "TEST_EVENT",
            npz_path=npz_path,
            coverage_summary={
                "coverage_status": "PASS",
                "minimum_required_days": 250,
                "valid_trading_days": 250,
            },
        )


def test_wfc_pass_preserves_promotion_when_other_gates_pass(tmp_path, monkeypatch):
    from workbench.src.run.engine import WorkbenchEngine

    _patch_engine(monkeypatch, tmp_path)
    npz_path = tmp_path / "event.npz"
    npz_path.write_bytes(b"npz")

    out = WorkbenchEngine(tmp_path).run(
        "TEST_MODEL",
        "TEST_EVENT",
        npz_path=npz_path,
        wfc_status="PASS",
        coverage_summary={
            "coverage_status": "PASS",
            "minimum_required_days": 250,
            "valid_trading_days": 250,
        },
    )

    assert out["promote_candidate"] is True
    assert out["report"]["promote_candidate"] is True
    assert out["report"]["wfc_status"] == "PASS"
    assert out["report"]["latency_authority"]["authority"] == "chi404_cpp_latency_summary"
    assert out["report"]["latency_authority"]["python_research_runtime_authoritative"] is False
    summary_path = Path(out["report"]["latency_authority"]["summary_path"])
    assert summary_path.parts[-3:] == ("runtime", "latency_reports", "latency_summary.json")


def test_history_gate_raises_when_not_skipped(tmp_path, monkeypatch):
    from workbench.src.run.engine import WorkbenchEngine

    _patch_engine(monkeypatch, tmp_path)
    npz_path = tmp_path / "event.npz"
    npz_path.write_bytes(b"npz")

    with pytest.raises(RuntimeError, match="DATA_INSUFFICIENT: need 250 valid trading days, have 12"):
        WorkbenchEngine(tmp_path).run(
            "TEST_MODEL",
            "TEST_EVENT",
            npz_path=npz_path,
            skip_history_gate=False,
            coverage_summary={
                "coverage_status": "BELOW_MINIMUM",
                "minimum_required_days": 250,
                "valid_trading_days": 12,
            },
        )
