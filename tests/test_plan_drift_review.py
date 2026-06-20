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
    assert proc.returncode == (0 if payload["pass"] else 1)
    assert "pass" in payload
    assert "completed_phase" in payload


def test_main_preserves_run_review_errors(monkeypatch, tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_plan_drift_review", _REVIEW)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)

    def fake_review(**kwargs):
        return {
            "evaluated_at_utc": "x",
            "plan_path": "x",
            "completed_phase": kwargs.get("completed_phase", ""),
            "base_ref": "HEAD",
            "changed_files": [],
            "allowed_prefixes": [],
            "out_of_scope": [],
            "deprecated_violations": [],
            "errors": ["synthetic run_review error"],
            "pass": False,
        }

    monkeypatch.setattr(mod, "run_review", fake_review)
    monkeypatch.setattr(mod, "_git_changed_files", lambda: [])
    out = tmp_path / "plan_drift_review.json"
    rc = mod.main(["--completed-phase", "npz-abort-wire", "--out", str(out)])
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert rc == 1
    assert payload["pass"] is False
    assert "synthetic run_review error" in payload["errors"]
