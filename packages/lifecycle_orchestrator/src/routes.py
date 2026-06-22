"""Route handlers — one per demotion route. They materialize + ENQUEUE work.

Each handler maps a degraded model's route to the existing entrypoint and queues
a tracked job (heavy jobs -> CHI404). Route manifests are persisted under
``runtime/lifecycle/jobs/manifests/`` before enqueue.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from model_metrics import lifecycle

from . import job_runner, quarantine_bridge

_DEFAULT_BAND = 6.255764

_ROUTE_MANIFEST_KIND = {
    lifecycle.ROUTE_REGIME_SHIFT: "archive_pause",
    lifecycle.ROUTE_PARAM_TWEAK: "gauntlet_retest",
    lifecycle.ROUTE_HYPOTHESIS_TWEAK: "screening_retest",
    lifecycle.ROUTE_EDGE_GONE: "retire_recommendation",
}


def _run_event_universe() -> str:
    return str(lifecycle._repo_root() / "scripts" / "run_event_universe.py")


def _materialize_rescreen(record, step: str) -> dict:
    """Build a runnable run_event_universe command if cell metadata is present."""
    mid = record.model_lifecycle_id
    cell = (record.research_card_links or {}).get("cell")
    if not cell or "hyp_id" not in cell or "event_type" not in cell:
        return {"step": step, "entry": _run_event_universe(),
                "args": ["--from-stage-a", "<bridge_stub>"], "note": "no cell metadata; not materialized"}
    stub = quarantine_bridge.materialize_param_candidate(
        hyp_id=cell["hyp_id"], event_type=cell["event_type"],
        band_ms=cell.get("band_ms", _DEFAULT_BAND), params=cell.get("params", {}),
        slug=cell.get("slug", mid.lower()),
    )
    stub_path = lifecycle.lifecycle_dir() / "param_sweep" / f"{mid}.json"
    quarantine_bridge.write_bridge_manifest(stub_path, stub)
    return {"step": step, "entry": _run_event_universe(),
            "args": ["--from-stage-a", str(stub_path)], "stub": str(stub_path)}


def _latest_transition_hash(model_id: str) -> Optional[str]:
    path = lifecycle.transitions_path()
    if not path.is_file():
        return None
    last_hash = None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if isinstance(rec, dict) and rec.get("model_lifecycle_id") == model_id:
                last_hash = rec.get("self_hash")
    except (OSError, json.JSONDecodeError):
        return None
    return last_hash


def _source_evidence(record) -> dict[str, Any]:
    links = dict(record.research_card_links or {})
    reval = getattr(record, "last_revalidation", None) or {}
    if isinstance(reval, dict) and reval:
        links["last_revalidation"] = reval
    if record.current_envelope_id:
        links["envelope_id"] = record.current_envelope_id
    demotion = getattr(record, "demotion", None) or {}
    if isinstance(demotion, dict) and demotion:
        links["demotion"] = demotion
    return links


def _artifact_evidence_path(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return bool(value)


def _has_artifact_evidence(links: dict[str, Any]) -> bool:
    artifact_keys = (
        "vectorbt", "vectorbt_results", "vectorbt_artifact", "paid_screen",
        "hftbacktest", "hbt", "hbt_observation", "replay_observation", "session_report", "session",
    )
    return any(_artifact_evidence_path(links.get(k)) for k in artifact_keys)


def build_route_manifest(
    record,
    *,
    reason: str,
    created_by: str,
    created_at: Optional[str] = None,
) -> dict:
    route = (record.reentry_routing or {}).get("route")
    if route not in lifecycle.ROUTES:
        raise ValueError(f"unknown or missing route for {record.model_lifecycle_id}")
    evidence = _source_evidence(record)
    if not evidence or not _has_artifact_evidence(evidence):
        raise ValueError("missing source evidence")
    stamp = created_at or lifecycle.now_iso()
    mid = record.model_lifecycle_id
    body = {
        "model_id": mid,
        "route": route,
        "kind": _ROUTE_MANIFEST_KIND[route],
        "reason": reason,
        "source_transition_hash": _latest_transition_hash(mid),
        "source_evidence": evidence,
        "created_by": created_by,
        "created_at": stamp,
    }
    body["manifest_digest"] = hashlib.sha256(
        json.dumps({k: v for k, v in body.items() if k != "manifest_digest"}, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return body


def create_route_manifest(
    record,
    *,
    reason: str,
    created_by: str,
    created_at: Optional[str] = None,
) -> dict:
    manifest = build_route_manifest(record, reason=reason, created_by=created_by, created_at=created_at)
    existing = job_runner.find_route_manifest(
        str(manifest["model_id"]),
        str(manifest["route"]),
        manifest.get("source_transition_hash"),
    )
    if existing is not None:
        manifest_id = str(existing["manifest_id"])
        path = job_runner.manifests_dir() / f"{manifest_id}.json"
        return {"manifest": existing, "manifest_path": str(path)}
    path, stored = job_runner.write_route_manifest(manifest)
    return {"manifest": stored, "manifest_path": str(path)}


def handle_route(record, *, actor: str = "orchestrator", reason: str = "") -> dict:
    route = (record.reentry_routing or {}).get("route")
    mid = record.model_lifecycle_id
    why = reason or (getattr(record, "demotion", None) or {}).get("reason") or f"route {route}"

    manifest_info = None
    try:
        manifest_info = create_route_manifest(record, reason=why, created_by=actor)
    except (ValueError, FileExistsError) as exc:
        return {"action": "manifest_rejected", "model_id": mid, "job": None, "error": str(exc)}

    if route == lifecycle.ROUTE_REGIME_SHIFT:
        return {"action": "regime_watch", "model_id": mid, "job": None,
                "manifest": manifest_info["manifest"],
                "note": "auto re-arm when regime re-enters approved set"}

    if route == lifecycle.ROUTE_PARAM_TWEAK:
        cmd = _materialize_rescreen(record, "param_rescreen")
        jid = job_runner.enqueue(mid, route, cmd, host="chi404")
        return {"action": "param_rescreen", "model_id": mid, "job": jid,
                "manifest": manifest_info["manifest"],
                "materialized": "<bridge_stub>" not in cmd["args"]}

    if route == lifecycle.ROUTE_HYPOTHESIS_TWEAK:
        cmd = {"step": "f3_propose", "entry": "python -m llm_slow_tier hypothesis-intake",
               "note": "F3 proposes a variant -> verifier -> scratch registry -> gauntlet"}
        jid = job_runner.enqueue(mid, route, cmd, host="laptop")
        return {"action": "hypothesis_propose", "model_id": mid, "job": jid,
                "manifest": manifest_info["manifest"]}

    if route == lifecycle.ROUTE_EDGE_GONE:
        cmd = _materialize_rescreen(record, "revalidate")
        jid = job_runner.enqueue(mid, route, cmd, host="chi404")
        return {"action": "revalidate", "model_id": mid, "job": jid,
                "manifest": manifest_info["manifest"],
                "materialized": "<bridge_stub>" not in cmd["args"]}

    return {"action": "noop", "model_id": mid, "job": None}
