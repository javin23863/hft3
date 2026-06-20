from __future__ import annotations

import json
from pathlib import Path

import pytest

import hft3.research.run_autonomous as run_autonomous
from hft3.research.run_autonomous import AutonomousRunner, CampaignConfig, RecoveryDecision
from hft3.validation.certification_registry import CertificationRecord, save_registry
from hft3.validation.gate_result import GateCategory, GateResult, Severity, write_robustness_gates_json


CONFIG_PATH = Path("configs/research/autonomous_hft3.yaml")


def _config(tmp_path: Path) -> CampaignConfig:
    cfg = CampaignConfig.from_yaml(CONFIG_PATH)
    cfg.output["artifacts_dir"] = str(tmp_path / "artifacts")
    cfg.data["event_windows"] = [
        {
            "event_id": "pytest",
            "start_ns": 1,
            "end_ns": 2,
            "symbols": ["MES.v.0"],
        }
    ]
    cfg.models["alpha"] = ["HYP_1"]
    cfg.models["select"] = {"roles": ["alpha"]}
    return cfg


def _write_passing_robustness_gates(path: Path, run_id: str, git_sha: str) -> None:
    gates = [
        GateResult(
            gate_name="data_resolution_eligibility",
            gate_category=GateCategory.DATA_INTEGRITY,
            metric_name="promotion_eligibility_impact",
            threshold=None,
            observed_value="eligible",
            comparison_operator="==",
            pass_fail=True,
            severity=Severity.INFO,
            blocking_status=False,
            artifact_reference="data_resolution.json",
        ),
        GateResult(
            gate_name="monte_carlo_sharpe_p05",
            gate_category=GateCategory.ROBUSTNESS,
            metric_name="sharpe_p05",
            threshold=0.5,
            observed_value=0.7,
            comparison_operator=">=",
            pass_fail=True,
            severity=Severity.BLOCKING,
        ),
        GateResult(
            gate_name="oos_max_drawdown",
            gate_category=GateCategory.DRAWDOWN_TAIL_RISK,
            metric_name="max_drawdown",
            threshold=-0.10,
            observed_value=-0.05,
            comparison_operator=">=",
            pass_fail=True,
            severity=Severity.BLOCKING,
        ),
        GateResult(
            gate_name="walk_forward_pass",
            gate_category=GateCategory.WALK_FORWARD,
            metric_name="wf_passed",
            threshold=1.0,
            observed_value=1.0,
            comparison_operator="==",
            pass_fail=True,
            severity=Severity.BLOCKING,
        ),
        GateResult(
            gate_name="artifact_completeness",
            gate_category=GateCategory.ARTIFACT_COMPLETENESS,
            metric_name="expected_files_present",
            threshold=1.0,
            observed_value=1.0,
            comparison_operator="==",
            pass_fail=True,
            severity=Severity.BLOCKING,
        ),
        GateResult(
            gate_name="double_wf_correlation",
            gate_category=GateCategory.WALK_FORWARD_CORRELATION,
            metric_name="spearman",
            threshold=0.2,
            observed_value=0.3,
            comparison_operator=">=",
            pass_fail=True,
            severity=Severity.BLOCKING,
        ),
    ]
    write_robustness_gates_json(path, gates, tier="T3", run_id=run_id, git_sha=git_sha)


def test_corrupt_state_requires_manual_review_and_run_fails(tmp_path: Path) -> None:
    state_dir = tmp_path / "runtime" / "research" / "BADSTATE"
    state_dir.mkdir(parents=True)
    (state_dir / "state.json").write_text("{not-json", encoding="utf-8")

    runner = AutonomousRunner(config=_config(tmp_path), root=tmp_path, run_id="BADSTATE")

    assert runner.recovery_decision == RecoveryDecision.MANUAL_REVIEW_REQUIRED
    assert runner.run() == 3
    assert not (tmp_path / "artifacts" / "BADSTATE" / "manifest.json").exists()


def test_checkpoint_identity_mismatch_requires_manual_review_and_run_fails(tmp_path: Path) -> None:
    state_dir = tmp_path / "runtime" / "research" / "IDMISMATCH"
    state_dir.mkdir(parents=True)
    (state_dir / "state.json").write_text(json.dumps({
        "run_id": "OTHER-RUN",
        "campaign_id": "other-campaign",
        "git_sha": "test-sha",
        "started_at": "2026-06-03T00:00:00+00:00",
        "last_updated_at": "2026-06-03T00:00:01+00:00",
        "completed_stages": [],
        "artifacts": {},
        "config_hash": "",
        "config_snapshot_path": "",
    }), encoding="utf-8")

    runner = AutonomousRunner(config=_config(tmp_path), root=tmp_path, run_id="IDMISMATCH")

    assert runner.recovery_decision == RecoveryDecision.MANUAL_REVIEW_REQUIRED
    assert runner.run() == 3
    assert not (tmp_path / "artifacts" / "IDMISMATCH" / "manifest.json").exists()


