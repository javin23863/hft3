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
import re
import subprocess
import sys
from pathlib import Path
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

# W4 launcher: the cockpit ENQUEUES into the existing durable job queue
# (packages/lifecycle_orchestrator.src.job_runner) and, when exec is enabled,
# kicks the existing worker to drain it. We reuse the queue's enqueue lock, state
# machine (pending/running/done/failed), CHI404 host-gating, and artifact capture
# instead of spawning our own ad-hoc process. Each allow-listed job maps to a
# FIXED command {entry, args} the worker runs as [python, entry, *args] for a .py
# entry or [entry, *args] otherwise — never built from request params.
# The capture daemon auto-rolls on its CT calendar and exposes no force-roll IPC,
# so roll_now restarts the service to re-resolve the front month (same command as
# capture_restart, distinct audit intent).
def _job_cmd() -> dict:
    s = paths.REPO / "scripts"
    return {
        "feature_rebuild":  {"host": "laptop",
                             "command": {"entry": str(s / "build_feature_store.py"), "args": ["--rebuild"]}},
        "rescreen_stage_a": {"host": "laptop",
                              "command": {"entry": str(s / "run_stage_a_screen.py"), "args": []}},
        "slowtier_run":     {"host": "laptop",
                              "command": {"entry": "powershell",
                                          "args": ["-NoProfile", "-ExecutionPolicy", "Bypass",
                                                  "-File", str(s / "slow_tier_nightly.ps1")]}},
        # The capture daemon auto-rolls (intraday live ROLL_ADD/DROP + ROLL_RESTART
        # self-exit) and handles only SIGTERM/SIGINT — no external force-roll or
        # reload IPC. The one manual lever is a service restart (the daemon
        # re-resolves the front month on startup), so roll_now and capture_restart
        # share that single mechanism with distinct intent, and BOTH briefly gap
        # live capture -> flagged disruptive (require explicit ack to fire).
        "roll_now":         {"host": "chi404", "disruptive": True,
                             "command": {"entry": "systemctl", "args": ["restart", "hft3-capture"],
                                         "note": "force front-month re-resolution via service restart "
                                                 "(no force-roll IPC; brief capture gap)"}},
        "capture_restart":  {"host": "chi404", "disruptive": True,
                             "command": {"entry": "systemctl", "args": ["restart", "hft3-capture"],
                                         "note": "restart hft3-capture service (brief capture gap)"}},
    }


def _exec_enabled() -> bool:
    return os.environ.get("COCKPIT_CONTROL_EXEC", "") == "1"


_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _worker_env() -> dict:
    """Env for the kicked worker so its child jobs get the shim PYTHONPATH (no
    machine-wide import stall) and resolve the lake (HFT3_NPZ_ROOT/...)."""
    env = dict(os.environ)
    shim = str(Path.home() / ".claude" / "shims")
    env["PYTHONPATH"] = os.pathsep.join([shim, str(paths.REPO), str(paths.REPO / "packages")])
    try:
        from data_system.src.npz_resolver import lake_root, npz_root

        env.setdefault("HFT3_NPZ_ROOT", str(npz_root(paths.REPO)))
        env.setdefault("HFT3_FEATURE_ROOT", str(lake_root(paths.REPO) / "features"))
        env.setdefault("HFT3_MANIFEST_PATH", str(lake_root(paths.REPO) / "manifest.parquet"))
    except Exception:
        pass
    env["HFT3_ORCH_EXEC"] = "1"
    env["PYTHONUNBUFFERED"] = "1"  # child jobs flush stdout live -> logfile updates mid-run
    return env


def _kick_worker() -> Optional[int]:
    """Spawn the EXISTING worker (detached, correct env) to drain the queue now.
    Returns its pid, or None if the spawn failed. job_runner.claim() makes
    concurrent kicks safe (a job already claimed is skipped)."""
    try:
        flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        proc = subprocess.Popen(
            [sys.executable, "-m", "lifecycle_orchestrator.src.worker"],
            cwd=str(paths.REPO), env=_worker_env(),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            creationflags=flags,
        )
        return proc.pid
    except Exception:
        return None


