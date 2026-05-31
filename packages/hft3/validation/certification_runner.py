"""T2 full backtester certification orchestrator."""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hft3.validation.certification_registry import (
    CertificationRecord,
    backtester_version,
    git_sha,
    new_certification_run_id,
    repo_root,
    save_registry,
)
from hft3.validation.fast_gate_report import write_fast_gate_report

try:
    from backtest_pipeline.src.runner import LATENCY_BANDS_MS, QUEUE_MODELS
except ImportError:
    LATENCY_BANDS_MS = [0.5, 1.0, 5.0]
    QUEUE_MODELS = ["LogProbQueueModel2", "RiskAverseQueueModel2"]

SCORECARD_JSON_REL = Path("runtime/validation/backtester_certification_scorecard.json")
SCORECARD_MD_REL = Path("runtime/validation/backtester_certification_scorecard.md")


@dataclass
class CertificationRunResult:
    status: str
    run_id: str
    git_sha: str
    blocking_failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    t0_passed: bool = False
    full_passed: bool = False
    scorecard_json: str = ""
    scorecard_md: str = ""


def _run_pytest(target: str, root: Path) -> tuple[bool, str, int, int, float]:
    import time

    cmd = [sys.executable, "-m", "pytest", target, "-q", "--tb=short"]
    start = time.time()
    proc = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True)
    duration = time.time() - start
    output = (proc.stdout or "") + (proc.stderr or "")
    failed = output.count(" FAILED")
    passed_line = [ln for ln in output.splitlines() if " passed" in ln or " failed" in ln]
    test_count = 0
    if passed_line:
        parts = passed_line[-1].split()
        for i, p in enumerate(parts):
            if p == "passed" and i > 0:
                try:
                    test_count = int(parts[i - 1])
                except ValueError:
                    pass
    return proc.returncode == 0, output, test_count, failed, duration


def _write_scorecard_md(payload: dict[str, Any]) -> str:
    lines = [
        "# Backtester Certification Scorecard",
        "",
        f"- **Status:** {payload.get('status')}",
        f"- **Run ID:** {payload.get('run_id')}",
        f"- **Git SHA:** {payload.get('git_sha')}",
        f"- **Timestamp UTC:** {payload.get('timestamp_utc')}",
        "",
        "## T0 Fast Gate",
        f"- Passed: {payload.get('t0_passed')}",
        "",
        "## T2 Full Suite",
        f"- Passed: {payload.get('full_passed')}",
        "",
    ]
    if payload.get("blocking_failures"):
        lines.append("## Blocking Failures")
        for item in payload["blocking_failures"]:
            lines.append(f"- {item}")
        lines.append("")
    if payload.get("warnings"):
        lines.append("## Warnings")
        for item in payload["warnings"]:
            lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines) + "\n"


def run_full_certification(root: Path | None = None) -> CertificationRunResult:
    root = root or repo_root()
    run_id = new_certification_run_id()
    sha = git_sha(root)
    blocking: list[str] = []
    warnings: list[str] = []

    t0_ok, t0_out, t0_count, t0_failed, t0_duration = _run_pytest("tests/backtester_validation/fast", root)
    write_fast_gate_report(
        passed=t0_ok,
        duration_sec=t0_duration,
        test_count=t0_count,
        failed_count=t0_failed,
        pytest_output_tail=t0_out,
        root=root,
    )
    if not t0_ok:
        blocking.append("T0 fast gate failed")

    full_ok, full_out, full_count, full_failed, _full_duration = _run_pytest(
        "tests/backtester_validation/full", root
    )
    if not full_ok:
        blocking.append("T2 full certification suite failed")

    if blocking:
        if t0_ok and not full_ok:
            status = "YELLOW" if full_failed <= 2 else "RED"
        else:
            status = "RED"
    else:
        status = "GREEN"

    payload: dict[str, Any] = {
        "tier": "T2",
        "status": status,
        "run_id": run_id,
        "git_sha": sha,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "t0_passed": t0_ok,
        "full_passed": full_ok,
        "t0_test_count": t0_count,
        "full_test_count": full_count,
        "blocking_failures": blocking,
        "warnings": warnings,
        "backtester_version": backtester_version(root),
        "covered_modules": ["backtest_pipeline", "execution", "replay", "features_engine", "workbench"],
        "covered_symbols": ["ES", "MES"],
        "covered_event_types": ["macro", "synthetic"],
        "covered_latency_bands": [float(x) for x in LATENCY_BANDS_MS if float(x) >= 0.5],
        "covered_queue_models": list(QUEUE_MODELS),
        "covered_execution_modes": ["REPLAY"],
    }

    json_path = root / SCORECARD_JSON_REL
    md_path = root / SCORECARD_MD_REL
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    md_text = _write_scorecard_md(payload)
    md_path.write_text(md_text, encoding="utf-8")

    record = CertificationRecord(
        latest_certification_run_id=run_id,
        latest_certification_commit=sha,
        latest_certification_timestamp=payload["timestamp_utc"],
        latest_certification_status=status,
        backtester_version=payload["backtester_version"],
        covered_modules=payload["covered_modules"],
        covered_symbols=payload["covered_symbols"],
        covered_event_types=payload["covered_event_types"],
        covered_latency_bands=payload["covered_latency_bands"],
        covered_queue_models=payload["covered_queue_models"],
        covered_execution_modes=payload["covered_execution_modes"],
        scorecard_path=str(SCORECARD_JSON_REL).replace("\\", "/"),
        blocking_failures=blocking,
        warnings=warnings,
    )
    save_registry(record, root)

    return CertificationRunResult(
        status=status,
        run_id=run_id,
        git_sha=sha,
        blocking_failures=blocking,
        warnings=warnings,
        t0_passed=t0_ok,
        full_passed=full_ok,
        scorecard_json=str(json_path),
        scorecard_md=str(md_path),
    )


def main() -> int:
    result = run_full_certification()
    print(f"Certification status: {result.status} (run_id={result.run_id})")
    if result.blocking_failures:
        for item in result.blocking_failures:
            print(f"  BLOCK: {item}")
    return 0 if result.status == "GREEN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
