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
