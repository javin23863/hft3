"""Route handlers — one per demotion route. They materialize + ENQUEUE work.

Each handler maps a degraded model's route to the existing entrypoint and queues
a tracked job (heavy jobs -> CHI404). When the model record carries cell metadata
(recorded at certify), the param/edge routes materialize a REAL stage-A stub and
a fully-formed command the worker can run; otherwise they enqueue a record-only
job flagged not-materialized. Regime-shift queues nothing: the decay detector
auto-re-arms when the regime returns.
"""
from __future__ import annotations

from model_metrics import lifecycle

from . import job_runner, quarantine_bridge

_DEFAULT_BAND = 6.255764


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


def handle_route(record) -> dict:
    route = (record.reentry_routing or {}).get("route")
    mid = record.model_lifecycle_id

    if route == lifecycle.ROUTE_REGIME_SHIFT:
        return {"action": "regime_watch", "model_id": mid, "job": None,
                "note": "auto re-arm when regime re-enters approved set"}

    if route == lifecycle.ROUTE_PARAM_TWEAK:
        cmd = _materialize_rescreen(record, "param_rescreen")
        jid = job_runner.enqueue(mid, route, cmd, host="chi404")
        return {"action": "param_rescreen", "model_id": mid, "job": jid,
                "materialized": "<bridge_stub>" not in cmd["args"]}

    if route == lifecycle.ROUTE_HYPOTHESIS_TWEAK:
        cmd = {"step": "f3_propose", "entry": "python -m llm_slow_tier hypothesis-intake",
               "note": "F3 proposes a variant -> verifier -> scratch registry -> gauntlet"}
        jid = job_runner.enqueue(mid, route, cmd, host="laptop")
        return {"action": "hypothesis_propose", "model_id": mid, "job": jid}

    if route == lifecycle.ROUTE_EDGE_GONE:
        cmd = _materialize_rescreen(record, "revalidate")
        jid = job_runner.enqueue(mid, route, cmd, host="chi404")
        return {"action": "revalidate", "model_id": mid, "job": jid,
                "materialized": "<bridge_stub>" not in cmd["args"]}

    return {"action": "noop", "model_id": mid, "job": None}
