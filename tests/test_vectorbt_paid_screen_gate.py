"""Tests for VectorBT paid screen gate and unit generation."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"


def test_generate_smoke_units_jsonl_count(tmp_path: Path) -> None:
    out = tmp_path / "units.jsonl"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--out",
            str(out),
            "--smoke-count",
            "5",
            "--event-types",
            "CPI",
            "--symbols",
            "MES.v.0",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert 1 <= len(lines) <= 5
    row = json.loads(lines[0])
    assert "unit_id" in row and "event_id" in row and "thesis" in row


def test_ready_gate_fails_on_missing_pilot(tmp_path: Path) -> None:
    smoke_manifest = tmp_path / "manifest.json"
    smoke_manifest.write_text(
        json.dumps(
            {
                "expected_work_units": 1,
                "completed_work_units": 1,
                "skipped_work_units": 0,
                "failed_work_units": 0,
                "out_dir": str(tmp_path),
                "unit_results": [],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "gate.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "validate_paid_screen_ready_gate.py"),
            "--pilot-artifact",
            str(tmp_path / "missing.json"),
            "--smoke-manifest",
            str(smoke_manifest),
            "--out",
            str(out),
            "--skip-pytest",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["ready_for_full_run"] is False
    assert payload["errors"]


def test_paid_screen_dry_run_lists_units(tmp_path: Path) -> None:
    units = tmp_path / "units.jsonl"
    units.write_text(
        json.dumps(
            {
                "unit_id": "u1",
                "event_id": "CPI_2024_09_11_TIGHT",
                "thesis": "t",
                "model_id": "SPREAD_BLOWOUT_RECOMPRESSION",
                "symbol": "MES.v.0",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "run_paid_screen.py"),
            "--units-jsonl",
            str(units),
            "--out",
            str(tmp_path / "run"),
            "--dry-run",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_run_paid_screen_dispatches_to_v2_orchestrator() -> None:
    script = (SCRIPTS / "run_paid_screen.py").read_text(encoding="utf-8")
    assert 'run_vectorbt_paid_screen_v2.py"' in script
    assert '_ORCHESTRATOR = _REPO / "scripts" / "run_vectorbt_paid_screen.py"' not in script

    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "run_paid_screen.py"), "--help"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "run_vectorbt_paid_screen_v2.py" in proc.stdout
    assert "--max-batches-before-recycle" in proc.stdout
    assert "--lake-manifest-hash" in proc.stdout


def test_next_steps_defaults_to_phase_a(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "vbt_paid_screen_next_steps.py"),
            "--json",
            "--pilot-artifact",
            str(missing),
            "--smoke-manifest",
            str(missing),
            "--gate-file",
            str(missing),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["phase"] == "A"
    assert payload["commands"]


def test_aggregate_promoted_ids(tmp_path: Path) -> None:
    unit_dir = tmp_path / "units" / "u1"
    unit_dir.mkdir(parents=True)
    artifact = {
        "promoted_ids": ["cand_a", "cand_b"],
        "candidate_ids": ["cand_a", "cand_b", "cand_c"],
    }
    (unit_dir / "screening_artifact.json").write_text(json.dumps(artifact), encoding="utf-8")
    manifest = {
        "out_dir": str(tmp_path),
        "expected_work_units": 1,
        "completed_work_units": 1,
        "failed_work_units": 0,
        "skipped_work_units": 0,
        "unit_results": [
            {
                "unit_id": "u1",
                "status": "OK",
                "screening_artifact_relpath": "units/u1/screening_artifact.json",
            }
        ],
    }
    manifest_path = tmp_path / "paid_screen_run_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    out = tmp_path / "promoted.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "aggregate_vbt_promoted_ids.py"),
            "--manifest",
            str(manifest_path),
            "--out",
            str(out),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["promoted_id_count"] == 2
    assert set(payload["promoted_ids"]) == {"cand_a", "cand_b"}


def test_all_active_models_generates_multiple_hypotheses(tmp_path: Path) -> None:
    """Full-scope generator expands active registry (not single model or Stage A)."""
    from features_engine.src.hypotheses.registry import get_active_hypotheses

    n_active = len(get_active_hypotheses())
    out = tmp_path / "units.jsonl"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--all-active-models",
            "--event-types",
            "CPI",
            "--symbols",
            "MES.v.0",
            "--out",
            str(out),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) >= n_active
    model_ids = {json.loads(ln)["model_id"] for ln in lines}
    assert len(model_ids) >= 2
    row = json.loads(lines[0])
    assert row.get("hyp_id") is not None
    assert "unit_id" in row and "event_id" in row and "thesis" in row


def test_model_ids_flag_expands_explicit_models(tmp_path: Path) -> None:
    out = tmp_path / "units.jsonl"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--model-ids",
            "HYP_5,HYP_1",
            "--event-types",
            "CPI",
            "--symbols",
            "MES.v.0",
            "--out",
            str(out),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    rows = [json.loads(ln) for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(rows) >= 2
    model_ids = {r["model_id"] for r in rows}
    assert "SPREAD_BLOWOUT_RECOMPRESSION" in model_ids
    assert "SECOND_WAVE_CONTINUATION" in model_ids


def test_next_steps_after_gate_points_to_vast_full(tmp_path: Path) -> None:
    pilot = tmp_path / "pilot.json"
    pilot.write_text(json.dumps({"screening_backend": "vectorbt"}), encoding="utf-8")
    smoke_manifest = tmp_path / "smoke_manifest.json"
    smoke_manifest.write_text(
        json.dumps(
            {
                "expected_work_units": 1,
                "completed_work_units": 1,
                "skipped_work_units": 0,
                "failed_work_units": 0,
                "out_dir": str(tmp_path),
                "unit_results": [],
            }
        ),
        encoding="utf-8",
    )
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps({"ready_for_full_run": True}), encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "vbt_paid_screen_next_steps.py"),
            "--json",
            "--pilot-artifact",
            str(pilot),
            "--smoke-manifest",
            str(smoke_manifest),
            "--gate-file",
            str(gate),
            "--full-manifest",
            str(tmp_path / "missing_full.json"),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["phase"] == "D1-D4"
    joined = "\n".join(payload["commands"])
    assert "run_vbt_paid_screen_vast_full.sh" in joined
    assert "stage_a_survivors" not in joined


def test_vast_full_script_has_no_stage_a_prerequisite() -> None:
    script = (REPO / "scripts" / "run_vbt_paid_screen_vast_full.sh").read_text(encoding="utf-8")
    assert "stage_a_survivors" not in script
    assert "--all-active-models" in script


def test_stage_a_survivors_expansion_not_capped_at_fifty(tmp_path: Path) -> None:
    """Full scope uses all TIGHT events per cell — not [:50] and not CPI+NFP-only smoke."""
    survivors = tmp_path / "stage_a_survivors.json"
    survivors.write_text(
        json.dumps(
            {
                "survivors": [{"hyp_id": 5, "event_type": "CPI"}],
                "pass_through": [],
                "tested_cells": [{"hyp_id": 5, "event_type": "CPI"}],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "units.jsonl"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--from-stage-a-survivors",
            str(survivors),
            "--symbols",
            "MES.v.0",
            "--out",
            str(out),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()]
    # CPI TIGHT MES catalog has >50 events; old [:50] cap would yield exactly 50
    assert len(lines) > 50
    row = json.loads(lines[0])
    assert row["model_id"] == "SPREAD_BLOWOUT_RECOMPRESSION"
    assert row.get("hyp_id") == 5
    assert "SPREAD_BLOWOUT_RECOMPRESSION" in row["thesis"]

    from research_pipeline.hypothesis_parser import parse_hypothesis

    parsed = parse_hypothesis(row["thesis"], use_llm=False)
    assert parsed.primary_model_id == row["model_id"]


def test_generated_thesis_round_trips_hyp_ids_1_to_50(tmp_path: Path) -> None:
    """Every Stage-A-style thesis must parse back to the same canonical slug."""
    from features_engine.src.model_registry import get_slug_for_hyp_id
    from research_pipeline.hypothesis_parser import parse_hypothesis

    for hyp_id in range(1, 51):
        slug = get_slug_for_hyp_id(hyp_id)
        thesis = (
            f"Display event-window strategy ({slug}) on CPI release for MES.v.0 "
            f"event CPI_2024_09_11_TIGHT"
        )
        parsed = parse_hypothesis(thesis, use_llm=False)
        assert parsed.primary_model_id == slug, f"hyp_id={hyp_id} got {parsed.primary_model_id}"


def test_paid_screen_refuses_high_workers_without_gate(tmp_path: Path) -> None:
    units = tmp_path / "units.jsonl"
    units.write_text(
        json.dumps(
            {
                "unit_id": "u1",
                "event_id": "CPI_2024_09_11_TIGHT",
                "thesis": "t",
                "model_id": "SPREAD_BLOWOUT_RECOMPRESSION",
                "symbol": "MES.v.0",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "run_paid_screen.py"),
            "--units-jsonl",
            str(units),
            "--out",
            str(tmp_path / "run"),
            "--workers",
            "32",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2


def test_single_model_id_hyp_5_resolves_canonical_slug(tmp_path: Path) -> None:
    events_csv = tmp_path / "events.csv"
    events_csv.write_text(
        "event_id,event_type,release_date,release_time,timezone,window_name,"
        "start_offset_seconds,end_offset_seconds,symbols,priority,source,source_url,"
        "effective_date,notes,row_status\n"
        "CPI_2020_01_15_TIGHT,CPI,2020-01-15,08:30:00,America/New_York,TIGHT,"
        "-60,10,MES.v.0,50,CPI,https://example.com/,2020-01-01,test,SOURCED\n",
        encoding="utf-8",
    )
    out = tmp_path / "units.jsonl"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--events-csv",
            str(events_csv),
            "--model-id",
            "HYP_5",
            "--symbols",
            "MES.v.0",
            "--event-types",
            "CPI",
            "--out",
            str(out),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    row = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert row["model_id"] == "SPREAD_BLOWOUT_RECOMPRESSION"
    assert row.get("hyp_id") == 5
    assert "SPREAD_BLOWOUT_RECOMPRESSION" in row["thesis"]


def test_multi_symbol_expansion_emits_all_matching_symbols(tmp_path: Path) -> None:
    events_csv = tmp_path / "events.csv"
    events_csv.write_text(
        "event_id,event_type,release_date,release_time,timezone,window_name,"
        "start_offset_seconds,end_offset_seconds,symbols,priority,source,source_url,"
        "effective_date,notes,row_status\n"
        "CPI_2020_01_15_TIGHT,CPI,2020-01-15,08:30:00,America/New_York,TIGHT,"
        "-60,10,\"MES.v.0,MNQ.v.0\",50,CPI,https://example.com/,2020-01-01,test,SOURCED\n",
        encoding="utf-8",
    )
    out = tmp_path / "units.jsonl"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--events-csv",
            str(events_csv),
            "--model-id",
            "HYP_5",
            "--symbols",
            "MES.v.0,MNQ.v.0",
            "--event-types",
            "CPI",
            "--out",
            str(out),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    rows = [json.loads(ln) for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()]
    symbols = {r["symbol"] for r in rows}
    assert symbols == {"MES.v.0", "MNQ.v.0"}
    assert len(rows) == 2


def test_all_active_default_excludes_holdout_events(tmp_path: Path) -> None:
    out = tmp_path / "units.jsonl"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--all-active-models",
            "--event-types",
            "CPI",
            "--symbols",
            "MES.v.0",
            "--out",
            str(out),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    rows = [json.loads(ln) for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert rows
    assert all(r.get("research_split") == "discovery_confirmation" for r in rows)
    years = {int(r["event_id"].split("_")[1]) for r in rows if r["event_id"].startswith("CPI_")}
    assert max(years) <= 2022
    assert not any(y >= 2023 for y in years)


def test_all_active_holdout_split_includes_holdout_when_explicit(tmp_path: Path) -> None:
    out = tmp_path / "units.jsonl"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "generate_vbt_paid_units_jsonl.py"),
            "--all-active-models",
            "--research-split",
            "holdout",
            "--event-types",
            "CPI",
            "--symbols",
            "MES.v.0",
            "--max-units",
            "5",
            "--out",
            str(out),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    rows = [json.loads(ln) for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert rows
    assert all(r.get("research_split") == "holdout" for r in rows)
    years = {int(r["event_id"].split("_")[1]) for r in rows if r["event_id"].startswith("CPI_")}
    assert years.issubset({2023, 2024})
    assert min(years) >= 2023


def test_vast_full_script_requires_declaration_before_workers() -> None:
    script = (REPO / "scripts" / "run_vbt_paid_screen_vast_full.sh").read_text(encoding="utf-8")
    assert "vbt_full_run_declaration.json" in script
    assert "expected_work_units" in script
    assert "DECL_EXPECTED" in script
    assert "DECL_GIT_HEAD" in script
    assert "git rev-parse HEAD" in script
    assert "Declaration git_head=" in script
    assert "DECL_EVENTS_HASH" in script
    assert "DECL_LAKE_HASH" in script
    assert "ERROR: Full-run declaration missing" in script
    assert "ERROR: Declaration expected_work_units=" in script
    assert "--research-split" in script


def test_vast_remote_verify_contract_is_no_launch() -> None:
    script_path = REPO / "scripts" / "vast_remote_verify.sh"
    script = script_path.read_text(encoding="utf-8")
    deploy = (REPO / "scripts" / "vast_deploy_and_verify.ps1").read_text(encoding="utf-8")
    assert "vast_remote_verify.sh" in deploy
    assert "DEPLOY_CONTRACT_PASS" in deploy
    assert "REMOTE_VERIFY_PASS" in script
    assert "DEPLOY_HEAD" in script
    assert "ready_for_full_run" in script
    assert "events_csv_hash" in script
    assert "lake_manifest_hash" in script
    assert "manifest.parquet" in script
    assert "run_paid_screen" not in script
    assert "run_vbt_paid_screen_vast_full.sh" not in script
    assert "tmux" not in script


def test_vast_deploy_contract_checks_declaration_head_and_hashes() -> None:
    deploy = (REPO / "scripts" / "vast_deploy_and_verify.ps1").read_text(encoding="utf-8")
    assert "vbt_full_run_declaration.json" in deploy
    assert "$declHead" in deploy
    assert "Local HEAD $repoHead != declaration git_head $declHead" in deploy
    assert "Origin/$GitBranch head $localHead != declaration git_head $declHead" in deploy
    assert "Declaration events_csv_hash $declEventsHash != gate $expectedEventsHash" in deploy
    assert "Declaration lake_manifest_hash $declLakeHash != gate $expectedLakeHash" in deploy


def test_vast_deploy_contract_copies_declaration_and_derives_branch() -> None:
    deploy = (REPO / "scripts" / "vast_deploy_and_verify.ps1").read_text(encoding="utf-8")
    assert 'else { "cursor/vast-vbt-workflow" }' not in deploy
    assert "git branch --show-current" in deploy
    assert "set HFT3_VAST_GIT_BRANCH" in deploy
    assert "$remoteDecl" in deploy
    assert "scp declaration failed" in deploy
    assert "SCP gate, declaration, events.csv, manifest.parquet" in deploy


def test_vast_deploy_contract_rejects_shell_unsafe_branch_and_paths() -> None:
    deploy = (REPO / "scripts" / "vast_deploy_and_verify.ps1").read_text(encoding="utf-8")
    assert "git check-ref-format --branch $Branch" in deploy
    assert "Invalid GitBranch" in deploy
    assert "Normalize-RemoteAbsolutePath \"RemoteRepo\" $RemoteRepo" in deploy
    assert "Normalize-RemoteAbsolutePath \"RemoteNpzRoot\" $RemoteNpzRoot" in deploy
    assert "Normalize-RemoteAbsolutePath \"RemoteManifestPath\" $RemoteManifestPath" in deploy
    assert "Normalize-RepoRelativePath \"EventsCsv\" $EventsCsv" in deploy
    assert "Get-RemotePosixParent" in deploy
    assert "Split-Path $remoteEvents" not in deploy
    assert "Split-Path $RemoteManifestPath" not in deploy
    assert "contains unsafe path characters" in deploy


def test_vast_deploy_contract_passes_remote_shell_values_as_quoted_bash_args() -> None:
    deploy = (REPO / "scripts" / "vast_deploy_and_verify.ps1").read_text(encoding="utf-8")
    assert "ConvertTo-BashSingleQuotedArg" in deploy
    assert "bash -s -- $quotedArgs" in deploy
    assert 'remote_repo="$1"' in deploy
    assert 'git -C "$remote_repo" fetch origin "$git_branch"' in deploy
    assert "Invoke-RemoteBash $syncCmd @($RemoteRepo, $GitBranch)" in deploy
    assert 'export DEPLOY_REPO="$1"' in deploy
    assert "Invoke-RemoteBash $remoteVerify @(" in deploy
    assert 'find "$1" -maxdepth 1 -type f -name' in deploy
    assert "Invoke-RemoteSh" not in deploy
    assert "export DEPLOY_REPO='$RemoteRepo'" not in deploy
    assert "find $RemoteNpzRoot -maxdepth" not in deploy


def test_vast_remote_verify_passes_temp_contract(tmp_path: Path) -> None:
    if shutil.which("bash") is None or shutil.which("git") is None:
        pytest.skip("requires bash and git")

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    (repo / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True, text=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    events = repo / "events.csv"
    manifest = repo / "manifest.parquet"
    npz_root = repo / "npz"
    reports = repo / "runtime" / "reports"
    npz_root.mkdir()
    reports.mkdir(parents=True)
    events.write_text("event_id\nE1\n", encoding="utf-8")
    manifest.write_bytes(b"manifest-bytes")
    (npz_root / "one.npz").write_bytes(b"npz-bytes")
    events_hash = hashlib.sha256(events.read_bytes()).hexdigest()[:32]
    lake_hash = hashlib.sha256(manifest.read_bytes()).hexdigest()[:32]
    (reports / "paid_screen_ready_gate.json").write_text(
        json.dumps(
            {
                "ready_for_full_run": True,
                "pilot_hashes": {
                    "events_csv_hash": events_hash,
                    "lake_manifest_hash": lake_hash,
                },
            }
        ),
        encoding="utf-8",
    )
    (reports / "vbt_full_run_declaration.json").write_text(
        json.dumps(
            {
                "git_head": head,
                "events_csv_hash": events_hash,
                "lake_manifest_hash": lake_hash,
            }
        ),
        encoding="utf-8",
    )
    shutil.copy2(SCRIPTS / "vast_remote_verify.sh", repo / "vast_remote_verify.sh")
    env = os.environ.copy()
    env.update(
        {
            "DEPLOY_REPO": ".",
            "DEPLOY_EVENTS": "events.csv",
            "DEPLOY_MANIFEST": "manifest.parquet",
            "DEPLOY_HEAD": head,
            "DEPLOY_NPZ_ROOT": "npz",
            "DEPLOY_PROBE_N": "1",
        }
    )
    proc = subprocess.run(
        ["bash", "vast_remote_verify.sh"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "REMOTE_VERIFY_PASS" in proc.stdout


def test_vast_remote_verify_rejects_manifest_json(tmp_path: Path) -> None:
    if shutil.which("bash") is None:
        pytest.skip("requires bash")
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy2(SCRIPTS / "vast_remote_verify.sh", repo / "vast_remote_verify.sh")
    env = os.environ.copy()
    env.update(
        {
            "DEPLOY_REPO": ".",
            "DEPLOY_EVENTS": "events.csv",
            "DEPLOY_MANIFEST": "manifest.json",
            "DEPLOY_HEAD": "deadbeef",
            "DEPLOY_NPZ_ROOT": "npz",
        }
    )
    proc = subprocess.run(
        ["bash", "vast_remote_verify.sh"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "manifest.parquet" in proc.stderr


def test_vast_ssh_script_uses_current_branch_not_hardcoded_main() -> None:
    script = (REPO / "scripts" / "vast_ssh_run_vbt_paid_screen.sh").read_text(encoding="utf-8")
    assert "VBT_GIT_BRANCH:-main" not in script
    assert "git branch --show-current" in script
    assert "detached HEAD" in script


def test_vast_ssh_script_supports_separate_host_and_port() -> None:
    script = (REPO / "scripts" / "vast_ssh_run_vbt_paid_screen.sh").read_text(encoding="utf-8")
    assert "VAST_SSH_HOST_ARG" in script
    assert "VAST_SSH_PORT" in script
    assert 'SSH_OPTS+=(-p "$VAST_SSH_PORT")' in script
    assert 'SCP_OPTS+=(-P "$VAST_SSH_PORT")' in script
    assert "do not embed -p in VAST_SSH_TARGET" in script
    assert '"$VAST_SSH_HOST_ARG"' in script
    assert "VAST_SSH_TARGET" in script


def test_next_steps_includes_declaration_before_full_run(tmp_path: Path) -> None:
    from scripts.vbt_paid_screen_next_steps import _phase

    pilot = tmp_path / "pilot.json"
    pilot.write_text(json.dumps({"screening_backend": "vectorbt"}), encoding="utf-8")
    smoke_manifest = tmp_path / "smoke_manifest.json"
    smoke_manifest.write_text(
        json.dumps(
            {
                "expected_work_units": 1,
                "completed_work_units": 1,
                "skipped_work_units": 0,
                "failed_work_units": 0,
                "out_dir": str(tmp_path),
                "unit_results": [],
            }
        ),
        encoding="utf-8",
    )
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps({"ready_for_full_run": True}), encoding="utf-8")
    missing_decl = tmp_path / "missing_decl.json"
    missing_full = tmp_path / "missing_full.json"

    phase, commands = _phase(
        {
            "pilot": pilot,
            "smoke": smoke_manifest,
            "gate": gate,
            "full": missing_full,
            "full_units": tmp_path / "units.jsonl",
            "decl": missing_decl,
        }
    )
    assert phase == "D1-D4"
    joined = "\n".join(commands)
    assert "Phase D0" in joined
    assert "vbt_full_run_declaration.json" in joined
    assert "run_vbt_paid_screen_vast_full.sh" in joined
    assert joined.index("vbt_full_run_declaration.json") < joined.index("run_vbt_paid_screen_vast_full.sh")
