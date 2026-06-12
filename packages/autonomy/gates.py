"""The mandatory arm-gate chain + anti-bypass enforcement.

An autonomous arm is allowed ONLY if every required gate is present AND passes.
A *missing* required gate (e.g. a refactor silently dropped one) is not a pass —
it forces refusal and signals the breaker to trip. This is the defense against
the gate chain being weakened without anyone noticing.
"""
from __future__ import annotations

from dataclasses import dataclass

# Gate names (stable identifiers).
GATE_MASTER_ENABLE = "master_enable"
GATE_BREAKER = "breaker_closed"
GATE_GREEN_CERT = "green_cert_not_stale"
GATE_GAUNTLET = "gauntlet_pass"
GATE_PROMOTION = "promotion_gate"
GATE_DEFECT_LEDGER = "defect_ledger_empty"
GATE_SHADOW = "shadow_pass"
GATE_EMBARGO = "embargo_clean"
GATE_DETERMINISM = "determinism"
GATE_KILL_DRILL = "kill_switch_drill"

# Every one of these MUST appear in the report and MUST pass before an arm.
AUTONOMY_REQUIRED_GATES = frozenset({
    GATE_MASTER_ENABLE, GATE_BREAKER, GATE_GREEN_CERT, GATE_GAUNTLET,
    GATE_PROMOTION, GATE_DEFECT_LEDGER, GATE_SHADOW, GATE_EMBARGO,
    GATE_DETERMINISM, GATE_KILL_DRILL,
})


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    detail: str = ""
    blocking: bool = True

    def to_dict(self) -> dict:
        return {"name": self.name, "passed": self.passed, "detail": self.detail, "blocking": self.blocking}


def evaluate_gate_chain(results: list[GateResult]) -> dict:
    """Decide arm/refuse. Fail-closed.

    A *structural* anomaly — a missing required gate, an unknown extra gate, or a
    required gate that is non-blocking — is treated as tampering: refuse AND trip
    the breaker. Only when the gate set is EXACTLY the required set, all blocking,
    do we evaluate pass/fail.
    """
    present = {r.name for r in results}
    missing = sorted(AUTONOMY_REQUIRED_GATES - present)
    extra = sorted(present - AUTONOMY_REQUIRED_GATES)
    non_blocking_required = sorted(r.name for r in results if r.name in AUTONOMY_REQUIRED_GATES and not r.blocking)
    if missing or extra or non_blocking_required:
        return {
            "allowed": False,
            "reason": "gate set invalid (missing/extra/non-blocking required gate)",
            "missing": missing,
            "extra": extra,
            "non_blocking_required": non_blocking_required,
            "failed": [],
            "trip_breaker": True,
            "gates": [r.to_dict() for r in results],
        }
    failed = sorted(r.name for r in results if not r.passed)
    if failed:
        return {
            "allowed": False, "reason": "blocking gate(s) failed",
            "missing": [], "extra": [], "non_blocking_required": [],
            "failed": failed, "trip_breaker": False,
            "gates": [r.to_dict() for r in results],
        }
    return {
        "allowed": True, "reason": "all required gates passed",
        "missing": [], "extra": [], "non_blocking_required": [],
        "failed": [], "trip_breaker": False,
        "gates": [r.to_dict() for r in results],
    }
