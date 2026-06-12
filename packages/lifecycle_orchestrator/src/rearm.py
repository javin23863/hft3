"""ML8 — autonomous re-arm: the only component that flips a model to LIVE.

Builds the mandatory G0..G8 gate report, runs the anti-bypass chain, audits the
full decision, and arms ONLY if every required gate passes AND autonomy is
explicitly enabled for live arm. A missing required gate trips the breaker. Any
unreadable input is fail-closed (the gate fails). No order is ever placed here;
"arm" = a lifecycle SHADOW/ARCHIVED_PAUSED -> LIVE transition + an ARM audit
record. The actual live-engine symlink flip is delegated to the deployment
validator (not invoked in research/REPLAY).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from autonomy import audit, breaker, config as acfg, gates
from model_metrics import lifecycle


# --- standalone fail-closed readers (used by the orchestrator) --------------
def defect_ledger_empty(path: Optional[Path] = None) -> tuple[bool, str]:
    """Empty (no OPEN items) => True. Absent/unparseable => False (fail-closed)."""
    p = path or (lifecycle._repo_root() / "runtime" / "validation" / "defect_ledger.jsonl")
    if not p.exists():
        return (False, "defect ledger absent (fail-closed)")
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return (False, f"ledger unreadable: {exc}")
    open_items = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as exc:
            # An unparseable row is an unknown item -> fail-closed.
            return (False, f"ledger row unparseable: {exc}")
        status = rec.get("status")
        # Missing status is treated as OPEN (fail-closed), not silently closed.
        if status is None or str(status).upper() == "OPEN":
            open_items += 1
    return (open_items == 0, f"{open_items} OPEN/unknown items")


def cert_green_not_stale(cert: Optional[dict]) -> tuple[bool, str]:
    if not isinstance(cert, dict):
        return (False, "cert missing")
    status = cert.get("latest_certification_status") or cert.get("status")
    stale = cert.get("stale", False)
    eligible = cert.get("promotion_eligible", True)
    ok = status == "GREEN" and not stale and eligible
    return (ok, f"status={status} stale={stale} eligible={eligible}")


# --- gate context (explicit inputs; orchestrator computes them) -------------
@dataclass
class GateContext:
    cert: Optional[dict] = None
    cert_ok_override: Optional[bool] = None
    gauntlet_passed: bool = False
    promotion_passed: bool = False
    defect_ledger_path: Optional[Path] = None
    defect_ledger_empty_override: Optional[bool] = None
    shadow_passed: bool = False
    embargo_clean: bool = False
    determinism_ok: bool = False
    kill_drill_ok: bool = False
    details: dict = field(default_factory=dict)


def build_gate_results(model_id: str, ctx: GateContext) -> list[gates.GateResult]:
    # G0 master-enable (incl. rearm.allow_live), G0' breaker
    master = acfg.master_enabled() and acfg.can_arm_live(model_id=model_id)
    breaker_closed = not breaker.is_frozen()

    if ctx.cert_ok_override is not None:
        cert_ok, cert_detail = ctx.cert_ok_override, "override"
    else:
        cert_ok, cert_detail = cert_green_not_stale(ctx.cert)

    if ctx.defect_ledger_empty_override is not None:
        ledger_ok, ledger_detail = ctx.defect_ledger_empty_override, "override"
    else:
        ledger_ok, ledger_detail = defect_ledger_empty(ctx.defect_ledger_path)

    return [
        gates.GateResult(gates.GATE_MASTER_ENABLE, master, "master+rearm.allow_live"),
        gates.GateResult(gates.GATE_BREAKER, breaker_closed, "circuit breaker closed"),
        gates.GateResult(gates.GATE_GREEN_CERT, cert_ok, cert_detail),
        gates.GateResult(gates.GATE_GAUNTLET, ctx.gauntlet_passed, "DSR/PBO/bootstrap/fee-x2 + holm"),
        gates.GateResult(gates.GATE_PROMOTION, ctx.promotion_passed, "PromotionGate.evaluate"),
        gates.GateResult(gates.GATE_DEFECT_LEDGER, ledger_ok, ledger_detail),
        gates.GateResult(gates.GATE_SHADOW, ctx.shadow_passed, "shadow 2026 window §4.4"),
        gates.GateResult(gates.GATE_EMBARGO, ctx.embargo_clean, "no >=2026 data in fitting"),
        gates.GateResult(gates.GATE_DETERMINISM, ctx.determinism_ok, "byte-identical replay"),
        gates.GateResult(gates.GATE_KILL_DRILL, ctx.kill_drill_ok, "kill-switch drill halt <=1s"),
    ]


def attempt_rearm(model_id: str, ctx: GateContext, *, actor: str = "autonomous-orchestrator", ts: Optional[str] = None) -> dict:
    """Run the gate chain; arm only if allowed. Always audited."""
    results = build_gate_results(model_id, ctx)
    decision = gates.evaluate_gate_chain(results)
    audit.append(audit.AUTO_GATE_EVAL, {"decision": {k: v for k, v in decision.items() if k != "gates"},
                                        "gates": decision["gates"]}, model_id=model_id, ts=ts)

    if not decision["allowed"]:
        if decision["trip_breaker"]:
            reason = f"missing required gate(s): {decision['missing']}"
            breaker.trip(reason, ts=ts)
            audit.append(audit.AUTONOMY_FROZEN, {"reason": reason}, model_id=model_id, ts=ts)
        audit.append(audit.AUTO_ARM_REFUSED,
                     {"reason": decision["reason"], "missing": decision["missing"], "failed": decision["failed"]},
                     model_id=model_id, ts=ts)
        return {"armed": False, **decision}

    # TOCTOU guard: re-check the volatile gates immediately before mutating live.
    # The breaker could have tripped or autonomy been disabled during evaluation.
    if breaker.is_frozen() or not (acfg.master_enabled() and acfg.can_arm_live(model_id=model_id)):
        audit.append(audit.AUTO_ARM_REFUSED,
                     {"reason": "volatile gate changed between eval and arm (TOCTOU guard)"},
                     model_id=model_id, ts=ts)
        return {"armed": False, "allowed": False, "reason": "TOCTOU: state changed before arm",
                "missing": [], "failed": ["master_enable_or_breaker"], "trip_breaker": False,
                "gates": decision["gates"]}

    # All gates passed AND autonomy permits live arm. Record the arm.
    rec = lifecycle.get_record(model_id)
    from_state = rec.current_state if rec else None
    if from_state in (lifecycle.SHADOW, lifecycle.ARCHIVED_PAUSED):
        lifecycle.apply_transition(model_id, lifecycle.LIVE, trigger="auto_arm",
                                   reason="gate chain passed", actor=actor, ts=ts)
    breaker.record_arm_outcome(model_id, "pass", ts=ts)
    audit.append(audit.AUTO_ARM, {"operator": actor, "kill_switch_drill_passed": True,
                                  "from_state": from_state}, model_id=model_id, ts=ts)
    return {"armed": True, **decision}
