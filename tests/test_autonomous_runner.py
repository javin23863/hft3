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
from hft3.validation.gate_result import GateCategory, GateResult, Severity, write_robustness_gates_json


CONFIG_PATH = Path("configs/research/autonomous_hft3.yaml")


def _config() -> CampaignConfig:
    return CampaignConfig.from_yaml(CONFIG_PATH)


def _runner_config() -> CampaignConfig:
    cfg = _config()
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


def _write_passing_robustness_gates(
    path: Path,
    run_id: str,
    git_sha: str,
    *,
    include_data_gate: bool = True,
) -> None:
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
    if not include_data_gate:
        gates = [g for g in gates if g.gate_name != "data_resolution_eligibility"]
    write_robustness_gates_json(path, gates, tier="T3", run_id=run_id, git_sha=git_sha)


# ---------- config loading ----------


def test_config_loads_yaml() -> None:
    cfg = _config()
    assert cfg.campaign_id == "autonomous-hft3-catalog-driven"
    assert cfg.data.get("dataset_id") == "databento_es_mbo_v1"
    assert "MES.v.0" in cfg.data.get("symbol_universe", [])
    assert cfg.data.get("event_catalog_path") == "packages/data_system/config/events.csv"
    assert cfg.models.get("catalog_path") == "apps/workbench/config/model_catalog.yaml"
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
    runner = AutonomousRunner(config=_runner_config(), root=tmp_path)
    rc = runner.run()
    # Default scaffolded decision is QUARANTINE → rc 2
    assert rc == 2


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
    cfg = _runner_config()
    cfg.output["artifacts_dir"] = str(tmp_path / "artifacts")
    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="R1")
    assert runner.run() == 2
    first_manifest = json.loads(
        (tmp_path / "artifacts" / "R1" / "manifest.json").read_text(encoding="utf-8")
    )
    # Re-run with the same id
    runner2 = AutonomousRunner(config=cfg, root=tmp_path, run_id="R1")
    assert runner2.run() == 2
    second_manifest = json.loads(
        (tmp_path / "artifacts" / "R1" / "manifest.json").read_text(encoding="utf-8")
    )
    # Same set of completed stages and same artifact filenames
    assert set(first_manifest["completed_stages"]) == set(second_manifest["completed_stages"])
    assert set(first_manifest["artifacts"].keys()) == set(second_manifest["artifacts"].keys())


# ---------- registry write ----------


def test_quarantine_path_does_not_write_registry(tmp_path: Path) -> None:
    cfg = _runner_config()
    cfg.output["artifacts_dir"] = str(tmp_path / "artifacts")
    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="Q1")
    rc = runner.run()
    assert rc == 2  # QUARANTINE in scaffolded mode
    ru = json.loads(
        (tmp_path / "artifacts" / "Q1" / "registry_update.json").read_text(encoding="utf-8")
    )
    assert ru["decision"] == "QUARANTINE"
    assert ru["promoted_to_certification_registry"] is False
    decision = json.loads(
        (tmp_path / "artifacts" / "Q1" / "promotion_decision.json").read_text(encoding="utf-8")
    )
    gate_names = {gate["gate_name"] for gate in decision["blocking_gates"]}
    assert "monte_carlo_sharpe_p05" in gate_names
    assert "double_wf_correlation" in gate_names


