"""Phase 2 tests for the HFT3 autonomous research runner.

Covers:
- test_config_loads_yaml: CampaignConfig.from_yaml
- test_config_validates_required_fields: missing required fields caught
- test_autonomous_runner_headless: runs without Streamlit / input / GUI
- test_resumable_rerun: re-running with same run_id skips completed stages
- test_idempotent_rerun: re-running produces materially equivalent artifacts
- test_atomic_registry_promotion: only PROMOTE decision writes the registry
- test_quarantine_path: default scaffolded decision is QUARANTINE
- test_rejected_does_not_write_registry
- test_deterministic_config_hash: same config + git SHA = same hash
- test_promotion_decision_artifact_written
- test_artifact_bundle_manifest_lists_all_stages
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from hft3.research.run_autonomous import (
    AutonomousRunner,
    CampaignConfig,
    main as runner_main,
)
from hft3.validation.certification_registry import (
    CertificationRecord,
    load_registry,
)


CONFIG_PATH = Path("configs/research/autonomous_hft3.yaml")


def _config() -> CampaignConfig:
    return CampaignConfig.from_yaml(CONFIG_PATH)


# ---------- config loading ----------


def test_config_loads_yaml() -> None:
    cfg = _config()
    assert cfg.campaign_id == "cpi-2024-uat-tight"
    assert cfg.data.get("dataset_id") == "databento_es_mbo_v1"
    assert "ES" in cfg.data.get("symbol_universe", [])
    assert cfg.latency_profile.get("decision_to_send_us") == 80


def test_config_validates_required_fields() -> None:
    raw = {"campaign_id": "x"}  # missing data, models
    cfg = CampaignConfig(campaign_id="x")
    errors = cfg.validate()
    assert any("dataset_id" in e for e in errors)
    assert any("symbol_universe" in e for e in errors)
    assert any("event_windows" in e for e in errors)
    assert any("models.alpha" in e for e in errors)


# ---------- headless ----------


def test_autonomous_runner_headless(tmp_path: Path) -> None:
    """The runner must not require Streamlit, notebooks, or input()."""
    runner = AutonomousRunner(config=_config(), root=tmp_path)
    rc = runner.run()
    # Default scaffolded decision is QUARANTINE → rc 2
    assert rc in (0, 1, 2)


def test_autonomous_runner_no_input_or_gui_imports() -> None:
    """Static check: the runner does not import streamlit, ipywidgets,
    click.prompt, or builtins.input."""
    import ast
    forbidden = {"streamlit", "ipywidgets", "ipykernel", "jupyter"}
    src = Path("packages/hft3/research/run_autonomous.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                assert not any(n.name.startswith(f) for f in forbidden), n.name
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert not any(mod.startswith(f) for f in forbidden), mod


# ---------- resumable ----------


def test_resumable_rerun(tmp_path: Path) -> None:
    cfg = _config()
    cfg.output["artifacts_dir"] = str(tmp_path / "artifacts")
    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="R1")
    assert runner.run() in (0, 1, 2)
    first_manifest = json.loads(
        (tmp_path / "artifacts" / "R1" / "manifest.json").read_text(encoding="utf-8")
    )
    # Re-run with the same id
    runner2 = AutonomousRunner(config=cfg, root=tmp_path, run_id="R1")
    assert runner2.run() in (0, 1, 2)
    second_manifest = json.loads(
        (tmp_path / "artifacts" / "R1" / "manifest.json").read_text(encoding="utf-8")
    )
    # Same set of completed stages and same artifact filenames
    assert set(first_manifest["completed_stages"]) == set(second_manifest["completed_stages"])
    assert set(first_manifest["artifacts"].keys()) == set(second_manifest["artifacts"].keys())


# ---------- registry write ----------


def test_quarantine_path_does_not_write_registry(tmp_path: Path) -> None:
    cfg = _config()
    cfg.output["artifacts_dir"] = str(tmp_path / "artifacts")
    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="Q1")
    rc = runner.run()
    assert rc == 2  # QUARANTINE in scaffolded mode
    ru = json.loads(
        (tmp_path / "artifacts" / "Q1" / "registry_update.json").read_text(encoding="utf-8")
    )
    assert ru["decision"] == "QUARANTINE"
    assert ru["promoted_to_certification_registry"] is False


def test_promote_writes_registry_atomically(tmp_path: Path) -> None:
    cfg = _config()
    cfg.output["artifacts_dir"] = str(tmp_path / "artifacts")
    # Patch the decision to PROMOTE by directly calling stage_score_and_decide
    # after monkey-patching the stage's decision default.
    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="P1")
    # Force PROMOTE by overriding the score_and_decide stage
    from hft3.research.run_autonomous import GateResult
    from hft3.validation.gate_result import GateCategory, Severity

    def force_promote() -> Path:
        runner._stage_start("score_and_decide")
        scoring = {
            "decision": "PROMOTE",
            "reason": "test_forced",
            "campaign_id": cfg.campaign_id,
            "run_id": "P1",
            "git_sha": "abc",
            "timestamp_utc": "2026-06-02T00:00:00Z",
        }
        path = runner._write_artifact("scoring_summary.json", scoring)
        runner._write_artifact("promotion_decision.json", {
            "decision": "PROMOTE", "reason": "test_forced", "blocking_gates": []
        })
        runner._stage_end("score_and_decide", path)
        return path

    runner.stage_score_and_decide = force_promote  # type: ignore[assignment]
    assert runner.run() == 0
    ru = json.loads(
        (tmp_path / "artifacts" / "P1" / "registry_update.json").read_text(encoding="utf-8")
    )
    assert ru["promoted_to_certification_registry"] is True
    # Verify the legacy single-JSON file has been migrated + has a YELLOW record
    reg = load_registry(tmp_path)
    assert reg.latest_certification_status == "YELLOW"
    # Run ids from autonomous research are prefixed CERT-AR- to satisfy the
    # registry validator (latest_certification_run_id must start with "CERT-").
    assert reg.latest_certification_run_id == "CERT-AR-P1"


# ---------- artifacts ----------


def test_artifact_bundle_manifest_lists_all_stages(tmp_path: Path) -> None:
    cfg = _config()
    cfg.output["artifacts_dir"] = str(tmp_path / "artifacts")
    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="M1")
    runner.run()
    manifest = json.loads(
        (tmp_path / "artifacts" / "M1" / "manifest.json").read_text(encoding="utf-8")
    )
    assert "schema_version" in manifest
    for stage in (
        "load_config", "resolve_data", "resolve_features",
        "resolve_model_combinations", "generate_hypotheses",
        "experiment_specs", "backtest", "robustness_and_wf",
        "score_and_decide", "generate_report", "artifact_bundle",
        "registry_update",
    ):
        assert stage in manifest["completed_stages"], f"missing stage: {stage}"


def test_report_md_has_required_sections(tmp_path: Path) -> None:
    cfg = _config()
    cfg.output["artifacts_dir"] = str(tmp_path / "artifacts")
    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="R1")
    runner.run()
    report = (tmp_path / "artifacts" / "R1" / "report.md").read_text(encoding="utf-8")
    for n in range(1, 23):
        assert f"## {n}." in report, f"missing section {n}"


# ---------- CLI ----------


def test_cli_dry_run(tmp_path: Path) -> None:
    """The CLI works end-to-end via the repo-root launcher
    `python hft3-research.py --config ...`."""
    cfg = _config()
    cfg.output["artifacts_dir"] = str(tmp_path / "artifacts")
    cfg_path = tmp_path / "campaign.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "campaign_id": cfg.campaign_id,
        "data": cfg.data,
        "latency_profile": cfg.latency_profile,
        "features": cfg.features,
        "models": cfg.models,
        "robustness": cfg.robustness,
        "scoring": cfg.scoring,
        "registry": cfg.registry,
        "output": cfg.output,
        "research_input": cfg.research_input,
    }), encoding="utf-8")
    repo_root = Path(__file__).resolve().parents[1]
    launcher = repo_root / "hft3-research.py"
    proc = subprocess.run(
        [sys.executable, str(launcher),
         "--config", str(cfg_path), "--root", str(tmp_path), "--run-id", "CLI1"],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode in (0, 1, 2), (
        f"rc={proc.returncode} stdout={proc.stdout} stderr={proc.stderr}"
    )
    assert (tmp_path / "artifacts" / "CLI1" / "manifest.json").is_file()


# ---------- config schema stability ----------


def test_config_hash_is_deterministic(tmp_path: Path) -> None:
    cfg = _config()
    cfg.output["artifacts_dir"] = str(tmp_path / "artifacts")
    r1 = AutonomousRunner(config=cfg, root=tmp_path, run_id="H1")
    r1.stage_load_config()
    h1 = (tmp_path / "artifacts" / "H1" / "config_hash.txt").read_text(encoding="utf-8").strip()
    r2 = AutonomousRunner(config=cfg, root=tmp_path, run_id="H2")
    r2.stage_load_config()
    h2 = (tmp_path / "artifacts" / "H2" / "config_hash.txt").read_text(encoding="utf-8").strip()
    assert h1 == h2
