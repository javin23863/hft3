"""Durable job queue + CHI404 offload guard.

Long re-screens/rebuilds/shadows do not run inline in a scheduled tick — they
are enqueued here and executed by a worker (laptop subprocess for light jobs,
CHI404 under taskset for heavy ones). The queue is a set of directories so a
crash leaves a recoverable trail; the orchestrator resumes from it. Heavy CHI404
dispatch is guarded against colliding with the capture daemon.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Optional

from model_metrics import lifecycle

_STATES = ("pending", "running", "done", "failed")
_MANIFEST_REQUIRED = frozenset({
    "model_id", "route", "reason", "created_by", "created_at", "source_evidence",
})
_MANIFEST_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

_VALID_ROUTES = frozenset({
    lifecycle.ROUTE_REGIME_SHIFT,
    lifecycle.ROUTE_PARAM_TWEAK,
    lifecycle.ROUTE_HYPOTHESIS_TWEAK,
    lifecycle.ROUTE_EDGE_GONE,
})


class DuplicateActiveJob(RuntimeError):
    """Raised when a singleton job already has a pending/running instance."""

    def __init__(self, job: dict) -> None:
        self.job = job
        super().__init__(
            f"{job.get('model_id')} already {job.get('state')} as {job.get('job_id')}"
        )


class _EnqueueLock:
    def __init__(self, timeout_s: float = 5.0) -> None:
        self._path = jobs_dir() / "enqueue.lock"
        self._timeout = timeout_s
        self._token = f"{os.getpid()}-{time.time_ns()}"

    def __enter__(self) -> "_EnqueueLock":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                fd = os.open(str(self._path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, self._token.encode("utf-8"))
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - self._path.stat().st_mtime > 30:
                        self._path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    raise RuntimeError("could not acquire enqueue lock")
                time.sleep(0.02)

    def __exit__(self, *exc) -> None:
        try:
            if self._path.read_text(encoding="utf-8") == self._token:
                self._path.unlink(missing_ok=True)
        except OSError:
            pass


def _bump_counter() -> int:
    """Monotonic job counter (under the enqueue lock)."""
    p = jobs_dir() / "counter.txt"
    try:
        n = int(p.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError, OSError):
        n = _next_seq()  # seed from existing files on first run
    n += 1
    p.write_text(str(n), encoding="utf-8")
    return n


def jobs_dir() -> Path:
    return lifecycle.lifecycle_dir() / "jobs"


def manifests_dir() -> Path:
    d = jobs_dir() / "manifests"
    d.mkdir(parents=True, exist_ok=True)
    return d


def validate_route_manifest(manifest: dict) -> list[str]:
    """Return validation errors; empty list means OK."""
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return ["manifest must be an object"]
    missing = sorted(_MANIFEST_REQUIRED - set(manifest))
    if missing:
        errors.append(f"missing fields: {', '.join(missing)}")
    route = manifest.get("route")
    if route not in _VALID_ROUTES:
        errors.append(f"unknown route {route!r}")
    mid = manifest.get("model_id")
    if not mid or not isinstance(mid, str):
        errors.append("model_id must be a non-empty string")
    evidence = manifest.get("source_evidence")
    if not isinstance(evidence, dict) or not evidence:
        errors.append("source_evidence must be a non-empty object")
    return errors


def write_route_manifest(manifest: dict) -> Path:
    """Persist a validated route manifest under runtime/lifecycle/jobs/manifests/."""
    errors = validate_route_manifest(manifest)
    if errors:
        raise ValueError("; ".join(errors))
    mid = str(manifest["model_id"])
    route = str(manifest["route"])
    if not manifest.get("manifest_id"):
        manifest_id = f"{mid}_{route}_{time.time_ns()}"
    else:
        manifest_id = str(manifest["manifest_id"])
        if not _MANIFEST_ID_RE.fullmatch(manifest_id):
            raise ValueError("invalid manifest_id")
    manifest = {**manifest, "manifest_id": manifest_id}
    p = manifests_dir() / f"{manifest_id}.json"
    if p.is_file():
        raise FileExistsError(f"manifest already exists: {manifest_id}")
    tmp = p.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(p)
    return p


def load_route_manifest(manifest_id: str) -> dict:
    if not manifest_id or not _MANIFEST_ID_RE.fullmatch(manifest_id):
        raise ValueError("invalid manifest_id")
    p = manifests_dir() / f"{manifest_id}.json"
    if not p.is_file():
        raise FileNotFoundError(manifest_id)
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("manifest malformed")
    errors = validate_route_manifest(data)
    if errors:
        raise ValueError("; ".join(errors))
    return data


def _state_dir(state: str) -> Path:
    d = jobs_dir() / state
    d.mkdir(parents=True, exist_ok=True)
    return d


def _next_seq() -> int:
    n = 0
    for st in _STATES:
        d = jobs_dir() / st
        if d.is_dir():
            n += len(list(d.glob("*.json")))
    return n


def enqueue(model_id: str, route: str, command: dict, *, host: str = "laptop", depends_on: Optional[list] = None) -> str:
    with _EnqueueLock():
        job_id = _enqueue_locked(model_id, route, command, host=host, depends_on=depends_on)
    return job_id


def enqueue_singleton(model_id: str, route: str, command: dict, *, host: str = "laptop", depends_on: Optional[list] = None) -> str:
    """Atomically enqueue only when no pending/running job exists for model_id."""
    with _EnqueueLock():
        active = _active_locked(model_id)
        if active is not None:
            raise DuplicateActiveJob(active)
        return _enqueue_locked(model_id, route, command, host=host, depends_on=depends_on)


def _enqueue_locked(model_id: str, route: str, command: dict, *, host: str, depends_on: Optional[list]) -> str:
    seq = _bump_counter()
    job_id = f"{model_id}_{route}_{seq}"
    job = {
        "job_id": job_id, "model_id": model_id, "route": route, "state": "pending",
        "host": host, "command": command, "depends_on": depends_on or [],
        "artifacts": {}, "error": None,
    }
    _write(job_id, "pending", job)
    return job_id


def _active_locked(model_id: str) -> Optional[dict]:
    for state in ("pending", "running"):
        for job in list_jobs(state):
            if job.get("model_id") == model_id:
                return job
    return None


def _path(job_id: str, state: str) -> Path:
    return _state_dir(state) / f"{job_id}.json"


def _write(job_id: str, state: str, job: dict) -> None:
    p = _path(job_id, state)
    tmp = p.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(job, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(p)


def _move(job_id: str, frm: str, to: str, mutate) -> dict:
    src = _path(job_id, frm)
    job = json.loads(src.read_text(encoding="utf-8"))
    mutate(job)
    job["state"] = to
    _write(job_id, to, job)
    src.unlink(missing_ok=True)
    return job


def claim(job_id: str) -> dict:
    return _move(job_id, "pending", "running", lambda j: None)


def complete(job_id: str, artifacts: Optional[dict] = None) -> dict:
    return _move(job_id, "running", "done", lambda j: j.update({"artifacts": artifacts or {}}))


def fail(job_id: str, error: str, artifacts: Optional[dict] = None) -> dict:
    def mutate(j: dict) -> None:
        j.update({"error": error})
        if artifacts is not None:
            j["artifacts"] = artifacts

    return _move(job_id, "running", "failed", mutate)


def list_jobs(state: str) -> list[dict]:
    d = jobs_dir() / state
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def capture_healthy() -> bool:
    """Guard: don't dispatch a heavy CHI404 job while capture is unhealthy/mid-roll.

    Fail-closed: if the baseline is unreadable, assume NOT healthy (refuse dispatch).
    """
    p = lifecycle._repo_root() / "runtime" / "chi404" / "baseline" / "latest_capture.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    if not isinstance(data, dict):
        return False
    return not (data.get("known_gaps") or data.get("drift_warnings"))


class Chi404Lock:
    """Single heavy CHI404 job at a time."""

    def __init__(self) -> None:
        self._path = jobs_dir() / "chi404.lock"

    def acquire(self) -> bool:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(self._path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return True
        except FileExistsError:
            return False

    def release(self) -> None:
        self._path.unlink(missing_ok=True)