def test_promote_writes_registry_atomically(tmp_path: Path) -> None:
    cfg = _runner_config()
    cfg.output["artifacts_dir"] = str(tmp_path / "artifacts")

    def force_promote(runner: AutonomousRunner, run_id: str, *, gates: str) -> Path:
        runner._stage_start("score_and_decide")
        if gates == "passing":
            _write_passing_robustness_gates(
                runner.run_dir / "robustness_gates.json", run_id, runner.state.git_sha
            )
        elif gates == "passing_without_data":
            _write_passing_robustness_gates(
                runner.run_dir / "robustness_gates.json",
                run_id,
                runner.state.git_sha,
                include_data_gate=False,
            )
        elif gates == "forged":
            _write_passing_robustness_gates(
                runner.run_dir / "robustness_gates.json", run_id, runner.state.git_sha
            )
            robustness_path = runner.run_dir / "robustness_gates.json"
            payload = json.loads(robustness_path.read_text(encoding="utf-8"))
            for gate in payload["gates"]:
                if gate["gate_name"] == "monte_carlo_sharpe_p05":
                    gate["observed_value"] = 0.1
                    break
            robustness_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        elif gates == "forged_data":
            _write_passing_robustness_gates(
                runner.run_dir / "robustness_gates.json", run_id, runner.state.git_sha
            )
            robustness_path = runner.run_dir / "robustness_gates.json"
            payload = json.loads(robustness_path.read_text(encoding="utf-8"))
            for gate in payload["gates"]:
                if gate["gate_name"] == "data_resolution_eligibility":
                    gate["observed_value"] = "ineligible"
                    gate["pass_fail"] = True
                    break
            robustness_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        elif gates == "missing_threshold":
            _write_passing_robustness_gates(
                runner.run_dir / "robustness_gates.json", run_id, runner.state.git_sha
            )
            robustness_path = runner.run_dir / "robustness_gates.json"
            payload = json.loads(robustness_path.read_text(encoding="utf-8"))
            for gate in payload["gates"]:
                if gate["gate_name"] == "monte_carlo_sharpe_p05":
                    gate.pop("threshold")
                    break
            robustness_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        elif gates == "wrong_category":
            _write_passing_robustness_gates(
                runner.run_dir / "robustness_gates.json", run_id, runner.state.git_sha
            )
            robustness_path = runner.run_dir / "robustness_gates.json"
            payload = json.loads(robustness_path.read_text(encoding="utf-8"))
            for gate in payload["gates"]:
                if gate["gate_name"] == "monte_carlo_sharpe_p05":
                    gate["gate_category"] = "data_integrity"
                    break
            robustness_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        elif gates == "duplicate_gate":
            _write_passing_robustness_gates(
                runner.run_dir / "robustness_gates.json", run_id, runner.state.git_sha
            )
            robustness_path = runner.run_dir / "robustness_gates.json"
            payload = json.loads(robustness_path.read_text(encoding="utf-8"))
            payload["gates"].append(dict(payload["gates"][-1]))
            robustness_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        scoring = {
            "decision": "PROMOTE",
            "reason": "test_forced",
            "campaign_id": cfg.campaign_id,
            "run_id": run_id,
            "git_sha": runner.state.git_sha,
            "timestamp_utc": "2026-06-02T00:00:00Z",
        }
        path = runner._write_artifact("scoring_summary.json", scoring)
        runner._write_artifact("promotion_decision.json", {
            "decision": "PROMOTE", "reason": "test_forced", "blocking_gates": []
        })
        runner._stage_end("score_and_decide", path)
        return path

    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="P1")
    runner.stage_score_and_decide = lambda: force_promote(runner, "P1", gates="missing")  # type: ignore[assignment]
    assert runner.run() == 3
    assert not (tmp_path / "artifacts" / "P1" / "registry_update.json").exists()

    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="P2")
    runner.stage_score_and_decide = lambda: force_promote(runner, "P2", gates="forged")  # type: ignore[assignment]
    assert runner.run() == 3
    assert not (tmp_path / "artifacts" / "P2" / "registry_update.json").exists()

    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="P4")
    runner.stage_score_and_decide = lambda: force_promote(runner, "P4", gates="passing_without_data")  # type: ignore[assignment]
    assert runner.run() == 3
    assert not (tmp_path / "artifacts" / "P4" / "registry_update.json").exists()

    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="P5")
    runner.stage_score_and_decide = lambda: force_promote(runner, "P5", gates="forged_data")  # type: ignore[assignment]
    assert runner.run() == 3
    assert not (tmp_path / "artifacts" / "P5" / "registry_update.json").exists()

    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="P6")
    runner.stage_score_and_decide = lambda: force_promote(runner, "P6", gates="missing_threshold")  # type: ignore[assignment]
    assert runner.run() == 3
    assert not (tmp_path / "artifacts" / "P6" / "registry_update.json").exists()

    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="P7")
    runner.stage_score_and_decide = lambda: force_promote(runner, "P7", gates="wrong_category")  # type: ignore[assignment]
    assert runner.run() == 3
    assert not (tmp_path / "artifacts" / "P7" / "registry_update.json").exists()

    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="P8")
    runner.stage_score_and_decide = lambda: force_promote(runner, "P8", gates="duplicate_gate")  # type: ignore[assignment]
    assert runner.run() == 3
    assert not (tmp_path / "artifacts" / "P8" / "registry_update.json").exists()

    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="P3")
    runner.stage_score_and_decide = lambda: force_promote(runner, "P3", gates="passing")  # type: ignore[assignment]
    assert runner.run() == 0
    ru = json.loads(
        (tmp_path / "artifacts" / "P3" / "registry_update.json").read_text(encoding="utf-8")
    )
    assert ru["promoted_to_certification_registry"] is True
    # Verify the legacy single-JSON file has been migrated + has a YELLOW record
    reg = load_registry(tmp_path)
    assert reg.latest_certification_status == "YELLOW"
    # Run ids from autonomous research are prefixed CERT-AR- to satisfy the
    # registry validator (latest_certification_run_id must start with "CERT-").
    assert reg.latest_certification_run_id == "CERT-AR-P3"


# ---------- artifacts ----------


def test_artifact_bundle_manifest_lists_all_stages(tmp_path: Path) -> None:
    cfg = _runner_config()
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
    cfg = _runner_config()
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
    cfg = _runner_config()
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
    assert proc.returncode == 2, (
        f"rc={proc.returncode} stdout={proc.stdout} stderr={proc.stderr}"
    )
    assert (tmp_path / "artifacts" / "CLI1" / "manifest.json").is_file()


# ---------- config schema stability ----------


def test_config_hash_is_deterministic(tmp_path: Path) -> None:
    cfg = _runner_config()
    cfg.output["artifacts_dir"] = str(tmp_path / "artifacts")
    r1 = AutonomousRunner(config=cfg, root=tmp_path, run_id="H1")
    r1.stage_load_config()
    h1 = (tmp_path / "artifacts" / "H1" / "config_hash.txt").read_text(encoding="utf-8").strip()
    r2 = AutonomousRunner(config=cfg, root=tmp_path, run_id="H2")
    r2.stage_load_config()
    h2 = (tmp_path / "artifacts" / "H2" / "config_hash.txt").read_text(encoding="utf-8").strip()
    assert h1 == h2