def _all_jobs() -> list[dict]:
    """Every queued job across states (state stamped in), via the existing queue."""
    try:
        from lifecycle_orchestrator.src import job_runner
    except Exception:
        return []
    out: list[dict] = []
    for st in ("pending", "running", "done", "failed"):
        for j in job_runner.list_jobs(st):
            j["state"] = st
            out.append(j)
    return out


def _active_job(name: str) -> Optional[dict]:
    for job in _all_jobs():
        if job.get("model_id") == name and job.get("state") in {"pending", "running"}:
            return job
    return None


def _tracked_jobs() -> list[dict]:
    jobs = _all_jobs()
    recent = jobs[-20:]
    active = [job for job in jobs if job.get("state") in {"pending", "running"}]
    combined: list[dict] = []
    seen: set[str] = set()
    for job in [*active, *recent]:
        job_id = str(job.get("job_id") or "")
        if not job_id or job_id in seen:
            continue
        seen.add(job_id)
        combined.append(job)
    return combined


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
        "tracked_jobs": [
            {"job_id": j.get("job_id"), "name": j.get("model_id"),
             "host": j.get("host"), "state": j.get("state")}
            for j in _tracked_jobs()
        ],
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
    spec = _job_cmd()[req.name]
    if spec.get("requires_exec_enabled") and not _exec_enabled():
        raise HTTPException(403, f"'{req.name}' requires COCKPIT_CONTROL_EXEC=1 before enqueue")
    # Disruptive jobs restart the live capture daemon (brief market-data gap):
    # require an explicit acknowledgement so the dashboard can never gap the tape
    # by a casual click.
    if spec.get("disruptive") and req.params.get("ack_capture_gap") is not True:
        raise HTTPException(
            400,
            f"'{req.name}' restarts the live capture daemon (brief market-data gap); "
            "pass params.ack_capture_gap=true to acknowledge and proceed",
        )
    try:
        from lifecycle_orchestrator.src import job_runner
    except Exception as exc:
        raise HTTPException(502, f"job queue unavailable: {exc}")
    try:
        if spec.get("singleton"):
            job_id = job_runner.enqueue_singleton(req.name, "cockpit", spec["command"], host=spec["host"])
        else:
            job_id = job_runner.enqueue(req.name, "cockpit", spec["command"], host=spec["host"])
    except getattr(job_runner, "DuplicateActiveJob", RuntimeError) as exc:
        raise HTTPException(409, str(exc))
    result = {"enqueued": True, "job_id": job_id, "host": spec["host"]}
    if _exec_enabled():
        pid = _kick_worker()
        result["worker_pid"] = pid
        result["note"] = "worker kicked to drain the queue" if pid else "worker kick failed; job stays pending"
        if spec["host"] == "chi404":
            result["note"] += " — CHI404 jobs run on the box (worker with HFT3_ORCH_CHI404=1), not from the laptop"
    else:
        result["note"] = "enqueued only — set COCKPIT_CONTROL_EXEC=1 to drain via the worker"
    _audit(req.name, req.params, result)
    return {"status": "accepted", "job": req.name, **result}


@router.get("/job/{job_id}/logs")
def job_logs(job_id: str, tail: int = 200, _: str = Depends(require_control)) -> dict:
    """A tracked job's state + LIVE output. Tails the per-job logfile the worker
    streams while the job runs (deterministic path, readable mid-run), not just the
    on-completion artifact. Poll this endpoint for a live tail."""
    if not _JOB_ID_RE.match(job_id):
        raise HTTPException(400, "invalid job_id")
    job = next((j for j in _all_jobs() if j.get("job_id") == job_id), None)
    if job is None:
        raise HTTPException(404, f"unknown job '{job_id}'")
    art = job.get("artifacts") or {}
    log_text = ""
    try:
        from lifecycle_orchestrator.src import job_runner

        lp = Path(art.get("logfile") or (job_runner.jobs_dir() / "logs" / f"{job_id}.log"))
        log_text = paths.read_text(lp) or ""
    except Exception:
        log_text = ""
    lines = log_text.splitlines()
    n = max(1, min(tail, 5000))
    return {
        "job_id": job_id, "name": job.get("model_id"), "host": job.get("host"),
        "state": job.get("state"), "command": job.get("command"),
        "executed": art.get("executed"), "returncode": art.get("returncode"),
        "note": art.get("note"), "error": job.get("error"),
        "log_lines": len(lines), "log_tail": "\n".join(lines[-n:]),
    }


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