def test_checkpoint_timestamp_regression_requires_manual_review_and_run_fails(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    state_dir = tmp_path / "runtime" / "research" / "BADTIME"
    state_dir.mkdir(parents=True)
    (state_dir / "state.json").write_text(json.dumps({
        "run_id": "BADTIME",
        "campaign_id": cfg.campaign_id,
        "git_sha": "test-sha",
        "started_at": "2026-06-03T00:00:01+00:00",
        "last_updated_at": "2026-06-03T00:00:00+00:00",
        "completed_stages": [],
        "artifacts": {},
        "config_hash": "",
        "config_snapshot_path": "",
    }), encoding="utf-8")

    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="BADTIME")

    assert runner.recovery_decision == RecoveryDecision.MANUAL_REVIEW_REQUIRED
    assert runner.run() == 3
    assert not (tmp_path / "artifacts" / "BADTIME" / "manifest.json").exists()


def test_completed_stage_missing_artifact_requires_manual_review_and_run_fails(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="MISSART")
    path = runner.stage_resolve_features()
    path.unlink()

    resumed = AutonomousRunner(config=cfg, root=tmp_path, run_id="MISSART")

    assert resumed.run() == 3
    assert resumed.recovery_decision == RecoveryDecision.MANUAL_REVIEW_REQUIRED
    assert not (tmp_path / "artifacts" / "MISSART" / "manifest.json").exists()
    assert not path.exists()


def test_completed_stage_corrupt_json_artifact_requires_manual_review_and_run_fails(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="CORRUPTART")
    path = runner.stage_resolve_features()
    path.write_text("{not-json", encoding="utf-8")

    resumed = AutonomousRunner(config=cfg, root=tmp_path, run_id="CORRUPTART")

    assert resumed.run() == 3
    assert resumed.recovery_decision == RecoveryDecision.MANUAL_REVIEW_REQUIRED
    assert not (tmp_path / "artifacts" / "CORRUPTART" / "manifest.json").exists()
    assert path.read_text(encoding="utf-8") == "{not-json"


def test_atomic_writes_leave_no_temp_on_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = AutonomousRunner(config=_config(tmp_path), root=tmp_path, run_id="ATOMIC")

    def fail_replace(src: object, dst: object) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(run_autonomous.os, "replace", fail_replace)
    with pytest.raises(OSError):
        runner._save_state()
    with pytest.raises(OSError):
        runner._write_artifact("atomic.json", {"ok": True})

    assert not list((tmp_path / "runtime" / "research" / "ATOMIC").glob("*.tmp"))
    assert not list((tmp_path / "artifacts" / "ATOMIC").glob("*.tmp"))


def test_write_artifact_rejects_nan_without_final_artifact(tmp_path: Path) -> None:
    runner = AutonomousRunner(config=_config(tmp_path), root=tmp_path, run_id="NANART")
    artifact = tmp_path / "artifacts" / "NANART" / "nan.json"

    with pytest.raises(ValueError):
        runner._write_artifact("nan.json", {"bad": float("nan")})

    assert not artifact.exists()


def test_registry_update_existing_marker_does_not_duplicate_registry_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="REGRESUME")
    runner._write_artifact("scoring_summary.json", {
        "decision": "PROMOTE",
        "reason": "test",
        "campaign_id": cfg.campaign_id,
        "run_id": "REGRESUME",
        "git_sha": runner.state.git_sha,
    })
    _write_passing_robustness_gates(
        tmp_path / "artifacts" / "REGRESUME" / "robustness_gates.json",
        "REGRESUME",
        runner.state.git_sha,
    )
    runner._write_artifact("registry_update.json", {
        "decision": "PROMOTE",
        "promoted_to_certification_registry": True,
        "reason": "test",
        "campaign_id": cfg.campaign_id,
        "run_id": "REGRESUME",
    })
    save_registry(CertificationRecord(
        latest_certification_run_id="CERT-AR-REGRESUME",
        latest_certification_commit=runner.state.git_sha,
        latest_certification_timestamp="2026-06-03T00:00:00+00:00",
        latest_certification_status="YELLOW",
    ), tmp_path)
    audit_path = tmp_path / "runtime" / "validation" / "certification_registry.jsonl"
    before = audit_path.read_text(encoding="utf-8")

    def fail_save(record: object, root: object) -> Path:
        raise AssertionError("registry save should not be called on marker resume")

    monkeypatch.setattr(run_autonomous, "save_certification_registry", fail_save)

    assert runner.stage_registry_update() == tmp_path / "artifacts" / "REGRESUME" / "registry_update.json"
    assert audit_path.read_text(encoding="utf-8") == before


