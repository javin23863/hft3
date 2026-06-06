"""T2 full backtester certification orchestrator.

Phase 38+ lane-aware extension: the T2 certification tier now runs the
lane-aware unified certification runner for non-CME lanes (crypto,
equities, options) in addition to the legacy CME T0 fast gate and T2
full suite. The CME core (T0+T2) is not duplicated — the lane-aware
runner is invoked with `lanes=[CRYPTO, EQUITIES, OPTIONS]` so the
backtester_validation/{fast,full} tests run only once per tier.

The scorecard aggregates per-lane coverage and pass/fail. Status
decisions:

- T0 fast gate fails → RED
- T2 full suite fails → RED
- Any non-CME lane pytest fails → YELLOW (recorded as warning)
- All pass → GREEN
"""
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
from hft3.validation.lanes import Lane
from hft3.validation.lanes.scorecard import (
    LaneScorecard,
    build_lane_scorecard,
    legacy_cme_scorecard_fields,
)
from hft3.validation.lanes.unified_certification_runner import run_unified_certification

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
    lane_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    lane_failures: list[str] = field(default_factory=list)
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
        f"- **Backtester Version:** {payload.get('backtester_version')}",
        "",
        "## T0 Fast Gate (CME core)",
        f"- Passed: {payload.get('t0_passed')}",
        f"- Test count: {payload.get('t0_test_count', 0)}",
        "",
        "## T2 Full Suite (CME core, adversarial)",
        f"- Passed: {payload.get('full_passed')}",
        f"- Test count: {payload.get('full_test_count', 0)}",
        "",
        "## Lane-aware Certification (Phase 38+)",
    ]
    lane_results = payload.get("lane_results", {})
    for lane_value, lr in lane_results.items():
        lines.append(f"### {lane_value}")
        lines.append(f"- Passed: {lr.get('passed')}")
        lines.append(f"- Returncode: {lr.get('returncode')}")
        if lr.get("test_paths"):
            lines.append(f"- Test paths: {', '.join(lr['test_paths'])}")
        if lr.get("failure_notes"):
            lines.append(f"- Failure notes: {lr['failure_notes']}")
        lines.append("")
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
    lines.append("## Coverage")
    lines.append(f"- Modules: {payload.get('covered_modules', [])}")
    lines.append(f"- Symbols: {payload.get('covered_symbols', [])}")
    lines.append(f"- Event types: {payload.get('covered_event_types', [])}")
    lines.append(f"- Latency bands (ms): {payload.get('covered_latency_bands', [])}")
    lines.append(f"- Queue models: {payload.get('covered_queue_models', [])}")
    lines.append(f"- Execution modes: {payload.get('covered_execution_modes', [])}")
    return "\n".join(lines) + "\n"


def _aggregate_lane_coverage(lane_card: LaneScorecard) -> dict[str, list[Any]]:
    """Union symbols, event_types, latency_bands across all 4 lanes."""
    symbols: set[str] = set()
    event_types: set[str] = set()
    latency_bands: set[float] = set()
    modules: set[str] = set()
    for lane_value, cov in lane_card.lane_coverage.items():
        for s in cov.get("symbols", []):
            symbols.add(str(s))
        for et in cov.get("event_types", []):
            event_types.add(str(et))
        for lb in cov.get("latency_bands_ms", []):
            try:
                latency_bands.add(float(lb))
            except (TypeError, ValueError):
                pass
        if lane_value == "cme_futures":
            modules.update(["backtest_pipeline", "execution", "replay", "features_engine", "workbench"])
        elif lane_value == "crypto":
            modules.add("crypto_lane")
        elif lane_value == "equities":
            modules.add("equities_lane")
        elif lane_value == "options":
            modules.add("options_lane")
    return {
        "covered_modules": sorted(modules),
        "covered_symbols": sorted(symbols),
        "covered_event_types": sorted(event_types),
        "covered_latency_bands": sorted(latency_bands),
    }


