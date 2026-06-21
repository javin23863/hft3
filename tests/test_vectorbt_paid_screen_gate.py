"""Tests for VectorBT paid screen gate and unit generation."""
from __future__ import annotations

import json
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
    assert "vbt_sample_run_declaration.json" in script
    assert "VBT_SAMPLE_MODE=1 requires VBT_MAX_UNITS" in script
    assert "expected_work_units" in script
    assert "DECL_EXPECTED" in script
    assert "ERROR: Full-run declaration missing" in script
    assert "ERROR: Declaration expected_work_units=" in script
    assert "--research-split" in script
    assert "--max-units" in script
    assert "--batch-timeout-seconds" in script
    assert 'PYTHON="$PYTHON" bash scripts/install_vbt_hbt_handoff_verify_deps.sh' in script


def test_paid_screen_shell_entrypoints_use_single_python_runtime() -> None:
    install = (REPO / "scripts" / "install_vbt_hbt_handoff_verify_deps.sh").read_text(encoding="utf-8")
    verify = (REPO / "scripts" / "run_vbt_hbt_handoff_verify.sh").read_text(encoding="utf-8")
    smoke = (REPO / "scripts" / "run_vbt_paid_screen_smoke.sh").read_text(encoding="utf-8")
    assert '"vectorbt[rust]==1.0.0"' in install
    assert 'PYTHON="$PYTHON" bash scripts/install_hftbacktest_realism_deps.sh' in install
    assert "pip3 install 'vectorbt[rust]==1.0.0'" not in (
        REPO / "scripts" / "run_vbt_paid_screen_vast_full.sh"
    ).read_text(encoding="utf-8")
    assert 'PYTHON="${PYTHON:-python3}"' in verify
    assert '"$PYTHON" -B -m pytest' in verify
    assert 'PYTHON="${PYTHON:-python3}"' in smoke
    assert '"$PYTHON" scripts/generate_vbt_paid_units_jsonl.py' in smoke
    assert '"$PYTHON" scripts/run_paid_screen.py' in smoke


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
