"""Model lifecycle state machine + persisted registry (single source of truth).

This is the connective tissue between the (already-existing) behavior-envelope
decay engine (``state_engine.classify_model_state``) and the governance
machinery (gauntlet / promotion / shadow / arm). It records, for every model,
*what lifecycle state it is in* and *the certified edge-envelope it must be
monitored against* — and enforces that only legal state transitions occur.

Persistence (under ``runtime/lifecycle/``, which the cockpit file-watcher
already covers):
  * ``model_lifecycle.json``   — materialized current snapshot (one object/model)
  * ``transitions.jsonl``      — append-only, SHA-256 hash-chained audit log
  * ``envelopes/<id>.json``    — frozen ModelBehaviorEnvelope snapshots (ML2)

The JSON snapshot is always rebuildable from the transition log; the log is the
source of truth. Writes are atomic (temp+replace) and serialized by a lock file
so the cockpit can read mid-write without tearing.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

SCHEMA_VERSION = "model_lifecycle.schema.v1"
_GENESIS_HASH = "0" * 64

# --- States -----------------------------------------------------------------
CANDIDATE = "CANDIDATE"
SCREENING = "SCREENING"
GAUNTLET = "GAUNTLET"
CERTIFIED = "CERTIFIED"
SHADOW = "SHADOW"
LIVE = "LIVE"
DEGRADED = "DEGRADED"
QUARANTINED = "QUARANTINED"
ARCHIVED_PAUSED = "ARCHIVED_PAUSED"
RETIRED = "RETIRED"

STATES = frozenset({
    CANDIDATE, SCREENING, GAUNTLET, CERTIFIED, SHADOW, LIVE,
    DEGRADED, QUARANTINED, ARCHIVED_PAUSED, RETIRED,
})
TERMINAL_STATES = frozenset({RETIRED})

# --- Demotion routes (set by the decay detector, ML3) -----------------------
ROUTE_REGIME_SHIFT = "regime_shift"
ROUTE_PARAM_TWEAK = "param_tweak"
ROUTE_HYPOTHESIS_TWEAK = "hypothesis_tweak"
ROUTE_EDGE_GONE = "edge_gone"
ROUTES = frozenset({ROUTE_REGIME_SHIFT, ROUTE_PARAM_TWEAK, ROUTE_HYPOTHESIS_TWEAK, ROUTE_EDGE_GONE})

# --- Legal transition table -------------------------------------------------
# Forward research/promotion path + demotion routes + re-entry edges.
# Note: any non-terminal state may additionally transition to QUARANTINED
# (manual halt / kill-switch / defect) — handled in _is_legal, not enumerated.
LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    CANDIDATE: frozenset({SCREENING, RETIRED}),
    SCREENING: frozenset({GAUNTLET, RETIRED}),
    GAUNTLET: frozenset({CERTIFIED, RETIRED, GAUNTLET, SCREENING}),  # self-loop = re-tune
    CERTIFIED: frozenset({SHADOW}),
    SHADOW: frozenset({LIVE}),
    LIVE: frozenset({DEGRADED}),
    DEGRADED: frozenset({LIVE, GAUNTLET, SCREENING, ARCHIVED_PAUSED, RETIRED}),
    QUARANTINED: frozenset({ARCHIVED_PAUSED, GAUNTLET, SCREENING, CERTIFIED, RETIRED}),
    ARCHIVED_PAUSED: frozenset({SHADOW, LIVE, RETIRED}),
    RETIRED: frozenset(),
}


class LifecycleError(Exception):
    """Base for lifecycle registry errors."""


class IllegalTransitionError(LifecycleError):
    """Raised when a state transition violates the transition table."""


class ChainBrokenError(LifecycleError):
    """Raised when the transition log hash chain fails verification."""


# --- Paths ------------------------------------------------------------------
def _repo_root() -> Path:
    # packages/model_metrics/lifecycle.py -> repo root is parents[2]
    return Path(__file__).resolve().parents[2]


def lifecycle_dir() -> Path:
    """Root of the lifecycle store. Overridable via HFT3_LIFECYCLE_DIR (tests)."""
    override = os.environ.get("HFT3_LIFECYCLE_DIR", "").strip()
    base = Path(override) if override else (_repo_root() / "runtime" / "lifecycle")
    return base


def registry_path() -> Path:
    return lifecycle_dir() / "model_lifecycle.json"


def transitions_path() -> Path:
    return lifecycle_dir() / "transitions.jsonl"


def envelopes_dir() -> Path:
    return lifecycle_dir() / "envelopes"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_iso() -> str:
    """Public ISO-8601 UTC timestamp (used by the decay driver)."""
    return _now_iso()


# --- Record -----------------------------------------------------------------
@dataclass
class LifecycleRecord:
    model_lifecycle_id: str
    hypothesis_id: Optional[int] = None
    slug: str = ""
    symbol: str = ""
    strategy_family: str = ""
    lane: str = "cme"
    current_state: str = CANDIDATE
    current_state_since: str = ""
    current_envelope_id: Optional[str] = None
    certified_edge_envelope_snapshot: Optional[dict] = None
    last_revalidation: Optional[dict] = None
    demotion: Optional[dict] = None
    reentry_routing: Optional[dict] = None
    research_card_links: dict = field(default_factory=dict)
    governance_links: dict = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "LifecycleRecord":
        fields = cls.__dataclass_fields__
        return cls(**{k: v for k, v in raw.items() if k in fields})


# --- Hashing / canonicalization ---------------------------------------------
def _canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(payload: dict) -> str:
    import hashlib

    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


# --- Lockfile (cross-process, best-effort, with stale takeover) -------------
class _Lock:
    """Token-confirmed lockfile. O_EXCL create is the atomic gate; the token
    written into the file lets a holder release ONLY its own lock (never delete
    a live lock taken over by another writer). Stale takeover after 30s for a
    dead holder."""

    def __init__(self, path: Path, timeout_s: float = 10.0) -> None:
        self._lock = path.with_suffix(path.suffix + ".lock")
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
                    self._lock.unlink(missing_ok=True)  # dead holder; reclaim
                    continue
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise LifecycleError(f"could not acquire lock {self._lock}")
            time.sleep(0.05)

    def __exit__(self, *exc: Any) -> None:
        # Only remove the lock if it is still ours (token match).
        try:
            if self._lock.read_text(encoding="utf-8") == self._token:
                self._lock.unlink(missing_ok=True)
        except OSError:
            pass


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(path)


# --- Load / verify ----------------------------------------------------------
def load_registry() -> dict[str, LifecycleRecord]:
    """Read the materialized snapshot. Empty dict if absent/corrupt."""
    try:
        with open(registry_path(), "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    out: dict[str, LifecycleRecord] = {}
    for mid, rec in (raw.get("models", {}) or {}).items():
        out[mid] = LifecycleRecord.from_dict(rec)
    return out


def _read_transitions() -> list[dict]:
    try:
        with open(transitions_path(), "r", encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]
    except (FileNotFoundError, OSError):
        return []


def _head_path() -> Path:
    return transitions_path().with_suffix(".head.json")


def _read_head() -> tuple[int, str]:
    """O(1) tail read: (next_seq, last_self_hash). Falls back to a full scan."""
    try:
        with open(_head_path(), "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return int(d["next_seq"]), str(d["last_hash"])
    except (FileNotFoundError, json.JSONDecodeError, OSError, KeyError, ValueError):
        prior = _read_transitions()
        return len(prior), (prior[-1]["self_hash"] if prior else _GENESIS_HASH)


def _write_head(next_seq: int, last_hash: str) -> None:
    _atomic_write_json(_head_path(), {"next_seq": next_seq, "last_hash": last_hash})


def verify_chain() -> bool:
    """Return True iff the transition log hash chain is intact."""
    prev = _GENESIS_HASH
    for i, rec in enumerate(_read_transitions()):
        body = {k: v for k, v in rec.items() if k != "self_hash"}
        if body.get("prev_hash") != prev:
            return False
        if _hash(body) != rec.get("self_hash"):
            return False
        if body.get("record_seq") != i:
            return False
        prev = rec["self_hash"]
    return True


def _is_legal(from_state: Optional[str], to_state: str) -> bool:
    if to_state not in STATES:
        return False
    if from_state is None:
        # creation: only into CANDIDATE
        return to_state == CANDIDATE
    if from_state in TERMINAL_STATES:
        return False
    # manual halt / kill-switch / defect: any non-terminal -> QUARANTINED
    if to_state == QUARANTINED:
        return True
    return to_state in LEGAL_TRANSITIONS.get(from_state, frozenset())


# --- The single mutation entry point ----------------------------------------
def apply_transition(
    model_lifecycle_id: str,
    to_state: str,
    *,
    trigger: str,
    reason: str,
    actor: str,
    route: Optional[str] = None,
    envelope_id: Optional[str] = None,
    links: Optional[dict] = None,
    record_updates: Optional[dict] = None,
    create: bool = False,
    initial: Optional[dict] = None,
    ts: Optional[str] = None,
) -> LifecycleRecord:
    """Apply one lifecycle transition, atomically + audited.

    ``create=True`` registers a new model in CANDIDATE (``to_state`` must be
    CANDIDATE). Otherwise the (from -> to) edge must be legal. ``record_updates``
    merges arbitrary fields into the record (e.g. demotion/reentry_routing).
    Raises IllegalTransitionError on a forbidden edge.
    """
    if route is not None and route not in ROUTES:
        raise LifecycleError(f"unknown route {route!r}")
    stamp = ts or _now_iso()

    with _Lock(registry_path()):
        registry = load_registry()
        existing = registry.get(model_lifecycle_id)
        from_state = existing.current_state if existing else None

        if create:
            if existing is not None:
                raise LifecycleError(f"model {model_lifecycle_id} already exists")
            if to_state != CANDIDATE:
                raise IllegalTransitionError("new models must start in CANDIDATE")
            rec = LifecycleRecord(model_lifecycle_id=model_lifecycle_id, **(initial or {}))
        else:
            if existing is None:
                raise LifecycleError(f"unknown model {model_lifecycle_id}")
            if not _is_legal(from_state, to_state):
                raise IllegalTransitionError(
                    f"illegal transition {from_state} -> {to_state} for {model_lifecycle_id}"
                )
            rec = existing

        rec.current_state = to_state
        rec.current_state_since = stamp
        if envelope_id is not None:
            rec.current_envelope_id = envelope_id
        if record_updates:
            for k, v in record_updates.items():
                if hasattr(rec, k):
                    setattr(rec, k, v)
                else:
                    rec.research_card_links[k] = v
        if route is not None:
            rec.reentry_routing = {"route": route, "decided_at": stamp, **(rec.reentry_routing or {})}

        registry[model_lifecycle_id] = rec

        # Append the audit record FIRST (chain), then materialize the snapshot.
        seq, prev_hash = _read_head()
        body = {
            "record_seq": seq,
            "ts": stamp,
            "model_lifecycle_id": model_lifecycle_id,
            "from_state": from_state,
            "to_state": to_state,
            "trigger": trigger,
            "reason": reason,
            "route": route,
            "actor": actor,
            "envelope_id": envelope_id,
            "links": links or {},
            "initial": initial or {},
            "record_updates": record_updates or {},
            "prev_hash": prev_hash,
        }
        body["self_hash"] = _hash(body)
        transitions_path().parent.mkdir(parents=True, exist_ok=True)
        with open(transitions_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(body) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        _write_head(seq + 1, body["self_hash"])

        _atomic_write_json(
            registry_path(),
            {
                "schema_version": SCHEMA_VERSION,
                "generated_utc": stamp,
                "models": {mid: r.to_dict() for mid, r in registry.items()},
            },
        )
    return rec


def annotate(
    model_lifecycle_id: str,
    updates: dict,
    *,
    reason: str,
    actor: str,
    ts: Optional[str] = None,
) -> LifecycleRecord:
    """Update record fields WITHOUT a state change (e.g. last_revalidation).

    Logged as a same-state audit entry so the change is traceable, but it is not
    a transition and bypasses the legal-edge check. Drives the submit gate
    (last_revalidation.model_state) without moving the lifecycle state.
    """
    stamp = ts or _now_iso()
    with _Lock(registry_path()):
        registry = load_registry()
        rec = registry.get(model_lifecycle_id)
        if rec is None:
            raise LifecycleError(f"unknown model {model_lifecycle_id}")
        for k, v in updates.items():
            if hasattr(rec, k):
                setattr(rec, k, v)
            else:
                rec.research_card_links[k] = v
        registry[model_lifecycle_id] = rec

        seq, prev_hash = _read_head()
        body = {
            "record_seq": seq, "ts": stamp, "model_lifecycle_id": model_lifecycle_id,
            "from_state": rec.current_state, "to_state": rec.current_state,
            "trigger": "annotate", "reason": reason, "route": None, "actor": actor,
            "envelope_id": None, "links": {}, "initial": {}, "record_updates": updates,
            "prev_hash": prev_hash,
        }
        body["self_hash"] = _hash(body)
        transitions_path().parent.mkdir(parents=True, exist_ok=True)
        with open(transitions_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(body) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        _write_head(seq + 1, body["self_hash"])
        _atomic_write_json(registry_path(), {
            "schema_version": SCHEMA_VERSION, "generated_utc": stamp,
            "models": {mid: r.to_dict() for mid, r in registry.items()},
        })
    return rec


def rebuild_registry_from_log() -> dict[str, LifecycleRecord]:
    """Reconstruct + rewrite the materialized registry by replaying the log.

    Recovery path if the snapshot diverges from the (authoritative) transition
    log — e.g. a crash between the jsonl append and the json write. Each
    transition body carries its ``initial``/``record_updates`` so the replay is
    lossless.
    """
    records: dict[str, LifecycleRecord] = {}
    last_ts = ""
    last_hash = _GENESIS_HASH
    count = 0
    for rec in _read_transitions():
        count += 1
        last_hash = rec.get("self_hash", last_hash)
        mid = rec["model_lifecycle_id"]
        if rec.get("from_state") is None:
            r = LifecycleRecord(model_lifecycle_id=mid, **(rec.get("initial") or {}))
        else:
            r = records.get(mid) or LifecycleRecord(model_lifecycle_id=mid)
        r.current_state = rec["to_state"]
        r.current_state_since = rec.get("ts", "")
        if rec.get("envelope_id"):
            r.current_envelope_id = rec["envelope_id"]
        for k, v in (rec.get("record_updates") or {}).items():
            if hasattr(r, k):
                setattr(r, k, v)
            else:
                r.research_card_links[k] = v
        if rec.get("route"):
            r.reentry_routing = {"route": rec["route"], "decided_at": rec.get("ts", ""), **(r.reentry_routing or {})}
        records[mid] = r
        last_ts = rec.get("ts", "")
    _atomic_write_json(registry_path(), {
        "schema_version": SCHEMA_VERSION,
        "generated_utc": last_ts or now_iso(),
        "rebuilt": True,
        "models": {mid: r.to_dict() for mid, r in records.items()},
    })
    _write_head(count, last_hash)  # restore the tail pointer after recovery
    return records


def get_record(model_lifecycle_id: str) -> Optional[LifecycleRecord]:
    return load_registry().get(model_lifecycle_id)


def save_envelope_snapshot(envelope_id: str, payload: dict) -> Path:
    """Freeze a ModelBehaviorEnvelope snapshot for a certified model (ML2)."""
    path = envelopes_dir() / f"{envelope_id}.json"
    _atomic_write_json(path, payload)
    return path


def load_envelope_snapshot(envelope_id: str) -> Optional[dict]:
    try:
        with open(envelopes_dir() / f"{envelope_id}.json", "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
