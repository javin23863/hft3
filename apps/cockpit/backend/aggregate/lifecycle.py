"""Lifecycle zone — per-model state-machine view.

Reads the materialized lifecycle registry (runtime/lifecycle/model_lifecycle.json)
that packages/model_metrics/lifecycle.py writes. Read-only + defensive, like every
other cockpit zone — the registry is the single source of truth, the cockpit never
writes it.
"""
from __future__ import annotations

from .. import paths, schemas

# State -> dot status for the UI.
_STATE_DOT = {
    "CANDIDATE": schemas.UNKNOWN,
    "SCREENING": schemas.RUNNING,
    "GAUNTLET": schemas.RUNNING,
    "CERTIFIED": schemas.OK,
    "SHADOW": schemas.RUNNING,
    "LIVE": schemas.OK,
    "DEGRADED": schemas.STALE,
    "QUARANTINED": schemas.FAIL,
    "ARCHIVED_PAUSED": schemas.STALE,
    "RETIRED": schemas.MISSING,
}

_FUNNEL_ORDER = [
    "CANDIDATE", "SCREENING", "GAUNTLET", "CERTIFIED", "SHADOW", "LIVE",
    "DEGRADED", "ARCHIVED_PAUSED", "QUARANTINED", "RETIRED",
]


def build() -> dict:
    data = paths.read_json(paths.MODEL_LIFECYCLE)
    models = (data or {}).get("models", {}) if isinstance(data, dict) else {}

    rows = []
    funnel = {s: 0 for s in _FUNNEL_ORDER}
    for mid, rec in sorted(models.items()):
        state = rec.get("current_state", "UNKNOWN")
        funnel[state] = funnel.get(state, 0) + 1
        routing = rec.get("reentry_routing") or {}
        demotion = rec.get("demotion") or {}
        reval = rec.get("last_revalidation") or {}
        rows.append({
            "id": mid,
            "hypothesis_id": rec.get("hypothesis_id"),
            "symbol": rec.get("symbol"),
            "state": state,
            "dot": _STATE_DOT.get(state, schemas.UNKNOWN),
            "since": rec.get("current_state_since"),
            "route": routing.get("route"),
            "demotion_reason": demotion.get("reason"),
            "last_revalidation": reval.get("model_state"),
            "envelope_id": rec.get("current_envelope_id"),
        })

    quarantined = funnel.get("QUARANTINED", 0)
    degraded = funnel.get("DEGRADED", 0) + funnel.get("ARCHIVED_PAUSED", 0)
    if quarantined:
        health = schemas.RED
    elif degraded:
        health = schemas.AMBER
    else:
        health = schemas.GREEN

    return {
        "zone": "lifecycle",
        "generated_utc": paths.now_iso(),
        "health": health,
        "total_models": len(models),
        "funnel": funnel,
        "live": funnel.get("LIVE", 0),
        "rows": rows,
        "registered": bool(models),
        "note": None if models else "no models registered yet (runtime/lifecycle/model_lifecycle.json absent)",
    }
