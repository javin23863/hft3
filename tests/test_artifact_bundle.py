"""Phase 12 tests for the artifact bundle validator.

Covers:
- test_required_artifacts_constant: 17 files defined
- test_validate_bundle_complete: all 17 files present → pass
- test_validate_bundle_incomplete: missing files → fail
- test_validate_bundle_optional_present: optional files are noted
- test_to_gate_result_complete: complete bundle → INFO gate
- test_to_gate_result_incomplete: incomplete bundle → BLOCKING gate
- test_runner_writes_all_17_artifacts: the runner writes all 17 required files
- test_runner_writes_git_commit_txt: git_commit.txt contains the git SHA
- test_runner_writes_execution_assumptions_json: execution_assumptions.json
  contains the latency profile
- test_runner_writes_logs_txt: logs.txt is written
- test_runner_bundle_validation_passes: the runner's bundle validation passes
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hft3.artifact_bundle import (
    REQUIRED_ARTIFACTS,
    ArtifactBundleResult,
    to_gate_result,
    validate_bundle,
)
from hft3.research.run_autonomous import AutonomousRunner, CampaignConfig
from hft3.validation.gate_result import GateCategory, Severity


# ---------- REQUIRED_ARTIFACTS constant ----------


def test_required_artifacts_constant() -> None:
    """The spec requires 17 files (plus report.md and logs.txt = 19 total)."""
    assert len(REQUIRED_ARTIFACTS) == 19
    assert "manifest.json" in REQUIRED_ARTIFACTS
    assert "config_snapshot.yaml" in REQUIRED_ARTIFACTS
    assert "git_commit.txt" in REQUIRED_ARTIFACTS
    assert "execution_assumptions.json" in REQUIRED_ARTIFACTS
    assert "logs.txt" in REQUIRED_ARTIFACTS
    assert "report.md" in REQUIRED_ARTIFACTS


# ---------- validate_bundle ----------


def test_validate_bundle_complete(tmp_path: Path) -> None:
    """All 17 files present → pass."""
    for name in REQUIRED_ARTIFACTS:
        (tmp_path / name).write_text("dummy", encoding="utf-8")
    result = validate_bundle(tmp_path)
    assert result.pass_fail is True
    assert result.reason_code == "ARTIFACT_BUNDLE_COMPLETE"
    assert len(result.required_present) == len(REQUIRED_ARTIFACTS)
    assert len(result.required_missing) == 0


def test_validate_bundle_incomplete(tmp_path: Path) -> None:
    """Missing files → fail."""
    # Write only 5 of the 17 required files
    for name in REQUIRED_ARTIFACTS[:5]:
        (tmp_path / name).write_text("dummy", encoding="utf-8")
    result = validate_bundle(tmp_path)
    assert result.pass_fail is False
    assert result.reason_code == "ARTIFACT_BUNDLE_INCOMPLETE"
    assert len(result.required_present) == 5
    assert len(result.required_missing) == len(REQUIRED_ARTIFACTS) - 5


def test_validate_bundle_optional_present(tmp_path: Path) -> None:
    """Optional files are noted but don't affect pass/fail."""
    for name in REQUIRED_ARTIFACTS:
        (tmp_path / name).write_text("dummy", encoding="utf-8")
    # Add an optional file
    (tmp_path / "data_resolution.json").write_text("{}", encoding="utf-8")
    result = validate_bundle(tmp_path)
    assert result.pass_fail is True
    assert "data_resolution.json" in result.optional_present


# ---------- gate derivation ----------


def test_to_gate_result_complete(tmp_path: Path) -> None:
    """Complete bundle → INFO gate (not blocking)."""
    for name in REQUIRED_ARTIFACTS:
        (tmp_path / name).write_text("dummy", encoding="utf-8")
    result = validate_bundle(tmp_path)
    gr = to_gate_result(result)
    assert gr.gate_category == GateCategory.ARTIFACT_COMPLETENESS
    assert gr.severity == Severity.INFO
    assert gr.blocking_status is False
    assert gr.pass_fail is True
    assert gr.reason_code == "ARTIFACT_BUNDLE_COMPLETE"


def test_to_gate_result_incomplete(tmp_path: Path) -> None:
    """Incomplete bundle → BLOCKING gate."""
    for name in REQUIRED_ARTIFACTS[:5]:
        (tmp_path / name).write_text("dummy", encoding="utf-8")
    result = validate_bundle(tmp_path)
    gr = to_gate_result(result)
    assert gr.gate_category == GateCategory.ARTIFACT_COMPLETENESS
    assert gr.severity == Severity.BLOCKING
    assert gr.blocking_status is True
    assert gr.pass_fail is False
    assert gr.reason_code == "ARTIFACT_BUNDLE_INCOMPLETE"


# ---------- runner integration ----------


def _config() -> CampaignConfig:
    return CampaignConfig.from_yaml(Path("configs/research/autonomous_hft3.yaml"))


def test_runner_writes_all_17_artifacts(tmp_path: Path) -> None:
    """The runner writes all 17 required files."""
    cfg = _config()
    cfg.output["artifacts_dir"] = str(tmp_path / "artifacts")
    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="AB1")
    runner.run()
    bundle_dir = tmp_path / "artifacts" / "AB1"
    for name in REQUIRED_ARTIFACTS:
        assert (bundle_dir / name).is_file(), f"missing artifact: {name}"


def test_runner_writes_git_commit_txt(tmp_path: Path) -> None:
    """git_commit.txt contains the git SHA."""
    cfg = _config()
    cfg.output["artifacts_dir"] = str(tmp_path / "artifacts")
    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="AB2")
    runner.run()
    git_commit_path = tmp_path / "artifacts" / "AB2" / "git_commit.txt"
    content = git_commit_path.read_text(encoding="utf-8").strip()
    assert content == runner.state.git_sha


def test_runner_writes_execution_assumptions_json(tmp_path: Path) -> None:
    """execution_assumptions.json contains the latency profile."""
    cfg = _config()
    cfg.output["artifacts_dir"] = str(tmp_path / "artifacts")
    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="AB3")
    runner.run()
    ea_path = tmp_path / "artifacts" / "AB3" / "execution_assumptions.json"
    payload = json.loads(ea_path.read_text(encoding="utf-8"))
    assert "fill_model" in payload
    assert "slippage_bps" in payload
    assert "decision_to_send_us" in payload


def test_runner_writes_logs_txt(tmp_path: Path) -> None:
    """logs.txt is written."""
    cfg = _config()
    cfg.output["artifacts_dir"] = str(tmp_path / "artifacts")
    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="AB4")
    runner.run()
    logs_path = tmp_path / "artifacts" / "AB4" / "logs.txt"
    assert logs_path.is_file()
    content = logs_path.read_text(encoding="utf-8")
    assert "AB4" in content


def test_runner_bundle_validation_passes(tmp_path: Path) -> None:
    """The runner's bundle validation passes (all 17 files present)."""
    cfg = _config()
    cfg.output["artifacts_dir"] = str(tmp_path / "artifacts")
    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="AB5")
    runner.run()
    validation_path = tmp_path / "artifacts" / "AB5" / "artifact_bundle_validation.json"
    payload = json.loads(validation_path.read_text(encoding="utf-8"))
    assert payload["pass_fail"] is True
    assert payload["reason_code"] == "ARTIFACT_BUNDLE_COMPLETE"
    assert len(payload["required_missing"]) == 0
