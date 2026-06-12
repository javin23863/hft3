"""Append-only, hash-chained autonomy audit log.

Every autonomous decision is one record; the chain makes the log
tamper-evident and the decision reproducible from the log alone. Same chain
construction as the lifecycle transitions log.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from . import paths

_GENESIS = "0" * 64


class _Lock:
    """Token-confirmed cross-process append lock (mirrors lifecycle lock)."""

    def __init__(self, target, timeout_s: float = 10.0) -> None:
        self._lock = target.with_suffix(target.suffix + ".lock")
        self._timeout = timeout_s
        self._token = f"{os.getpid()}-{time.time_ns()}"

    def _try_create(self) -> bool:
        try:
            fd = os.open(str(self._lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        try:
            os.write(fd, self._token.encode("utf-8"))
        finally:
            os.close(fd)
        return True

    def __enter__(self) -> "_Lock":
        self._lock.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self._timeout
        while True:
            if self._try_create():
                return self
            try:
                if time.time() - self._lock.stat().st_mtime > 30:
                    self._lock.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError(f"could not acquire audit lock {self._lock}")
            time.sleep(0.02)

    def __exit__(self, *exc: Any) -> None:
        try:
            if self._lock.read_text(encoding="utf-8") == self._token:
                self._lock.unlink(missing_ok=True)
        except OSError:
            pass


def _head_path():
    return paths.audit_path().with_suffix(".head.json")


def _read_head() -> tuple[int, str]:
    try:
        with open(_head_path(), "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return int(d["next_seq"]), str(d["last_hash"])
    except (FileNotFoundError, json.JSONDecodeError, OSError, KeyError, ValueError):
        prior = _read()
        return len(prior), (prior[-1]["self_hash"] if prior else _GENESIS)


def _write_head(next_seq: int, last_hash: str) -> None:
    p = _head_path()
    tmp = p.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"next_seq": next_seq, "last_hash": last_hash}, fh)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(p)

# Event types.
DEGRADATION_DETECTED = "DEGRADATION_DETECTED"
AUTO_DEMOTE = "AUTO_DEMOTE"
RETEST_START = "RETEST_START"
RETEST_RESULT = "RETEST_RESULT"
AUTO_GATE_EVAL = "AUTO_GATE_EVAL"
AUTO_ARM = "AUTO_ARM"
AUTO_ARM_REFUSED = "AUTO_ARM_REFUSED"
SHADOW_START = "SHADOW_START"
SHADOW_PASS = "SHADOW_PASS"
SHADOW_FAIL = "SHADOW_FAIL"
AUTONOMY_FROZEN = "AUTONOMY_FROZEN"
AUTONOMY_UNFROZEN = "AUTONOMY_UNFROZEN"
PROPOSER_RUN = "PROPOSER_RUN"
MASTER_DISABLED = "MASTER_DISABLED"


def _canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(payload: dict) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _read() -> list[dict]:
    try:
        with open(paths.audit_path(), "r", encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]
    except (FileNotFoundError, OSError):
        return []


def append(event_type: str, payload: dict, *, model_id: Optional[str] = None, ts: Optional[str] = None) -> dict:
    p = paths.audit_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # Hold the lock across read-tail + write so the chain link can't race.
    with _Lock(p):
        seq, prev = _read_head()
        body = {
            "record_seq": seq,
            "ts": ts or datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "model_id": model_id,
            "payload": payload,
            "prev_hash": prev,
        }
        body["self_hash"] = _hash(body)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(body) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        _write_head(seq + 1, body["self_hash"])
    return body


def verify_chain() -> bool:
    prev = _GENESIS
    for i, rec in enumerate(_read()):
        body = {k: v for k, v in rec.items() if k != "self_hash"}
        if body.get("prev_hash") != prev or body.get("record_seq") != i:
            return False
        if _hash(body) != rec.get("self_hash"):
            return False
        prev = rec["self_hash"]
    return True


def tail(n: int = 50) -> list[dict]:
    return _read()[-n:]
