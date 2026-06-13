"""Decay driver — the trigger that was missing.

For every LIVE/DEGRADED model it loads the FROZEN certified envelope, evaluates a
fresh observation against it (re-validation backtest now; the live stream later),
and:
  * ALWAYS records the result onto the record (``last_revalidation``) — this is
    what the submit gate reads, so a RED read flattens the model's orders even
    with autonomy off (detection is always on; it only annotates).
  * AUTO-DEMOTES (moves the lifecycle state) ONLY when autonomy ``demote`` is
    enabled — LIVE->DEGRADED, then DEGRADED->route-target. A clean GREEN read
    attempts recovery only through the re-arm gate chain; direct DEGRADED->LIVE
    transition is forbidden here. Every demotion is audited + counted by the
    breaker.

Observations are supplied as ``{model_id: observation_dict}`` (a re-validation
producer builds these; the driver itself is pure orchestration).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

from model_metrics import decay_detector, lifecycle


def _optional_bool(raw: dict, key: str) -> Optional[bool]:
    value = raw.get(key)
    return value if isinstance(value, bool) else None


def _strict_bool(raw: dict, key: str) -> bool:
    value = raw.get(key)
    return value if isinstance(value, bool) else False


def _gate_context_from_observation(obs: dict):
    from lifecycle_orchestrator.src import rearm

    raw = obs.get("rearm_gate_context") or obs.get("gate_context") or {}
    if not isinstance(raw, dict):
        raw = {}

    details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
    details = dict(details)
    for key in ("symbol", "event_id"):
        if key not in details and obs.get(key):
            details[key] = obs[key]

    ctx = rearm.GateContext(
        cert=raw.get("cert") if isinstance(raw.get("cert"), dict) else None,
        cert_ok_override=_optional_bool(raw, "cert_ok_override"),
        gauntlet_passed=_strict_bool(raw, "gauntlet_passed"),
        promotion_passed=_strict_bool(raw, "promotion_passed"),
        defect_ledger_empty_override=_optional_bool(raw, "defect_ledger_empty_override"),
        shadow_passed=_strict_bool(raw, "shadow_passed"),
        embargo_clean=_strict_bool(raw, "embargo_clean"),
        determinism_ok=_strict_bool(raw, "determinism_ok"),
        kill_drill_ok=_strict_bool(raw, "kill_drill_ok"),
        details=details,
    )
    if raw.get("defect_ledger_path"):
        ctx.defect_ledger_path = Path(str(raw["defect_ledger_path"]))
    if raw.get("options_defect_ledger_root"):
        ctx.options_defect_ledger_root = Path(str(raw["options_defect_ledger_root"]))
    return ctx


def _decision_summary(decision: dict) -> dict:
    keep = ("armed", "allowed", "reason", "failed", "missing", "extra", "non_blocking_required")
    return {k: decision[k] for k in keep if k in decision}


def _attempt_recovery_via_rearm(mid: str, obs: dict, *, actor: str, ts: Optional[str]) -> tuple[bool, dict]:
    from autonomy import audit
    from lifecycle_orchestrator.src import rearm

    try:
        decision = rearm.attempt_rearm(mid, _gate_context_from_observation(obs), actor=actor, ts=ts)
    except Exception as exc:  # noqa: BLE001 - live recovery must fail closed on any gate/audit error.
        reason = f"rearm gate error: {exc}"
        try:
            audit.append(audit.AUTO_ARM_REFUSED, {"reason": reason}, model_id=mid, ts=ts)
        except Exception:  # noqa: BLE001
            pass
        return False, {"armed": False, "allowed": False, "reason": reason, "failed": ["rearm_gate_error"]}

    rec = lifecycle.get_record(mid)
    if decision.get("armed") is True and rec is not None and rec.current_state == lifecycle.LIVE:
        return True, decision
    if decision.get("armed") is True:
        decision = {**decision, "reason": "rearm gate allowed but did not transition model to LIVE"}
    return False, decision


def run_eval(observations: dict, *, actor: str = "decay_driver", ts: Optional[str] = None) -> dict:
    from autonomy import audit, breaker, config as acfg

    reg = lifecycle.load_registry()
    out = {"evaluated": 0, "flagged": 0, "demoted": 0, "recovered": 0, "actions": []}

    for mid, rec in reg.items():
        if rec.current_state not in (lifecycle.LIVE, lifecycle.DEGRADED):
            continue
        env = lifecycle.load_envelope_snapshot(rec.current_envelope_id) if rec.current_envelope_id else None
        obs = observations.get(mid)
        if env is None or obs is None:
            continue
        out["evaluated"] += 1
        r = decay_detector.evaluate(env, obs)

        # Always record the latest read (drives the submit gate).
        lifecycle.annotate(mid, {"last_revalidation": {
            "model_state": r.model_state, "route": r.route,
            "triggers": [t.get("name") for t in r.triggers], "ts": ts or lifecycle.now_iso(),
        }}, reason=f"decay eval {r.model_state}", actor=actor, ts=ts)

        demote_enabled = acfg.action_enabled("demote", model_id=mid)

        if not r.demote:
            # Recovery to any live/armed state must pass through rearm.py's
            # G0-G8 gate chain, including lane capability/profile checks.
            if rec.current_state == lifecycle.DEGRADED and r.model_state == "GREEN":
                recovered, decision = _attempt_recovery_via_rearm(mid, obs, actor=actor, ts=ts)
                if recovered:
                    out["recovered"] += 1
                    out["actions"].append({"model_id": mid, "action": "recover",
                                           **_decision_summary(decision)})
                else:
                    out["actions"].append({"model_id": mid, "action": "recover_refused",
                                           **_decision_summary(decision)})
            continue

        out["flagged"] += 1
        audit.append(audit.DEGRADATION_DETECTED,
                     {"model_state": r.model_state, "route": r.route, "reason": r.reason},
                     model_id=mid, ts=ts)

        if not demote_enabled:
            # detection-only: annotated (submit gate already enforces), no state move
            out["actions"].append({"model_id": mid, "action": "flag_only", "route": r.route})
            continue

        if rec.current_state == lifecycle.LIVE:
            lifecycle.apply_transition(mid, lifecycle.DEGRADED, trigger="decay", reason=r.reason,
                                       actor=actor, route=r.route,
                                       record_updates={"demotion": {"reason": r.reason, "from_state": "LIVE"}}, ts=ts)
        else:  # already DEGRADED -> move to the route target (or quarantine for infra/None)
            target = r.target_state or lifecycle.QUARANTINED
            lifecycle.apply_transition(mid, target, trigger="route", reason=r.reason,
                                       actor=actor, route=r.route, ts=ts)
        breaker.record_demotion(mid, ts=ts)
        audit.append(audit.AUTO_DEMOTE, {"route": r.route, "to": r.target_state}, model_id=mid, ts=ts)
        out["demoted"] += 1
        out["actions"].append({"model_id": mid, "action": "demote", "route": r.route})

    return out


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(prog="run_lifecycle_eval")
    p.add_argument("--observations", required=True, help="path to {model_id: observation} JSON")
    args = p.parse_args(argv)
    with open(args.observations, "r", encoding="utf-8") as fh:
        obs = json.load(fh)
    print(json.dumps(run_eval(obs), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
