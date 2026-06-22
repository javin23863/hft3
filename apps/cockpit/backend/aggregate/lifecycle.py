"""Lifecycle zone — per-model state-machine view.

Reads the materialized lifecycle registry (runtime/lifecycle/model_lifecycle.json)
that packages/model_metrics/lifecycle.py writes. Read-only + defensive, like every
other cockpit zone — the registry is the single source of truth, the cockpit never
writes it.

Research prefilter is VectorBT paid screen on Vast; lifecycle SCREENING/GAUNTLET
states map to screening artifacts and robust gates before LIVE eligibility.
Submit-gate enforcement (degraded states) is in trade_manager.risk_layer, separate
from trading kill-switch and autonomy global kill.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from model_metrics import lifecycle as lc

from .. import paths, schemas

_TRADE_STATES = (lc.LIVE, lc.DEGRADED)

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

_NEXT_REQUIRED_GATE = {
    "CANDIDATE": "screening",
    "SCREENING": "gauntlet",
    "GAUNTLET": "certification",
    "CERTIFIED": "shadow",
    "SHADOW": "rearm G0-G8",
    "LIVE": None,
    "DEGRADED": "rearm G0-G8",
    "QUARANTINED": "operator review",
    "ARCHIVED_PAUSED": "rearm G0-G8",
    "RETIRED": None,
}

_LAST_TRANSITION_KEYS = (
    "ts", "from_state", "to_state", "trigger", "reason", "route", "actor",
)


def _load_registry() -> tuple[dict[str, dict], bool, bool, Optional[str]]:
    """Return (models, registered, malformed, note)."""
    path = paths.MODEL_LIFECYCLE
    if not path.is_file():
        return {}, False, False, None
    raw = paths.read_json(path)
    if raw is None:
        return {}, False, True, f"lifecycle registry at {path} is malformed or unparseable"
    if not isinstance(raw, dict):
        return {}, False, True, f"lifecycle registry at {path} has invalid shape (expected object)"
    models = raw.get("models")
    if not isinstance(models, dict):
        return {}, False, True, f"lifecycle registry at {path} has invalid models shape"
    if not models:
        return {}, False, False, "no models registered yet (registry empty)"
    return models, True, False, None


def _ts_key(raw: Any) -> str:
    if not raw or not isinstance(raw, str):
        return ""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return raw


def _parse_transitions() -> tuple[dict[str, dict[str, Any]], bool]:
    """Per-model last transition + count; True if any jsonl line was bad."""
    counts: dict[str, int] = {}
    last: dict[str, dict] = {}
    bad = False
    path = paths.LIFECYCLE_TRANSITIONS
    if not path.is_file():
        return {}, False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}, False
    for line in lines:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            bad = True
            continue
        if not isinstance(rec, dict):
            bad = True
            continue
        mid = rec.get("model_lifecycle_id")
        if not mid:
            bad = True
            continue
        counts[mid] = counts.get(mid, 0) + 1
        prev = last.get(mid)
        if prev is None:
            last[mid] = rec
            continue
        prev_ts = _ts_key(prev.get("ts"))
        cur_ts = _ts_key(rec.get("ts"))
        if cur_ts >= prev_ts:
            last[mid] = rec
    out: dict[str, dict[str, Any]] = {}
    for mid, cnt in counts.items():
        rec = last[mid]
        out[mid] = {
            "last_transition": {k: rec.get(k) for k in _LAST_TRANSITION_KEYS},
            "transition_count": cnt,
        }
    return out, bad


def _next_required_gate(state: str) -> Optional[str]:
    if state in _NEXT_REQUIRED_GATE:
        return _NEXT_REQUIRED_GATE[state]
    return "unknown"


def _rel(path: Path) -> str:
    try:
        return path.relative_to(paths.REPO).as_posix()
    except ValueError:
        return path.as_posix()


def _safe_envelope_rel(envelope_id: str | None) -> str | None:
    if not envelope_id or not isinstance(envelope_id, str):
        return None
    eid = envelope_id.strip()
    if not eid or ".." in eid or "/" in eid or "\\" in eid:
        return None
    root = paths.MODEL_LIFECYCLE.parent.resolve()
    candidate = (root / "envelopes" / f"{eid}.json").resolve()
    if root not in candidate.parents and candidate != root:
        return None
    if not candidate.is_file():
        return None
    return _rel(candidate)


def _row_dot(state: str, model_state: str | None, *, has_revalidation: bool) -> str:
    if state in ("QUARANTINED", "RETIRED"):
        return _STATE_DOT[state]
    if state in _TRADE_STATES:
        if not has_revalidation:
            return schemas.STALE
        if model_state == "RED":
            return schemas.FAIL
        if model_state == "YELLOW":
            return schemas.STALE
        if model_state == "GREEN":
            return schemas.OK if state == lc.LIVE else schemas.STALE
    return _STATE_DOT.get(state, schemas.UNKNOWN)


def _evidence_links(rec: dict) -> dict:
    out: dict[str, Any] = {}
    rc = rec.get("research_card_links") or {}
    if rc:
        string_links = {str(k): v for k, v in rc.items() if isinstance(v, str)}
        if string_links:
            out["research_card_links"] = string_links
    gov = rec.get("governance_links") or {}
    if gov:
        out["governance_links"] = gov
    eid = rec.get("current_envelope_id")
    safe = _safe_envelope_rel(eid if isinstance(eid, str) else None)
    if safe:
        out["envelope"] = safe
    return out


def _submit_fields(rec: dict) -> tuple[bool, float, str]:
    """Submit decision from the already-loaded registry row (same policy as submit_gate)."""
    state = rec.get("current_state", "")
    reval = rec.get("last_revalidation") or {}
    model_state = reval.get("model_state")
    if state in _TRADE_STATES and not model_state:
        return False, 0.0, "stale_no_revalidation"
    gate_state = model_state if model_state is not None else "GREEN"
    try:
        from model_metrics.decay_detector import submit_allowed

        allowed, size = submit_allowed(state, gate_state)
        return allowed, size, f"lifecycle={state} model_state={gate_state}"
    except Exception as exc:
        logging.warning("submit_gate lookup failed for lifecycle row: %s", exc)
        return False, 0.0, "submit_gate_error"


def _build_row(mid: str, rec: dict, tx: dict[str, dict[str, Any]]) -> dict:
    state = rec.get("current_state", "UNKNOWN")
    routing = rec.get("reentry_routing") or {}
    demotion = rec.get("demotion") or {}
    reval = rec.get("last_revalidation") or {}
    model_state = reval.get("model_state")
    has_reval = bool(reval.get("model_state"))
    allowed, size, reason = _submit_fields(rec)
    tx_info = tx.get(mid, {})
    return {
        "id": mid,
        "hypothesis_id": rec.get("hypothesis_id"),
        "symbol": rec.get("symbol"),
        "state": state,
        "dot": _row_dot(state, model_state, has_revalidation=has_reval),
        "since": rec.get("current_state_since"),
        "route": routing.get("route"),
        "demotion_reason": demotion.get("reason"),
        # last_revalidation: legacy alias (model_state string only); prefer latest_model_state in new UI.
        "last_revalidation": reval.get("model_state"),
        "envelope_id": rec.get("current_envelope_id"),
        "submit_allowed": allowed,
        "submit_size_factor": size,
        "submit_reason": reason,
        "latest_model_state": reval.get("model_state"),
        "latest_revalidation_ts": reval.get("ts"),
        "latest_revalidation_triggers": reval.get("triggers") or [],
        "route_decided_at": routing.get("decided_at"),
        "next_required_gate": _next_required_gate(state),
        "evidence_links": _evidence_links(rec),
        "last_transition": tx_info.get("last_transition"),
        "transition_count": tx_info.get("transition_count", 0),
    }


def row_for_hypothesis(hyp_id: int) -> dict:
    """Lifecycle snapshot for one hypothesis id (read-only)."""
    models, registered, malformed, note = _load_registry()
    if malformed:
        return {"tracked": False, "status": "malformed", "note": note}
    if not registered:
        return {"tracked": False, "status": "untracked"}
    tx, _tx_bad = _parse_transitions()
    for mid, rec in models.items():
        if rec.get("hypothesis_id") == hyp_id:
            row = _build_row(mid, rec, tx)
            row["tracked"] = True
            row["status"] = "tracked"
            return row
    return {"tracked": False, "status": "untracked"}


def build() -> dict:
    models, registered, malformed, reg_note = _load_registry()
    tx, tx_bad = _parse_transitions()

    rows = []
    funnel = {s: 0 for s in _FUNNEL_ORDER}
    if registered:
        for mid, rec in sorted(models.items()):
            state = rec.get("current_state", "UNKNOWN")
            funnel[state] = funnel.get(state, 0) + 1
            rows.append(_build_row(mid, rec, tx))

    blocked_count = sum(1 for r in rows if not r["submit_allowed"])
    degraded_count = funnel.get("DEGRADED", 0)
    needs_rearm_count = (
        funnel.get("SHADOW", 0) + funnel.get("DEGRADED", 0) + funnel.get("ARCHIVED_PAUSED", 0)
    )
    needs_retest_count = funnel.get("SCREENING", 0) + funnel.get("GAUNTLET", 0)
    retired_count = funnel.get("RETIRED", 0)

    if malformed:
        health = schemas.RED
    elif funnel.get("QUARANTINED", 0):
        health = schemas.RED
    elif any(
        r["state"] in _TRADE_STATES
        and (
            r.get("latest_model_state") not in (None, "GREEN")
            or not r.get("submit_allowed")
            or r.get("submit_size_factor", 1.0) < 1.0
        )
        for r in rows
    ):
        health = schemas.AMBER
    elif funnel.get("DEGRADED", 0) + funnel.get("ARCHIVED_PAUSED", 0):
        health = schemas.AMBER
    else:
        health = schemas.GREEN

    if malformed:
        note = reg_note
    elif registered:
        note = None
    elif reg_note:
        note = reg_note
    else:
        note = "no models registered yet (runtime/lifecycle/model_lifecycle.json absent)"

    out = {
        "zone": "lifecycle",
        "generated_utc": paths.now_iso(),
        "health": health,
        "total_models": len(models) if registered else 0,
        "funnel": funnel,
        "live": funnel.get("LIVE", 0),
        "rows": rows,
        "registered": registered,
        "note": note,
        "blocked_count": blocked_count,
        "degraded_count": degraded_count,
        "needs_rearm_count": needs_rearm_count,
        "needs_retest_count": needs_retest_count,
        "retired_count": retired_count,
    }
    if tx_bad:
        out["transition_log_warning"] = "one or more transition log lines were skipped (malformed)"
    return out
