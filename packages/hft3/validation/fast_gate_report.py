"""T0 fast gate JSON report writer."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hft3.validation.certification_registry import git_sha, repo_root

DEFAULT_REPORT_REL = Path("runtime/validation/fast_gate_report.json")


def report_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / DEFAULT_REPORT_REL


def write_fast_gate_report(
    *,
    passed: bool,
    duration_sec: float,
    test_count: int,
    failed_count: int = 0,
    pytest_output_tail: str = "",
    root: Path | None = None,
) -> Path:
    root = root or repo_root()
    payload: dict[str, Any] = {
        "tier": "T0",
        "passed": passed,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha(root),
        "duration_sec": round(duration_sec, 3),
        "test_count": test_count,
        "failed_count": failed_count,
        "command": "python -m pytest tests/backtester_validation/fast -q",
        "pytest_output_tail": pytest_output_tail[-4000:] if pytest_output_tail else "",
    }
    path = report_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def load_fast_gate_report(root: Path | None = None) -> dict[str, Any] | None:
    path = report_path(root)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
