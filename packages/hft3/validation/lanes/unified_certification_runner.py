"""Unified certification runner.

Discovers all registered lanes via LaneRegistry, runs each lane's
validation, and aggregates the results into a lane-aware scorecard.

The legacy CME-specific certification_runner.py is preserved for
backward compatibility; this module is the new lane-agnostic entry
point for the certification tier.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .lane import Lane
from .lane_registry import LaneRegistry
from .registration import register_all_lanes
from .scorecard import LaneScorecard, build_lane_scorecard, legacy_cme_scorecard_fields

CERTIFICATION_REPORT_REL = Path("runtime/validation/lane_certification_report.json")


@dataclass
class LaneRunResult:
    """Result of running one lane's validation."""

    lane: str
    passed: bool
    returncode: int
    output_tail: str = ""
    test_paths: list[str] = field(default_factory=list)
    failure_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane": self.lane,
            "passed": self.passed,
            "returncode": self.returncode,
            "output_tail": self.output_tail,
            "test_paths": list(self.test_paths),
            "failure_notes": list(self.failure_notes),
        }


def _run_pytest_for_lane(
    test_paths: list[str], root: Path, *, pytest_timeout: float | None = 60.0
) -> LaneRunResult:
    """Run pytest for a single lane's test paths. Returns the first non-empty path's result."""
    if not test_paths:
        return LaneRunResult(
            lane="unknown",
            passed=False,
            returncode=2,
            output_tail="(no test paths configured)",
            failure_notes=["no test paths configured"],
        )
    combined_output = ""
    overall_pass = True
    last_returncode = 0
    ran_any = False
    for tp in test_paths:
        path = root / tp
        if not path.exists():
            combined_output += f"[skip] {tp} (not present)\n"
            overall_pass = False
            last_returncode = 2
            continue
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", tp, "-q", "--tb=no", "-x"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=pytest_timeout,
            )
        except subprocess.TimeoutExpired:
            combined_output += f"[timeout] {tp}\n"
            overall_pass = False
            last_returncode = 124
            continue
        except FileNotFoundError:
            combined_output += f"[skip] {tp} (pytest not available)\n"
            overall_pass = False
            last_returncode = 127
            continue
        ran_any = True
        last_returncode = proc.returncode
        if proc.returncode != 0:
            overall_pass = False
        combined_output += f"=== {tp} (rc={proc.returncode}) ===\n"
        combined_output += (proc.stdout or "")[-500:]
        combined_output += (proc.stderr or "")[-500:]
    if not ran_any:
        overall_pass = False
        if last_returncode == 0:
            last_returncode = 2
    return LaneRunResult(
        lane="unknown",
        passed=overall_pass,
        returncode=last_returncode,
        output_tail=combined_output[-2000:],
        test_paths=list(test_paths),
        failure_notes=[] if overall_pass else [f"pytest failed for {test_paths}"],
    )


def run_unified_certification(
    root: Path | None = None,
    *,
    lanes: list[Lane] | None = None,
    auto_register: bool = True,
    skip_pytest: bool = False,
    pytest_timeout: float | None = 60.0,
) -> LaneScorecard:
    """Run certification across all registered lanes and return a LaneScorecard.

    - root: repo root (defaults to LaneRegistry's known root or cwd)
    - lanes: if given, restrict to these lanes; otherwise run all registered
    - skip_pytest: if True, skip pytest execution and only build the scorecard
    - pytest_timeout: per-test-path timeout in seconds (None = no timeout)
    """
    if auto_register:
        register_all_lanes()
    if root is None:
        root = Path.cwd()
    reg = LaneRegistry.instance()
    git_sha = _git_sha(root)
    timestamp_utc = datetime.now(timezone.utc).isoformat()
    card = build_lane_scorecard(git_sha=git_sha, timestamp_utc=timestamp_utc)
    card.extra = card.lane_coverage  # type: ignore[attr-defined]
    run_results: dict[str, LaneRunResult] = {}
    target_lanes = lanes if lanes is not None else reg.all_lanes()
    for lane in target_lanes:
        lane_reg = reg.get(lane)
        if lane_reg is None:
            continue
        if skip_pytest:
            run_results[lane.value] = LaneRunResult(
                lane=lane.value,
                passed=False,
                returncode=2,
                output_tail="(pytest skipped)",
                test_paths=list(lane_reg.test_paths),
                failure_notes=["pytest skipped; non-promotable certification result"],
            )
            continue
        result = _run_pytest_for_lane(
            list(lane_reg.test_paths), root, pytest_timeout=pytest_timeout
        )
        result.lane = lane.value
        run_results[lane.value] = result
    if not hasattr(card, "extra") or card.extra is None:
        card.extra = {}  # type: ignore[attr-defined]
    for lane_value, result in run_results.items():
        if lane_value not in card.lane_coverage:
            card.lane_coverage[lane_value] = {}
        card.lane_coverage[lane_value]["run_result"] = result.to_dict()
    card.extra = card.lane_coverage  # type: ignore[attr-defined]
    return card


def _git_sha(root: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ""


def write_unified_certification_report(
    card: LaneScorecard,
    root: Path | None = None,
) -> Path:
    """Write the unified certification report to runtime/validation/."""
    if root is None:
        root = Path.cwd()
    out = root / CERTIFICATION_REPORT_REL
    out.parent.mkdir(parents=True, exist_ok=True)
    import json

    payload = card.to_dict()
    payload["legacy_cme_fields"] = legacy_cme_scorecard_fields(card)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Unified lane-aware certification runner")
    parser.add_argument("--lanes", nargs="*", default=None, help="Restrict to these lanes")
    parser.add_argument("--skip-pytest", action="store_true", help="Build scorecard only")
    parser.add_argument("--output", default=None, help="Output report path")
    args = parser.parse_args(argv)
    lane_filter: list[Lane] | None = None
    if args.lanes:
        lane_filter = []
        for lv in args.lanes:
            try:
                lane_filter.append(Lane(lv))
            except ValueError:
                print(f"unknown lane: {lv}", file=sys.stderr)
                return 2
    card = run_unified_certification(lanes=lane_filter, skip_pytest=args.skip_pytest)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        import json

        out.write_text(json.dumps(card.to_dict(), indent=2) + "\n", encoding="utf-8")
    else:
        write_unified_certification_report(card)
    overall_pass = all(
        cov.get("run_result", {}).get("passed", True)
        for cov in card.lane_coverage.values()
    )
    return 0 if overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
