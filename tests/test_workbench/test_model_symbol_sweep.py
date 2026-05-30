"""Tests for scripts/run_model_symbol_sweep.py."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "run_model_symbol_sweep.py"


def test_sweep_requires_explicit_mode():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=str(REPO),
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "Specify --backfill and/or --sweep" in proc.stderr


def test_sweep_dry_run_exit_zero():
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--sweep",
            "--dry-run",
            "--symbols",
            "MES.v.0",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
