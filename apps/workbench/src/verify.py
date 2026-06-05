"""Workbench preflight check: imports, UI tests, latency summary, and required files."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

_REPO = Path(__file__).resolve().parents[3]
VERIFY_RESPONSE_CONTRACT = {
    "required": ["tests", "files", "files_ok", "all_ok"],
    "success_field": "all_ok",
    "success_values": [True],
    "failure_exit_code": 1,
}


def _run_pytest(repo: Path, test_paths: list[str]) -> Dict[str, Any]:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", *test_paths, "-q", "--tb=line"],
            cwd=str(repo), capture_output=True, text=True, timeout=120,
        )
        return {
            "passed": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout_tail": result.stdout.strip().split("\n")[-3:] if result.stdout else [],
        }
    except Exception as exc:
        return {"passed": False, "error": str(exc)}


def _check_file(path: Path, label: str) -> Dict[str, Any]:
    return {"label": label, "path": str(path), "exists": path.is_file()}


def verify(repo: Optional[Path] = None) -> Dict[str, Any]:
    repo = repo or _REPO
    events_catalog = repo / "packages" / "data_system" / "config" / "events.csv"
    if not events_catalog.is_file():
        events_catalog = repo / "data_system" / "config" / "events.csv"

    tests = _run_pytest(repo, [
        "tests/test_workbench/test_ui_imports.py",
        "tests/test_workbench/test_event_catalog.py",
    ])

    checks = [
        _check_file(repo / "runtime" / "latency_reports" / "latency_summary.json", "CHI404 latency summary"),
        _check_file(repo / "apps" / "workbench" / "ui" / "app.py", "Streamlit app"),
        _check_file(repo / "apps" / "workbench" / "requirements.txt", "Workbench requirements"),
        _check_file(repo / "packages" / "economic_event_universe" / "config" / "event_universe.yaml", "Economic event universe"),
        _check_file(events_catalog, "Events catalog"),
    ]

    files_ok = all(c["exists"] for c in checks)
    all_ok = tests["passed"] and files_ok

    return {
        "tests": tests,
        "files": checks,
        "files_ok": files_ok,
        "all_ok": all_ok,
    }
