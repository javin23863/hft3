"""Decay driver — the trigger that was missing.

For every LIVE/DEGRADED model it loads the FROZEN certified envelope, evaluates a
fresh observation against it (re-validation backtest now; the live stream later),
and:
  * ALWAYS records the result onto the record (``last_revalidation``) — this is
    what the submit gate reads, so a RED read flattens the model's orders even
    with autonomy off (detection is always on; it only annotates).
  * AUTO-DEMOTES (moves the lifecycle state) ONLY when autonomy ``demote`` is
    enabled — LIVE->DEGRADED, then DEGRADED->route-target; recovers DEGRADED->LIVE
    on a clean GREEN read. Every demotion is audited + counted by the breaker.

Observations are supplied as ``{model_id: observation_dict}`` (a re-validation
producer builds these; the driver itself is pure orchestration).
"""
from __future__ import annotations

import argparse
import json
from typing import Optional

from model_metrics import decay_detector, lifecycle


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
            # recovery: a clean GREEN read returns a DEGRADED model to LIVE (gated)
            if rec.current_state == lifecycle.DEGRADED and r.model_state == "GREEN" and demote_enabled:
                lifecycle.apply_transition(mid, lifecycle.LIVE, trigger="recover",
                                           reason="back within envelope", actor=actor, ts=ts)
                out["recovered"] += 1
                out["actions"].append({"model_id": mid, "action": "recover"})
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
