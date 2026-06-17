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
        json.dumps({"unit_id": "u1", "event_id": "CPI_2024_09_11_TIGHT", "thesis": "t"}) + "\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "run_vectorbt_paid_screen.py"),
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
        json.dumps({"unit_id": "u1", "event_id": "CPI_2024_09_11_TIGHT", "thesis": "t"}) + "\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "run_vectorbt_paid_screen.py"),
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
