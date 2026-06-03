"""T4 champion promotion gate logic.

Phase 8 hardening: each check in the gate is now expressed as a
`GateResult` (see `hft3.validation.gate_result`). The legacy
`PromotionGateResult` shape is preserved for backward compatibility:
- `passed` = True iff no blocking gate failed.
- `failures` = list of free-text descriptions of blocking failures
  (preserves the substrings the existing tests look for: "GREEN",
  "MISSING", "missing", etc.).
- `gates` = the new list[GateResult] (additive field).

New functions:
- `evaluate_promotion_gates(...)` returns the raw `list[GateResult]`.
- `write_robustness_gates_for_promotion(...)` writes
  `runtime/validation/robustness_gates.json` per Phase 12.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hft3.validation.certification_registry import git_sha, load_registry, repo_root
from hft3.validation.certification_staleness import assess_staleness
from hft3.validation.fast_gate_report import load_fast_gate_report
from hft3.validation.gate_result import (
    COMPARISON_OPERATORS,  # noqa: F401  (re-export)
    GateCategory,
    GateResult,
    SCHEMA_VERSION,
    Severity,
    aggregate_promotion,
    blocking_failures,
    warnings as gate_warnings,
    write_robustness_gates_json,
)
from hft3.validation.lanes.lane_aware_promotion import check_candidate_lane_coverage

REPORT_JSON_REL = Path("runtime/validation/champion_promotion_gate_report.json")
REPORT_MD_REL = Path("runtime/validation/champion_promotion_gate_report.md")
ROBUSTNESS_GATES_REL = Path("runtime/validation/robustness_gates.json")
SCORECARD_JSON_REL = Path("runtime/validation/backtester_certification_scorecard.json")


@dataclass
class PromotionGateResult:
    passed: bool
    failures: list[str] = field(default_factory=list)
    model_id: str = ""
    event_id: str = ""
    symbol: str = ""
    latency_ms: float = 0.0
    queue_model: str = ""
    certification_status: str = ""
    certification_commit: str = ""
    current_commit: str = ""
    stale: bool = True
    # ---- Phase 8 additions (backward compatible: both default to empty) ----
    gates: list[GateResult] = field(default_factory=list)
    gate_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["gates"] = [g.to_dict() for g in self.gates]
        return d


def _run_t0_fast_gate(root: Path) -> tuple[bool, str]:
    cmd = [sys.executable, "-m", "pytest", "tests/backtester_validation/fast", "-q"]
    proc = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True)
    if proc.returncode == 0:
        return True, ""
    return False, (proc.stdout or "") + (proc.stderr or "")


def _scorecard_covers(
    registry: Any,
    *,
    event_id: str,
    symbol: str,
    latency_ms: float,
    queue_model: str,
) -> tuple[bool, str]:
    if registry.latest_certification_status != "GREEN":
        return False, f"registry_status={registry.latest_certification_status}"
    if symbol and registry.covered_symbols and symbol not in registry.covered_symbols:
        return False, f"symbol {symbol} not in covered_symbols"
    if event_id:
        prefix = event_id.split("_")[0].lower()
        covered = [e.lower() for e in registry.covered_event_types]
        if covered and prefix not in covered and "macro" not in covered and "synthetic" not in covered:
            return False, f"event_type for {event_id} not covered"
    if latency_ms and registry.covered_latency_bands:
        bands = [float(x) for x in registry.covered_latency_bands]
        if not any(abs(latency_ms - band) < 0.01 for band in bands):
            nearest = min(bands, key=lambda b: abs(latency_ms - b))
            return False, f"latency_ms {latency_ms} not in covered_latency_bands (nearest={nearest})"
    if queue_model and registry.covered_queue_models and queue_model not in registry.covered_queue_models:
        return False, f"queue_model {queue_model} not in covered_queue_models"
    return True, ""


def _scorecard_path(root: Path, registry: Any) -> Path:
    scorecard_path = Path(getattr(registry, "scorecard_path", "") or SCORECARD_JSON_REL)
    if scorecard_path.is_absolute():
        return scorecard_path
    return root / scorecard_path


def _load_scorecard_payload(scorecard_path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(scorecard_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _artifact_ref(path: Path, root: Path) -> str:
    if path.is_relative_to(root):
        return str(path.relative_to(root)).replace("\\", "/")
    return str(path).replace("\\", "/")


def _coverage_reason_code(covers_reason: str) -> str:
    reason = covers_reason.upper()
    if "SYMBOL" in reason:
        return "SCORECARD_NOT_COVERED_SYMBOL"
    if "EVENT" in reason:
        return "SCORECARD_NOT_COVERED_EVENT_TYPE"
    if "LATENCY" in reason:
        return "SCORECARD_NOT_COVERED_LATENCY"
    if "QUEUE" in reason:
        return "SCORECARD_NOT_COVERED_QUEUE_MODEL"
    if "REGISTRY_STATUS" in reason:
        return "SCORECARD_NOT_COVERED_REGISTRY_STATUS"
    return "SCORECARD_NOT_COVERED"


def evaluate_promotion_gates(
    *,
    model_id: str = "",
    event_id: str = "",
    symbol: str = "",
    latency_ms: float = 0.0,
    queue_model: str = "",
    campaign_dir: Path | None = None,
    skip_t0_rerun: bool = False,
    root: Path | None = None,
) -> list[GateResult]:
    """Return the new `list[GateResult]` for the promotion gate.

    Each pre-existing check is expressed as one `GateResult`. The
    `aggregate_promotion` helper reduces this list to the legacy
    `passed / failures / warnings` triple.
    """
    root = root or repo_root()
    registry = load_registry(root)
    staleness = assess_staleness(root, registry=registry)
    gates: list[GateResult] = []

    # 1. registry status
    gates.append(
        GateResult(
            gate_name="registry_status_green",
            gate_category=GateCategory.REGISTRY_ELIGIBILITY,
            metric_name="latest_certification_status",
            threshold=None,
            observed_value=None,
            comparison_operator="==",
            pass_fail=registry.latest_certification_status == "GREEN",
            severity=Severity.BLOCKING,
            reason_code=(
                "REGISTRY_STATUS_GREEN"
                if registry.latest_certification_status == "GREEN"
                else "REGISTRY_STATUS_NOT_GREEN"
            ),
            artifact_reference="runtime/validation/certification_registry.json",
        )
    )

    # 2. certification not stale
    gates.append(
        GateResult(
            gate_name="certification_not_stale",
            gate_category=GateCategory.REGISTRY_ELIGIBILITY,
            metric_name="certification_is_current",
            threshold=None,
            observed_value=None,
            comparison_operator="==",
            pass_fail=staleness.certification_is_current,
            severity=Severity.BLOCKING,
            reason_code=(
                "CERTIFICATION_CURRENT"
                if staleness.certification_is_current
                else f"CERTIFICATION_STALE_{staleness.stale_reason.upper()}"
            ),
        )
    )

    # 3. scorecard present
    scorecard_path = _scorecard_path(root, registry)
    scorecard_ok = scorecard_path.is_file()
    scorecard_payload = _load_scorecard_payload(scorecard_path) if scorecard_ok else None
    gates.append(
        GateResult(
            gate_name="scorecard_present",
            gate_category=GateCategory.REGISTRY_ELIGIBILITY,
            metric_name="scorecard_path_exists",
            threshold=None,
            observed_value=None,
            comparison_operator="==",
            pass_fail=scorecard_ok,
            severity=Severity.BLOCKING,
            reason_code=(
                "SCORECARD_PRESENT" if scorecard_ok else "SCORECARD_MISSING"
            ),
            artifact_reference="runtime/validation/backtester_certification_scorecard.json",
        )
    )

    if scorecard_ok and scorecard_payload is None:
        gates.append(
            GateResult(
                gate_name="scorecard_valid_json",
                gate_category=GateCategory.REGISTRY_ELIGIBILITY,
                metric_name="scorecard_json_parseable",
                threshold=None,
                observed_value=None,
                comparison_operator="==",
                pass_fail=False,
                severity=Severity.BLOCKING,
                reason_code="SCORECARD_INVALID_JSON",
                artifact_reference=_artifact_ref(scorecard_path, root),
            )
        )

    # 4-7. scorecard coverage (lane-aware when available, legacy flat registry otherwise)
    if scorecard_ok and scorecard_payload is not None:
        lane_coverage = scorecard_payload.get("lane_coverage") if scorecard_payload else None
        if isinstance(lane_coverage, dict):
            lane_result = check_candidate_lane_coverage(
                model_id=model_id,
                symbol=symbol,
                event_id=event_id,
                latency_ms=latency_ms,
                queue_model=queue_model,
                scorecard=scorecard_payload,
            )
            gates.append(
                GateResult(
                    gate_name="lane_scorecard_covers",
                    gate_category=GateCategory.REGISTRY_ELIGIBILITY,
                    metric_name="lane_coverage_check",
                    threshold=None,
                    observed_value=None,
                    comparison_operator="==",
                    pass_fail=lane_result.passed,
                    severity=Severity.BLOCKING,
                    reason_code="LANE_SCORECARD_COVERS" if lane_result.passed else "LANE_SCORECARD_NOT_COVERED",
                    artifact_reference=_artifact_ref(scorecard_path, root),
                    extra=lane_result.to_dict(),
                )
            )
        else:
            covers_ok, covers_reason = _scorecard_covers(
                registry,
                event_id=event_id,
                symbol=symbol,
                latency_ms=latency_ms,
                queue_model=queue_model,
            )
            gates.append(
                GateResult(
                    gate_name="scorecard_covers",
                    gate_category=GateCategory.REGISTRY_ELIGIBILITY,
                    metric_name="coverage_check",
                    threshold=None,
                    observed_value=None,
                    comparison_operator="==",
                    pass_fail=covers_ok,
                    severity=Severity.BLOCKING,
                    reason_code="SCORECARD_COVERS" if covers_ok else _coverage_reason_code(covers_reason),
                    artifact_reference=_artifact_ref(scorecard_path, root),
                    extra={"failure_reason": covers_reason} if covers_reason else {},
                )
            )

    # 8. T0 pytest (or fast_gate_report reload)
    if skip_t0_rerun:
        fg = load_fast_gate_report(root)
        fg_passed = bool(fg and fg.get("passed"))
        fg_stale = bool(fg and fg.get("git_sha") and fg.get("git_sha") != git_sha(root))
        gates.append(
            GateResult(
                gate_name="t0_fast_gate",
                gate_category=GateCategory.BACKTEST_VALIDITY,
                metric_name="fast_gate_passed",
                threshold=None,
                observed_value=None,
                comparison_operator="==",
                pass_fail=fg_passed and not fg_stale,
                severity=Severity.BLOCKING,
                reason_code=(
                    "T0_FAST_GATE_PASSED"
                    if fg_passed and not fg_stale
                    else ("T0_FAST_GATE_FAILED" if not fg_passed else "T0_FAST_GATE_STALE")
                ),
                artifact_reference="runtime/validation/fast_gate_report.json",
            )
        )
    else:
        t0_ok, t0_out = _run_t0_fast_gate(root)
        gates.append(
            GateResult(
                gate_name="t0_fast_gate",
                gate_category=GateCategory.BACKTEST_VALIDITY,
                metric_name="pytest_returncode",
                threshold=0.0,
                observed_value=0.0 if t0_ok else 1.0,
                comparison_operator="==",
                pass_fail=t0_ok,
                severity=Severity.BLOCKING,
                reason_code="T0_PYTEST_PASSED" if t0_ok else "T0_PYTEST_FAILED",
                artifact_reference="runtime/validation/fast_gate_report.json",
                extra={"output_tail": t0_out[-500:]} if not t0_ok else {},
            )
        )

    # 9. campaign stamp promotion_eligible (if campaign_dir provided)
    if campaign_dir is not None:
        summary_path = campaign_dir / "summary.json"
        if not summary_path.is_file():
            gates.append(
                GateResult(
                    gate_name="campaign_summary_present",
                    gate_category=GateCategory.REGISTRY_ELIGIBILITY,
                    metric_name="campaign_summary_path",
                    threshold=None,
                    observed_value=None,
                    comparison_operator="==",
                    pass_fail=False,
                    severity=Severity.BLOCKING,
                    reason_code="CAMPAIGN_SUMMARY_MISSING",
                    artifact_reference=str(summary_path),
                )
            )
        else:
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                stamp = summary.get("certification_stamp") or {}
                eligible = bool(stamp.get("promotion_eligible"))
                label = stamp.get("promotion_label", "")
                gates.append(
                    GateResult(
                        gate_name="campaign_stamp_promotion_eligible",
                        gate_category=GateCategory.REGISTRY_ELIGIBILITY,
                        metric_name="stamp_promotion_eligible",
                        threshold=None,
                        observed_value=None,
                        comparison_operator="==",
                        pass_fail=eligible,
                        severity=Severity.BLOCKING,
                        reason_code=(
                            "CAMPAIGN_STAMP_ELIGIBLE" if eligible else "CAMPAIGN_STAMP_NOT_ELIGIBLE"
                        ),
                        artifact_reference=str(summary_path),
                        extra={"promotion_label": label},
                    )
                )
            except json.JSONDecodeError:
                gates.append(
                    GateResult(
                        gate_name="campaign_summary_valid_json",
                        gate_category=GateCategory.REGISTRY_ELIGIBILITY,
                        metric_name="json_parseable",
                        threshold=None,
                        observed_value=None,
                        comparison_operator="==",
                        pass_fail=False,
                        severity=Severity.BLOCKING,
                        reason_code="CAMPAIGN_SUMMARY_INVALID_JSON",
                        artifact_reference=str(summary_path),
                    )
                )

    return gates


def evaluate_promotion_gate(
    *,
    model_id: str = "",
    event_id: str = "",
    symbol: str = "",
    latency_ms: float = 0.0,
    queue_model: str = "",
    campaign_dir: Path | None = None,
    skip_t0_rerun: bool = False,
    root: Path | None = None,
) -> PromotionGateResult:
    """Backward-compatible T4 promotion gate. Returns the legacy
    `PromotionGateResult` shape, populated with the new `gates` list and
    `gate_warnings` field."""
    root = root or repo_root()
    registry = load_registry(root)
    staleness = assess_staleness(root, registry=registry)
    gates = evaluate_promotion_gates(
        model_id=model_id,
        event_id=event_id,
        symbol=symbol,
        latency_ms=latency_ms,
        queue_model=queue_model,
        campaign_dir=campaign_dir,
        skip_t0_rerun=skip_t0_rerun,
        root=root,
    )
    passed, failures, warns = aggregate_promotion(gates)
    return PromotionGateResult(
        passed=passed,
        failures=failures,
        model_id=model_id,
        event_id=event_id,
        symbol=symbol,
        latency_ms=latency_ms,
        queue_model=queue_model,
        certification_status=registry.latest_certification_status,
        certification_commit=registry.latest_certification_commit,
        current_commit=git_sha(root),
        stale=not staleness.certification_is_current,
        gates=gates,
        gate_warnings=warns,
    )


def write_promotion_gate_report(result: PromotionGateResult, root: Path | None = None) -> tuple[Path, Path]:
    root = root or repo_root()
    payload = {
        "tier": "T4",
        "schema_version": SCHEMA_VERSION,
        "passed": result.passed,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        **result.to_dict(),
    }
    json_path = root / REPORT_JSON_REL
    md_path = root / REPORT_MD_REL
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    md_lines = [
        "# Champion Promotion Gate Report",
        "",
        f"- **Passed:** {result.passed}",
        f"- **Event ID:** {result.event_id}",
        f"- **Symbol:** {result.symbol}",
        f"- **Certification status:** {result.certification_status}",
        f"- **Stale:** {result.stale}",
        "",
    ]
    if result.failures:
        md_lines.append("## Failures")
        for f in result.failures:
            md_lines.append(f"- {f}")
        md_lines.append("")
    if result.gate_warnings:
        md_lines.append("## Warnings")
        for w in result.gate_warnings:
            md_lines.append(f"- {w}")
        md_lines.append("")
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return json_path, md_path


def write_robustness_gates_for_promotion(
    gates: list[GateResult], root: Path | None = None, *, run_id: str = ""
) -> Path:
    """Write `runtime/validation/robustness_gates.json` per Phase 12."""
    root = root or repo_root()
    out = root / ROBUSTNESS_GATES_REL
    return write_robustness_gates_json(
        out,
        gates,
        tier="T4",
        run_id=run_id,
        git_sha=git_sha(root),
        thresholds_source="apps/workbench/config/wfc_gate.yaml",
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
    )


PROMOTION_ELIGIBILITY_REQUIRED_GATES = frozenset({
    "data_resolution_eligibility",
    "monte_carlo_sharpe_p05",
    "oos_max_drawdown",
    "walk_forward_pass",
    "artifact_completeness",
    "double_wf_correlation",
})

PROMOTION_ELIGIBILITY_OPTIONAL_GATES = frozenset({
    "survives_latency",
    "ev_positive",
})


@dataclass
class PromotionEligibilityVerdict:
    eligible: bool
    failed_gates: list[str] = field(default_factory=list)
    missing_gates: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_promotion_eligibility(
    gate_dicts: list[dict[str, Any]],
) -> PromotionEligibilityVerdict:
    """Single combined invariant for promotion eligibility.

    All required gates must be present and passing. Optional gates
    (survives_latency, ev_positive) are checked only when present.
    Any failure yields ineligible.
    """
    gates_by_name = {g.get("gate_name"): g for g in gate_dicts}
    failed: list[str] = []
    missing: list[str] = []
    for name in PROMOTION_ELIGIBILITY_REQUIRED_GATES:
        gate = gates_by_name.get(name)
        if gate is None:
            missing.append(name)
        elif gate.get("pass_fail") is not True:
            failed.append(name)
    for name in PROMOTION_ELIGIBILITY_OPTIONAL_GATES:
        gate = gates_by_name.get(name)
        if gate is not None and gate.get("pass_fail") is not True:
            failed.append(name)
    eligible = not failed and not missing
    return PromotionEligibilityVerdict(
        eligible=eligible,
        failed_gates=failed,
        missing_gates=missing,
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="T4 champion promotion gate")
    parser.add_argument("--event-id", default="")
    parser.add_argument("--model-id", default="")
    parser.add_argument("--symbol", default="")
    parser.add_argument("--latency-ms", type=float, default=0.0)
    parser.add_argument("--queue-model", default="")
    parser.add_argument("--campaign-dir", default="")
    parser.add_argument("--skip-t0-rerun", action="store_true")
    parser.add_argument(
        "--write-robustness-gates",
        action="store_true",
        help="Also write runtime/validation/robustness_gates.json (Phase 12).",
    )
    parser.add_argument("--run-id", default="")
    args = parser.parse_args(argv)
    campaign = Path(args.campaign_dir) if args.campaign_dir else None
    result = evaluate_promotion_gate(
        event_id=args.event_id,
        model_id=args.model_id,
        symbol=args.symbol,
        latency_ms=args.latency_ms,
        queue_model=args.queue_model,
        campaign_dir=campaign,
        skip_t0_rerun=args.skip_t0_rerun,
    )
    write_promotion_gate_report(result)
    if args.write_robustness_gates:
        write_robustness_gates_for_promotion(result.gates, run_id=args.run_id)
    if result.failures:
        for f in result.failures:
            print(f"FAIL: {f}", file=sys.stderr)
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
