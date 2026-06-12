"""Autonomy zone — master-switch, circuit breaker, gate state, audit tail.

Surfaces the safety rails so a human can see (and stop) what the autonomous loop
is doing. Read-only. Imports the autonomy package lazily so the cockpit degrades
gracefully if it is unavailable.
"""
from __future__ import annotations

from .. import paths, schemas

_JOB_STATES = ("pending", "running", "done", "failed")


def build() -> dict:
    try:
        from autonomy.status import snapshot
        snap = snapshot()
    except Exception as exc:  # autonomy package missing/unreadable
        return {
            "zone": "autonomy", "generated_utc": paths.now_iso(), "health": schemas.AMBER,
            "available": False, "error": str(exc),
        }

    try:
        from lifecycle_orchestrator.src import job_runner
        jobs = {st: len(job_runner.list_jobs(st)) for st in _JOB_STATES}
    except Exception:
        jobs = {}

    breaker = snap.get("breaker", {}) or {}
    frozen = bool(breaker.get("frozen"))
    chain_ok = bool(snap.get("audit_chain_ok", True))
    master = bool(snap.get("master_enabled"))

    if frozen or not chain_ok:
        health = schemas.RED
    elif master:
        health = schemas.AMBER  # autonomy is live — worth a human's eye
    else:
        health = schemas.GREEN

    return {
        "zone": "autonomy",
        "generated_utc": paths.now_iso(),
        "health": health,
        "available": True,
        "master_enabled": master,
        "env_enabled": snap.get("env_enabled"),
        "file_enabled": snap.get("file_enabled"),
        "kill_engaged": snap.get("kill_engaged"),
        "can_arm_live": snap.get("can_arm_live"),
        "actions": snap.get("actions", {}),
        "rearm_allow_live": snap.get("rearm_allow_live"),
        "breaker": {"frozen": frozen, "reason": breaker.get("freeze_reason", ""),
                     "frozen_at": breaker.get("frozen_at", "")},
        "audit_chain_ok": chain_ok,
        "jobs": jobs,
        "recent_audit": snap.get("recent_audit", []),
    }
