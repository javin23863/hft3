"""ML8 — autonomous re-arm: the only component that flips a model to LIVE.

Builds the mandatory G0..G8 gate report, runs the anti-bypass chain, audits the
full decision, and arms ONLY if every required gate passes AND autonomy is
explicitly enabled for live arm. A missing required gate trips the breaker. Any
unreadable input is fail-closed (the gate fails). No order is ever placed here;
"arm" = a gated lifecycle SHADOW/DEGRADED/ARCHIVED_PAUSED -> LIVE transition
and an ARM audit record. The actual live-engine symlink flip is delegated to the
deployment validator (not invoked in research/REPLAY).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from autonomy import audit, breaker, config as acfg, gates
from model_metrics import lifecycle


_DEFECT_LEDGER_CLOSED_STATUSES = {"CLOSED", "FIXED", "RESOLVED"}


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
        status = str(rec.get("status") or "").upper()
        # Only known closed statuses are closed. Missing/unknown status is
        # treated as OPEN (fail-closed), not silently waived.
        if status not in _DEFECT_LEDGER_CLOSED_STATUSES:
            open_items += 1
    return (open_items == 0, f"{open_items} OPEN/unknown items")


def cert_green_not_stale(cert: Optional[dict]) -> tuple[bool, str]:
    if not isinstance(cert, dict):
        return (False, "cert missing")
    status = cert.get("latest_certification_status") or cert.get("status")
    if cert.get("stale") is not False:
        stale = cert.get("stale")
        eligible = cert.get("promotion_eligible")
        return (False, f"status={status} stale={stale} eligible={eligible}")
    if cert.get("promotion_eligible") is not True:
        stale = cert.get("stale")
        eligible = cert.get("promotion_eligible")
        return (False, f"status={status} stale={stale} eligible={eligible}")
    stale = cert.get("stale")
    eligible = cert.get("promotion_eligible")
    ok = status == "GREEN"
    return (ok, f"status={status} stale={stale} eligible={eligible}")


# --- gate context (explicit inputs; orchestrator computes them) -------------
@dataclass
class GateContext:
    cert: Optional[dict] = None
    cert_ok_override: Optional[bool] = None
    gauntlet_passed: bool = False
    promotion_passed: bool = False
    defect_ledger_path: Optional[Path] = None
    options_defect_ledger_root: Optional[Path] = None
    defect_ledger_empty_override: Optional[bool] = None
    shadow_passed: bool = False
    embargo_clean: bool = False
    determinism_ok: bool = False
    kill_drill_ok: bool = False
    details: dict = field(default_factory=dict)


def _candidate_inputs(model_id: str, ctx: GateContext) -> tuple[str, str, str]:
    details = ctx.details or {}
    symbol = str(details.get("symbol", ""))
    event_id = str(details.get("event_id", ""))
    return model_id, symbol, event_id


def _candidate_in_options_scope(model_id: str, ctx: GateContext) -> bool:
    model_id, symbol, event_id = _candidate_inputs(model_id, ctx)
    try:
        from hft3.validation.lanes.lane import Lane
        from hft3.validation.lanes.lane_aware_promotion import resolve_lane_for_candidate

        lane = resolve_lane_for_candidate(model_id=model_id, symbol=symbol, event_id=event_id)
        if lane in (Lane.CME_OPTIONS, Lane.EQUITIES):
            return True
    except Exception:  # noqa: BLE001
        pass
    probes = (model_id, symbol, event_id)
    return any(str(v).upper().startswith(("FOPT_", "OPTIONS_", "PARITY_")) for v in probes)


def _capability_profile_from_config(lane_value: str, cfg: object) -> tuple[object | None, str]:
    profile = getattr(cfg, "capability_profile", None)
    if profile is None and hasattr(cfg, "to_dict"):
        try:
            cfg_dict = cfg.to_dict()
        except Exception as exc:  # noqa: BLE001
            return None, f"lane '{lane_value}' config unreadable: {exc}"
        profile = cfg_dict.get("capability_profile") if isinstance(cfg_dict, dict) else None
    if profile is None:
        return None, f"lane '{lane_value}' capability_profile missing"
    return profile, ""


def _capability_research_only(profile: object) -> tuple[Optional[bool], str]:
    if isinstance(profile, dict):
        return profile.get("research_only"), str(profile.get("name", "<unnamed>"))
    return getattr(profile, "research_only", None), str(getattr(profile, "name", "<unnamed>"))


def _candidate_live_capability_ok(model_id: str, ctx: GateContext) -> tuple[bool, str]:
    model_id, symbol, event_id = _candidate_inputs(model_id, ctx)
    try:
        from hft3.validation.lanes.lane import Lane
        from hft3.validation.lanes.lane_registry import LaneRegistry
        from hft3.validation.lanes.lane_aware_promotion import resolve_lane_for_candidate
        from hft3.validation.lanes.registration import register_all_lanes

        register_all_lanes()
        lane = resolve_lane_for_candidate(
            model_id=model_id,
            symbol=symbol,
            event_id=event_id,
            auto_register=False,
        )
        reg = LaneRegistry.instance().get(lane)
        if reg is None:
            return False, f"lane '{lane.value}' is not registered; capability_profile unreadable"
        cfg = reg.config_loader()
    except Exception as exc:  # noqa: BLE001
        return False, f"lane capability_profile unreadable: {exc}"

    profile, profile_error = _capability_profile_from_config(lane.value, cfg)
    if profile_error:
        return False, profile_error

    research_only, profile_name = _capability_research_only(profile)
    if research_only is True:
        return False, f"lane '{lane.value}' capability_profile '{profile_name}' is research_only"
    if research_only is not False:
        return False, f"lane '{lane.value}' capability_profile research_only flag unreadable"

    if lane != Lane.CME_OPTIONS and _candidate_in_options_scope(model_id, ctx):
        try:
            options_reg = LaneRegistry.instance().get(Lane.CME_OPTIONS)
            if options_reg is None:
                return False, "options scope lane 'cme_options' is not registered; capability_profile unreadable"
            options_cfg = options_reg.config_loader()
        except Exception as exc:  # noqa: BLE001
            return False, f"options scope capability_profile unreadable: {exc}"
        options_profile, options_error = _capability_profile_from_config(Lane.CME_OPTIONS.value, options_cfg)
        if options_error:
            return False, f"options scope {options_error}"
        options_research_only, options_profile_name = _capability_research_only(options_profile)
        if options_research_only is True:
            return (
                False,
                f"options scope candidate resolved to lane '{lane.value}', but canonical "
                f"'{Lane.CME_OPTIONS.value}' capability_profile '{options_profile_name}' is research_only",
            )
        if options_research_only is not False:
            return False, f"options scope '{Lane.CME_OPTIONS.value}' research_only flag unreadable"
    return True, f"lane '{lane.value}' capability_profile '{profile_name}' permits live arm"


def _options_defect_ledger_empty_for_candidate(model_id: str, ctx: GateContext) -> tuple[bool, str]:
    if not _candidate_in_options_scope(model_id, ctx):
        return True, "options ledger not applicable"
    try:
        from hft3.validation.options_defect_ledger import load_options_defect_ledger

        ledger = load_options_defect_ledger(ctx.options_defect_ledger_root or lifecycle._repo_root())
    except Exception as exc:  # noqa: BLE001
        return False, f"options defect ledger unreadable: {exc}"
    return (
        ledger.empty,
        f"options defect ledger {ledger.status}: {ledger.open_count} OPEN ({','.join(ledger.open_ids)})",
    )


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
    options_ledger_ok, options_ledger_detail = _options_defect_ledger_empty_for_candidate(model_id, ctx)
    ledger_ok = ledger_ok and options_ledger_ok
    if not options_ledger_ok:
        ledger_detail = f"{ledger_detail}; {options_ledger_detail}"

    capability_ok, capability_detail = _candidate_live_capability_ok(model_id, ctx)
    promotion_ok = ctx.promotion_passed and capability_ok
    promotion_detail = f"PromotionGate.evaluate={ctx.promotion_passed}; {capability_detail}"

    return [
        gates.GateResult(gates.GATE_MASTER_ENABLE, master, "master+rearm.allow_live"),
        gates.GateResult(gates.GATE_BREAKER, breaker_closed, "circuit breaker closed"),
        gates.GateResult(gates.GATE_GREEN_CERT, cert_ok, cert_detail),
        gates.GateResult(gates.GATE_GAUNTLET, ctx.gauntlet_passed, "DSR/PBO/bootstrap/fee-x2 + holm"),
        gates.GateResult(gates.GATE_PROMOTION, promotion_ok, promotion_detail),
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
    capability_ok, capability_detail = _candidate_live_capability_ok(model_id, ctx)
    if not capability_ok:
        audit.append(audit.AUTO_ARM_REFUSED,
                     {"reason": f"lane capability changed between eval and arm: {capability_detail}"},
                     model_id=model_id, ts=ts)
        return {"armed": False, "allowed": False, "reason": "TOCTOU: lane capability blocked before arm",
                "missing": [], "failed": [gates.GATE_PROMOTION], "trip_breaker": False,
                "gates": decision["gates"]}
    options_ledger_ok, options_ledger_detail = _options_defect_ledger_empty_for_candidate(model_id, ctx)
    if not options_ledger_ok:
        audit.append(audit.AUTO_ARM_REFUSED,
                     {"reason": f"options ledger changed between eval and arm: {options_ledger_detail}"},
                     model_id=model_id, ts=ts)
        return {"armed": False, "allowed": False, "reason": "TOCTOU: options ledger blocked before arm",
                "missing": [], "failed": [gates.GATE_DEFECT_LEDGER], "trip_breaker": False,
                "gates": decision["gates"]}

    # All gates passed AND autonomy permits live arm. Record the arm only after
    # the lifecycle registry actually moves to LIVE.
    rec = lifecycle.get_record(model_id)
    from_state = rec.current_state if rec else None
    allowed_from_states = (lifecycle.SHADOW, lifecycle.DEGRADED, lifecycle.ARCHIVED_PAUSED)
    if from_state not in allowed_from_states:
        reason = f"cannot arm from lifecycle state {from_state or 'MISSING'}"
        audit.append(audit.AUTO_ARM_REFUSED, {"reason": reason, "from_state": from_state},
                     model_id=model_id, ts=ts)
        return {"armed": False, "allowed": False, "reason": reason,
                "missing": [], "failed": ["lifecycle_state"], "trip_breaker": False,
                "gates": decision["gates"]}
    try:
        lifecycle.apply_transition(model_id, lifecycle.LIVE, trigger="auto_arm",
                                   reason="gate chain passed", actor=actor, ts=ts)
    except Exception as exc:  # noqa: BLE001
        reason = f"lifecycle transition to LIVE failed: {exc}"
        audit.append(audit.AUTO_ARM_REFUSED, {"reason": reason, "from_state": from_state},
                     model_id=model_id, ts=ts)
        return {"armed": False, "allowed": False, "reason": reason,
                "missing": [], "failed": ["lifecycle_state"], "trip_breaker": False,
                "gates": decision["gates"]}
    rec_after = lifecycle.get_record(model_id)
    if rec_after is None or rec_after.current_state != lifecycle.LIVE:
        reason = "lifecycle transition returned without LIVE state"
        audit.append(audit.AUTO_ARM_REFUSED, {"reason": reason, "from_state": from_state},
                     model_id=model_id, ts=ts)
        return {"armed": False, "allowed": False, "reason": reason,
                "missing": [], "failed": ["lifecycle_state"], "trip_breaker": False,
                "gates": decision["gates"]}
    breaker.record_arm_outcome(model_id, "pass", ts=ts)
    audit.append(audit.AUTO_ARM, {"operator": actor, "kill_switch_drill_passed": True,
                                  "from_state": from_state}, model_id=model_id, ts=ts)
    return {"armed": True, **decision}
