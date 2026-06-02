"""Unified gate-result schema for the HFT3 validation pipeline (Phase 8).

Every emitter (T0 fast gate, T1 stamp, T2 certification, T3 WFC + robustness
pack, T4 promotion) produces a `list[GateResult]`. Aggregators reduce a list
to the legacy `PromotionGateResult` shape for backward compatibility.

The 17 gate categories (the spec says 16; we cover all 17) are:

  1.  data_integrity
  2.  leakage_prevention
  3.  backtest_validity
  4.  execution_realism
  5.  robustness
  6.  walk_forward
  7.  walk_forward_correlation
  8.  latency_sensitivity
  9.  cost_sensitivity
  10. slippage_sensitivity
  11. liquidity_capacity
  12. regime_stability
  13. parameter_stability
  14. drawdown_tail_risk
  15. model_combination_attribution
  16. registry_eligibility
  17. artifact_completeness
"""
from __future__ import annotations

import enum
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


class Severity(str, enum.Enum):
    """How a failed gate affects promotion."""

    INFO = "info"
    WARN = "warn"
    BLOCKING = "blocking"


class GateCategory(str, enum.Enum):
    DATA_INTEGRITY = "data_integrity"
    LEAKAGE_PREVENTION = "leakage_prevention"
    BACKTEST_VALIDITY = "backtest_validity"
    EXECUTION_REALISM = "execution_realism"
    ROBUSTNESS = "robustness"
    WALK_FORWARD = "walk_forward"
    WALK_FORWARD_CORRELATION = "walk_forward_correlation"
    LATENCY_SENSITIVITY = "latency_sensitivity"
    COST_SENSITIVITY = "cost_sensitivity"
    SLIPPAGE_SENSITIVITY = "slippage_sensitivity"
    LIQUIDITY_CAPACITY = "liquidity_capacity"
    REGIME_STABILITY = "regime_stability"
    PARAMETER_STABILITY = "parameter_stability"
    DRAWDOWN_TAIL_RISK = "drawdown_tail_risk"
    MODEL_COMBINATION_ATTRIBUTION = "model_combination_attribution"
    REGISTRY_ELIGIBILITY = "registry_eligibility"
    ARTIFACT_COMPLETENESS = "artifact_completeness"


COMPARISON_OPERATORS: frozenset[str] = frozenset({">=", "<=", "==", ">", "<", "in", "not_in"})

SCHEMA_VERSION = 1


@dataclass
class GateResult:
    """A single gate result. The 11 spec fields are all required.

    Additional fields may be passed via `extra` for forward compatibility
    with new schema versions.
    """

    gate_name: str
    gate_category: GateCategory
    metric_name: str
    threshold: Optional[float] = None
    observed_value: Optional[float] = None
    comparison_operator: str = ">="
    pass_fail: bool = False
    severity: Severity = Severity.BLOCKING
    reason_code: str = ""
    artifact_reference: Optional[str] = None
    blocking_status: bool = True
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.comparison_operator not in COMPARISON_OPERATORS:
            raise ValueError(
                f"comparison_operator must be one of {sorted(COMPARISON_OPERATORS)}, "
                f"got {self.comparison_operator!r}"
            )
        if (self.severity == Severity.BLOCKING) != self.blocking_status:
            raise ValueError(
                "severity and blocking_status must agree: "
                "BLOCKING ↔ True, INFO/WARN ↔ False"
            )
        if self.severity == Severity.INFO and self.pass_fail:
            return
        if self.threshold is None or self.observed_value is None:
            return
        _check(self.observed_value, self.threshold, self.comparison_operator, self.pass_fail)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["gate_category"] = self.gate_category.value
        d["severity"] = self.severity.value
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


def _check(observed: float, threshold: float, op: str, expected_pass: bool) -> None:
    """Raise if the comparison does not match `expected_pass`."""
    actual_pass: bool
    if op == ">=":
        actual_pass = observed >= threshold
    elif op == "<=":
        actual_pass = observed <= threshold
    elif op == ">":
        actual_pass = observed > threshold
    elif op == "<":
        actual_pass = observed < threshold
    elif op == "==":
        actual_pass = observed == threshold
    else:
        return
    if actual_pass != expected_pass:
        raise ValueError(
            f"observed {observed} {op} threshold {threshold} does not yield "
            f"pass_fail={expected_pass}"
        )


def blocking_failures(gates: list[GateResult]) -> list[GateResult]:
    """Return the subset of gates whose failure is blocking."""
    return [g for g in gates if g.severity == Severity.BLOCKING and not g.pass_fail]


def warnings(gates: list[GateResult]) -> list[GateResult]:
    """Return the subset of gates whose failure is a warning."""
    return [g for g in gates if g.severity == Severity.WARN and not g.pass_fail]


def aggregate_promotion(
    gates: list[GateResult],
) -> tuple[bool, list[str], list[str]]:
    """Reduce a list[GateResult] to (passed, failures, warnings) tuples.

    - `passed` is True iff no blocking gate failed.
    - `failures` is the list of blocking-failure descriptions in
      `"{gate_name}: {reason_code} ({observed} {op} {threshold})"` form.
      The free-text fragments used by the existing tests ("GREEN", "MISSING",
      "missing", etc.) are preserved when the gate's reason code contains them.
    - `warnings` is the list of warn-failure descriptions.
    """
    failures: list[str] = []
    for g in blocking_failures(gates):
        if g.threshold is None or g.observed_value is None:
            failures.append(f"{g.gate_name}: {g.reason_code}")
        else:
            failures.append(
                f"{g.gate_name}: {g.reason_code} ({g.observed_value} {g.comparison_operator} {g.threshold})"
            )
    warns: list[str] = []
    for g in warnings(gates):
        warns.append(f"{g.gate_name}: {g.reason_code}")
    passed = len(failures) == 0
    return passed, failures, warns


def write_robustness_gates_json(
    out_path: Any,
    gates: list[GateResult],
    *,
    tier: str = "T3",
    run_id: str = "",
    git_sha: str = "",
    thresholds_source: str = "",
    timestamp_utc: str = "",
) -> Any:
    """Write a `robustness_gates.json` artifact bundle for Phase 12."""
    from pathlib import Path
    import os
    import tempfile

    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "tier": tier,
        "run_id": run_id,
        "git_sha": git_sha,
        "timestamp_utc": timestamp_utc,
        "thresholds_source": thresholds_source,
        "gates": [g.to_dict() for g in gates],
        "summary": {
            "total": len(gates),
            "passed": sum(1 for g in gates if g.pass_fail),
            "blocking_failures": len(blocking_failures(gates)),
            "warnings": len(warnings(gates)),
            "passed_overall": len(blocking_failures(gates)) == 0,
        },
    }
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
    return path