def test_registry_update_corrupt_existing_marker_requires_manual_review_without_registry_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="REGCORRUPT")
    runner._write_artifact("scoring_summary.json", {
        "decision": "PROMOTE",
        "reason": "test",
        "campaign_id": cfg.campaign_id,
        "run_id": "REGCORRUPT",
        "git_sha": runner.state.git_sha,
    })
    marker_path = runner._write_artifact("registry_update.json", "{not-json")
    before = marker_path.read_text(encoding="utf-8")

    def fail_save(record: object, root: object) -> Path:
        raise AssertionError("registry save should not be called on corrupt marker")

    monkeypatch.setattr(run_autonomous, "save_certification_registry", fail_save)

    with pytest.raises(RuntimeError):
        runner.stage_registry_update()

    assert runner.recovery_decision == RecoveryDecision.MANUAL_REVIEW_REQUIRED
    assert marker_path.read_text(encoding="utf-8") == before
    assert not runner.state_path.exists()


def test_registry_update_non_object_existing_marker_requires_manual_review_without_registry_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="REGARRAY")
    runner._write_artifact("scoring_summary.json", {
        "decision": "PROMOTE",
        "reason": "test",
        "campaign_id": cfg.campaign_id,
        "run_id": "REGARRAY",
        "git_sha": runner.state.git_sha,
    })
    marker_path = runner._write_artifact("registry_update.json", [])
    before = marker_path.read_text(encoding="utf-8")

    def fail_save(record: object, root: object) -> Path:
        raise AssertionError("registry save should not be called on non-object marker")

    monkeypatch.setattr(run_autonomous, "save_certification_registry", fail_save)

    with pytest.raises(RuntimeError):
        runner.stage_registry_update()

    assert runner.recovery_decision == RecoveryDecision.MANUAL_REVIEW_REQUIRED
    assert marker_path.read_text(encoding="utf-8") == before
    assert not runner.state_path.exists()


def test_registry_update_mismatched_existing_marker_requires_manual_review_without_registry_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="REGMISMATCH")
    runner._write_artifact("scoring_summary.json", {
        "decision": "PROMOTE",
        "reason": "test",
        "campaign_id": cfg.campaign_id,
        "run_id": "REGMISMATCH",
        "git_sha": runner.state.git_sha,
    })
    marker_path = runner._write_artifact("registry_update.json", {
        "decision": "QUARANTINE",
        "promoted_to_certification_registry": False,
        "reason": "old",
        "campaign_id": cfg.campaign_id,
        "run_id": "REGMISMATCH",
    })
    before = marker_path.read_text(encoding="utf-8")

    def fail_save(record: object, root: object) -> Path:
        raise AssertionError("registry save should not be called on mismatched marker")

    monkeypatch.setattr(run_autonomous, "save_certification_registry", fail_save)

    with pytest.raises(RuntimeError):
        runner.stage_registry_update()

    assert runner.recovery_decision == RecoveryDecision.MANUAL_REVIEW_REQUIRED
    assert marker_path.read_text(encoding="utf-8") == before
    assert not runner.state_path.exists()


def test_completed_registry_update_mismatched_decision_requires_manual_review_without_rewrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path)
    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="REGDONEBADDECISION")

    assert runner.run() == 2

    marker_path = tmp_path / "artifacts" / "REGDONEBADDECISION" / "registry_update.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["decision"] = "REJECT"
    marker_path.write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    before = marker_path.read_text(encoding="utf-8")

    def fail_finalize() -> None:
        raise AssertionError("bundle finalization should not run after marker mismatch")

    resumed = AutonomousRunner(config=cfg, root=tmp_path, run_id="REGDONEBADDECISION")
    monkeypatch.setattr(resumed, "_finalize_bundle", fail_finalize)

    assert resumed.run() == 3
    assert resumed.recovery_decision == RecoveryDecision.MANUAL_REVIEW_REQUIRED
    assert marker_path.read_text(encoding="utf-8") == before


def test_autonomous_runner_has_no_live_or_routing_imports() -> None:
    src = Path("packages/hft3/research/run_autonomous.py").read_text(encoding="utf-8")
    forbidden = ("rithmic", "trade_manager", "execution_adapter", "submit_order", "route_order")
    assert not any(term in src.lower() for term in forbidden)
