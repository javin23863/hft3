"""Top-level orchestrator scan — master-enable-gated, audited, fail-closed.

A scan reads the lifecycle registry and, for each DEGRADED model whose route is
permitted, dispatches its route handler (which enqueues the work). The FIRST
thing it does is check the autonomy master enable; if disabled (the default) it
audits MASTER_DISABLED and does nothing. Heavy work runs out-of-band via the
job queue, so a scan is cheap and resumable.
"""
from __future__ import annotations

import argparse
import json
from typing import Optional

from autonomy import audit, config as acfg
from model_metrics import lifecycle

from . import routes


def scan(*, actor: str = "orchestrator", ts: Optional[str] = None) -> dict:
    if not acfg.master_enabled():
        audit.append(audit.MASTER_DISABLED, {"reason": "autonomy master enable is OFF"}, ts=ts)
        return {"disabled": True, "scanned": 0, "dispatched": []}

    registry = lifecycle.load_registry()
    degraded = [r for r in registry.values() if r.current_state == lifecycle.DEGRADED]
    dispatched = []
    for rec in degraded:
        if not acfg.action_enabled("retest", model_id=rec.model_lifecycle_id):
            continue
        plan = routes.handle_route(rec, actor=actor, reason=f"orchestrator scan for {rec.model_lifecycle_id}")
        audit.append(audit.RETEST_START, plan, model_id=rec.model_lifecycle_id, ts=ts)
        dispatched.append(plan)
    return {"disabled": False, "scanned": len(degraded), "dispatched": dispatched}


def status() -> dict:
    from autonomy.status import snapshot

    snap = snapshot()
    registry = lifecycle.load_registry()
    states: dict[str, int] = {}
    for rec in registry.values():
        states[rec.current_state] = states.get(rec.current_state, 0) + 1
    return {"autonomy": snap, "lifecycle_states": states, "total_models": len(registry)}


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(prog="lifecycle_orchestrator")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan")
    sub.add_parser("status")
    args = p.parse_args(argv)
    if args.cmd == "scan":
        print(json.dumps(scan(), indent=2))
    elif args.cmd == "status":
        print(json.dumps(status(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
