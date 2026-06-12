"""Control plane — local-origin job triggers (read-mostly in W1).

Every endpoint requires the ``control`` scope (local origin, no proxy — see
auth.require_control) and every call is appended to an audit log. Job execution
(rebuild / re-screen / roll / restart) is *gated off* unless
``COCKPIT_CONTROL_EXEC=1`` so the dashboard can never launch a heavy/irreversible
job by accident; W4 wires the actual tracked-subprocess + log streaming. The
Databento *preflight* is fully live because it is read-only (cost estimate, no
download). No endpoint here ever places an order or flips EXECUTION_MODE.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import paths
from .auth import require_control

router = APIRouter(prefix="/api/control", tags=["control"])

# Allow-list of named jobs the dashboard may trigger (each maps to an existing CLI).
JOBS = {
    "feature_rebuild": "rebuild feature store (PC6 chain)",
    "rescreen_stage_a": "re-run Stage A screen",
    "roll_now": "force contract roll on capture daemon",
    "capture_restart": "restart hft3-capture service",
    "slowtier_run": "run slow-tier nightly now",
}


def _exec_enabled() -> bool:
    return os.environ.get("COCKPIT_CONTROL_EXEC", "") == "1"


def _audit(action: str, params: dict, result: dict) -> None:
    rec = {"ts": paths.now_iso(), "action": action, "params": params, "result": result}
    path = paths.CONTROL_AUDIT_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def _audit_tail(n: int = 20) -> list[dict]:
    txt = paths.read_text(paths.CONTROL_AUDIT_LOG)
    if not txt:
        return []
    out = []
    for line in txt.strip().splitlines()[-n:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


@router.get("/status")
def status(_: str = Depends(require_control)) -> dict:
    return {
        "exec_enabled": _exec_enabled(),
        "execution_mode": paths.execution_mode(),
        "jobs": JOBS,
        "recent_audit": _audit_tail(),
        "note": "control is local-origin only; job execution gated by COCKPIT_CONTROL_EXEC=1",
    }


class JobRequest(BaseModel):
    name: str
    confirm: bool = False
    params: dict = {}


@router.post("/job")
def trigger_job(req: JobRequest, _: str = Depends(require_control)) -> dict:
    if req.name not in JOBS:
        raise HTTPException(400, f"unknown job '{req.name}'")
    if not req.confirm:
        raise HTTPException(400, "confirm=true required")
    executed = False
    note = "recorded only — set COCKPIT_CONTROL_EXEC=1 to enable execution (W4)"
    if _exec_enabled():
        # W4: launch tracked subprocess + stream logs. Intentionally not wired in W1.
        note = "exec enabled but launcher not yet wired (W4)"
    result = {"executed": executed, "note": note}
    _audit(req.name, req.params, result)
    return {"status": "accepted", "job": req.name, **result}


@router.post("/autonomy/stop")
def autonomy_stop(_: str = Depends(require_control)) -> dict:
    """Emergency stop of the autonomy LOOP (not the trading book). Always allowed
    (even with COCKPIT_CONTROL_EXEC off) — a STOP is never gated. Trips the
    circuit breaker, which persists and can only be cleared by a human."""
    try:
        from autonomy import breaker

        breaker.trip("cockpit emergency stop")
    except Exception as exc:
        raise HTTPException(502, f"autonomy stop failed: {exc}")
    _audit("autonomy_stop", {}, {"frozen": True})
    return {"status": "stopped", "frozen": True}


class UnfreezeRequest(BaseModel):
    confirm: bool = False
    operator: str = "cockpit"


@router.post("/autonomy/unfreeze")
def autonomy_unfreeze(req: UnfreezeRequest, _: str = Depends(require_control)) -> dict:
    """Clear the breaker (re-enable autonomy). LOOSENS safety => gated + confirm."""
    if not req.confirm:
        raise HTTPException(400, "confirm=true required")
    if not _exec_enabled():
        raise HTTPException(403, "unfreeze requires COCKPIT_CONTROL_EXEC=1")
    try:
        from autonomy import breaker

        breaker.clear(operator=req.operator)
    except Exception as exc:
        raise HTTPException(502, f"unfreeze failed: {exc}")
    _audit("autonomy_unfreeze", {"operator": req.operator}, {"frozen": False})
    return {"status": "unfrozen", "operator": req.operator}


class PreflightRequest(BaseModel):
    symbols: list[str]
    start_utc: str
    end_utc: str
    dataset: str = "GLBX.MDP3"
    schema_: str = "mbo"
    stype_in: str = "continuous"


@router.post("/databento/preflight")
def databento_preflight(req: PreflightRequest, _: str = Depends(require_control)) -> dict:
    """Read-only cost estimate. Never downloads."""
    try:
        from datetime import datetime

        from data_system.src.databento_client import DatabentoResearchClient  # type: ignore

        client = DatabentoResearchClient()
        cost = client.estimate_cost(
            req.symbols,
            datetime.fromisoformat(req.start_utc.replace("Z", "+00:00")),
            datetime.fromisoformat(req.end_utc.replace("Z", "+00:00")),
            dataset=req.dataset,
            schema=req.schema_,
            stype_in=req.stype_in,
        )
        result = {"cost_usd": float(cost), "symbols": req.symbols}
    except Exception as exc:
        raise HTTPException(502, f"preflight failed: {exc}")
    _audit("databento_preflight", req.model_dump(), result)
    return result
