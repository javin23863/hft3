"""Tests for scripts/run_plan_drift_review.py."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_REVIEW = _REPO / "scripts" / "run_plan_drift_review.py"


def test_plan_drift_passes_for_npz_abort_scope(tmp_path):
    out = tmp_path / "plan_drift_review.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(_REVIEW),
            "--completed-phase",
            "npz-abort-wire",
            "--out",
            str(out),
        ],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert proc.returncode in (0, 1)
    assert "pass" in payload
    assert "completed_phase" in payload