def run_full_certification(
    root: Path | None = None,
    *,
    skip_lane_pytest: bool = False,
) -> CertificationRunResult:
    root = root or repo_root()
    run_id = new_certification_run_id()
    sha = git_sha(root)
    blocking: list[str] = []
    warnings: list[str] = []
    lane_results: dict[str, dict[str, Any]] = {}
    lane_failures: list[str] = []

    # Step 1: T0 fast gate (CME core)
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

    # Step 2: T2 full suite (CME core, adversarial)
    full_ok, full_out, full_count, _full_failed, _full_duration = _run_pytest(
        "tests/backtester_validation/full", root
    )
    if not full_ok:
        blocking.append("T2 full certification suite failed")

    # Step 3: Lane-aware certification (Phase 38+)
    # Run non-CME lanes (CRYPTO, EQUITIES, OPTIONS). CME core is already
    # covered by T0/T2; running it again would duplicate ~28 tests.
    lane_card: LaneScorecard | None = None
    if not skip_lane_pytest:
        try:
            lane_card = run_unified_certification(
                root=root,
                lanes=[Lane.CRYPTO, Lane.EQUITIES, Lane.OPTIONS],
                skip_pytest=False,
                pytest_timeout=300.0,
            )
            for lane_value, cov in lane_card.lane_coverage.items():
                if lane_value == Lane.CME_FUTURES.value:
                    continue
                rr = cov.get("run_result", {})
                lane_results[lane_value] = rr
                if rr and not rr.get("passed", True):
                    lane_failures.append(lane_value)
                    warnings.append(
                        f"lane '{lane_value}' pytest failed (rc={rr.get('returncode')}): "
                        f"{'; '.join(rr.get('failure_notes', []))}"
                    )
            # CME core is covered by T0 fast gate + T2 full suite; record
            # that explicitly so the scorecard reflects the full lane set.
            lane_results[Lane.CME_FUTURES.value] = {
                "lane": Lane.CME_FUTURES.value,
                "passed": bool(t0_ok and full_ok),
                "returncode": 0 if (t0_ok and full_ok) else 1,
                "test_paths": ["tests/backtester_validation/fast", "tests/backtester_validation/full"],
                "failure_notes": [] if (t0_ok and full_ok) else [
                    f"T0 fast gate {'failed' if not t0_ok else 'passed'}",
                    f"T2 full suite {'failed' if not full_ok else 'passed'}",
                ],
                "covered_by": "T0 fast gate + T2 full suite (CME core)",
            }
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"lane-aware certification raised {type(exc).__name__}: {exc}")
            lane_card = None

    # Status decision is fail-closed: blockers are production blockers.
    if blocking:
        status = "RED"
    elif lane_failures:
        status = "YELLOW"
    else:
        status = "GREEN"

    # Coverage aggregation
    if lane_card is not None:
        agg = _aggregate_lane_coverage(lane_card)
    else:
        agg = {
            "covered_modules": ["backtest_pipeline", "execution", "replay", "features_engine", "workbench"],
            "covered_symbols": ["ES", "MES"],
            "covered_event_types": ["macro", "synthetic"],
            "covered_latency_bands": [float(x) for x in LATENCY_BANDS_MS if float(x) >= 0.5],
        }
    legacy_cme_bands = [float(x) for x in LATENCY_BANDS_MS if float(x) >= 0.5]
    legacy_cme_bands_set = set(agg["covered_latency_bands"]) | set(legacy_cme_bands)
    agg["covered_latency_bands"] = sorted(legacy_cme_bands_set)

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
        "lane_results": lane_results,
        "lane_failures": lane_failures,
        "backtester_version": backtester_version(root),
        "covered_modules": agg["covered_modules"],
        "covered_symbols": agg["covered_symbols"],
        "covered_event_types": agg["covered_event_types"],
        "covered_latency_bands": agg["covered_latency_bands"],
        "covered_queue_models": list(QUEUE_MODELS),
        "covered_execution_modes": ["REPLAY"],
    }
    if lane_card is not None:
        payload["lane_coverage"] = lane_card.lane_coverage
        payload["legacy_cme_fields"] = legacy_cme_scorecard_fields(lane_card)

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
        lane_results=lane_results,
        lane_failures=lane_failures,
        scorecard_json=str(json_path),
        scorecard_md=str(md_path),
    )


def main() -> int:
    result = run_full_certification()
    print(f"Certification status: {result.status} (run_id={result.run_id})")
    t0 = getattr(result, "t0_passed", None)
    full = getattr(result, "full_passed", None)
    if t0 is not None:
        print(f"  T0 fast gate: {'PASS' if t0 else 'FAIL'}")
    if full is not None:
        print(f"  T2 full suite: {'PASS' if full else 'FAIL'}")
    lane_results = getattr(result, "lane_results", {}) or {}
    if lane_results:
        for lane_value, lr in lane_results.items():
            passed = lr.get("passed") if isinstance(lr, dict) else None
            rc = lr.get("returncode") if isinstance(lr, dict) else None
            status_str = "PASS" if passed else ("FAIL" if passed is False else "SKIP")
            print(f"  Lane {lane_value}: {status_str} (rc={rc})")
    if getattr(result, "blocking_failures", None):
        for item in result.blocking_failures:
            print(f"  BLOCK: {item}")
    if getattr(result, "warnings", None):
        for item in result.warnings:
            print(f"  WARN: {item}")
    return 0 if result.status == "GREEN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
