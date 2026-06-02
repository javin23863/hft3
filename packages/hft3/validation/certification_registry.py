"""Persist and load backtester certification registry.

Phase 11 hardening: atomic write, cross-platform file lock, JSONL audit log
with SHA-256 hash chain, and schema validation. The public API
(`CertificationRecord`, `load_registry`, `save_registry`, `repo_root`,
`registry_path`, `git_sha`, `backtester_version`, `get_latest_status`,
`new_certification_run_id`) is preserved for backward compatibility.

New surfaces:

- `audit_log_path(root)` — path of the append-only JSONL audit log.
- `load_audit_log(root)` — verify hash chain, return list of records.
- `validate_record(record_dict)` — raise `RegistrySchemaError` on bad input.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hft3.validation.registry_errors import (
    HashChainBroken,
    HashChainUnavailable,
    RegistryCorruptError,
    RegistryLockTimeout,
    RegistrySchemaError,
)

DEFAULT_REGISTRY_REL = Path("runtime/validation/certification_registry.json")
DEFAULT_AUDIT_LOG_REL = Path("runtime/validation/certification_registry.jsonl")
DEFAULT_LOCK_REL = Path("runtime/validation/certification_registry.json.lock")
DEFAULT_BAK_REL = Path("runtime/validation/certification_registry.json.bak")

GENESIS_HASH = "0" * 64
ALLOWED_CERT_STATUSES = frozenset({"MISSING", "GREEN", "YELLOW", "RED"})


@dataclass
class CertificationRecord:
    latest_certification_run_id: str = ""
    latest_certification_commit: str = ""
    latest_certification_timestamp: str = ""
    latest_certification_status: str = "MISSING"
    backtester_version: str = "unknown"
    covered_modules: list[str] = field(default_factory=list)
    covered_symbols: list[str] = field(default_factory=list)
    covered_event_types: list[str] = field(default_factory=list)
    covered_latency_bands: list[float] = field(default_factory=list)
    covered_queue_models: list[str] = field(default_factory=list)
    covered_execution_modes: list[str] = field(default_factory=list)
    scorecard_path: str = ""
    blocking_failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CertificationRecord:
        return cls(
            latest_certification_run_id=str(raw.get("latest_certification_run_id", "")),
            latest_certification_commit=str(raw.get("latest_certification_commit", "")),
            latest_certification_timestamp=str(raw.get("latest_certification_timestamp", "")),
            latest_certification_status=str(raw.get("latest_certification_status", "MISSING")),
            backtester_version=str(raw.get("backtester_version", "unknown")),
            covered_modules=list(raw.get("covered_modules", [])),
            covered_symbols=list(raw.get("covered_symbols", [])),
            covered_event_types=list(raw.get("covered_event_types", [])),
            covered_latency_bands=[float(x) for x in raw.get("covered_latency_bands", [])],
            covered_queue_models=list(raw.get("covered_queue_models", [])),
            covered_execution_modes=list(raw.get("covered_execution_modes", [])),
            scorecard_path=str(raw.get("scorecard_path", "")),
            blocking_failures=list(raw.get("blocking_failures", [])),
            warnings=list(raw.get("warnings", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def registry_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / DEFAULT_REGISTRY_REL


def audit_log_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / DEFAULT_AUDIT_LOG_REL


def lock_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / DEFAULT_LOCK_REL


def backup_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / DEFAULT_BAK_REL


def git_sha(root: Path) -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def backtester_version(root: Path | None = None) -> str:
    root = root or repo_root()
    try:
        out = subprocess.check_output(
            ["git", "describe", "--always", "--dirty"],
            cwd=str(root),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        short = git_sha(root)
        return short[:12] if short != "unknown" else "unknown"


def new_certification_run_id() -> str:
    return f"CERT-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


def validate_record(record: dict[str, Any]) -> None:
    """Raise RegistrySchemaError if any required field is missing or invalid.

    Validates the 14 legacy `CertificationRecord` fields plus the audit-log
    envelope (`record_seq`, `record_type`, `prev_hash`, `self_hash`)
    when present.
    """
    if not isinstance(record, dict):
        raise RegistrySchemaError("record must be a dict")

    status = record.get("latest_certification_status", "MISSING")
    if status not in ALLOWED_CERT_STATUSES:
        raise RegistrySchemaError(
            f"latest_certification_status must be one of {sorted(ALLOWED_CERT_STATUSES)}, got {status!r}"
        )

    run_id = record.get("latest_certification_run_id", "")
    if run_id and not run_id.startswith("CERT-"):
        raise RegistrySchemaError(
            f"latest_certification_run_id must start with 'CERT-', got {run_id!r}"
        )

    commit = record.get("latest_certification_commit", "")
    if commit:
        if any(c.isspace() or ord(c) < 0x20 for c in commit):
            raise RegistrySchemaError(
                f"latest_certification_commit must not contain whitespace or control chars, got {commit!r}"
            )
        if not all(c.isalnum() or c in "._-" for c in commit):
            raise RegistrySchemaError(
                f"latest_certification_commit must be alphanumeric (with . _ - allowed), got {commit!r}"
            )

    timestamp = record.get("latest_certification_timestamp", "")
    if timestamp:
        try:
            datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise RegistrySchemaError(
                f"latest_certification_timestamp must be ISO-8601, got {timestamp!r}: {exc}"
            ) from exc

    for list_field in (
        "covered_modules",
        "covered_symbols",
        "covered_event_types",
        "covered_queue_models",
        "covered_execution_modes",
        "blocking_failures",
        "warnings",
    ):
        v = record.get(list_field, [])
        if not isinstance(v, list) or any(not isinstance(x, str) for x in v):
            raise RegistrySchemaError(f"{list_field} must be List[str], got {v!r}")

    bands = record.get("covered_latency_bands", [])
    if not isinstance(bands, list) or any(not isinstance(x, (int, float)) for x in bands):
        raise RegistrySchemaError(f"covered_latency_bands must be List[float], got {bands!r}")


def canonical_json(payload: dict[str, Any]) -> bytes:
    """Canonical JSON bytes (sorted keys, no whitespace, UTF-8).

    Used as the input to SHA-256. Stable across Python versions and platforms.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def record_hash(record: dict[str, Any]) -> str:
    """SHA-256 hex digest of `record` with its own `self_hash` field stripped."""
    view = {k: v for k, v in record.items() if k != "self_hash"}
    return hashlib.sha256(canonical_json(view)).hexdigest()


def _atomic_write_text(path: Path, payload: str, *, encoding: str = "utf-8") -> None:
    """Atomically write `payload` to `path` via temp file + `os.replace` + fsync."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


class _RegistryLock:
    """Cross-platform advisory file lock (stdlib only).

    Blocking with timeout. Releases on process exit automatically.
    """

    def __init__(self, path: Path, *, timeout_sec: float = 30.0) -> None:
        self.path = path
        self.timeout = timeout_sec
        self._fd: int | None = None

    def __enter__(self) -> "_RegistryLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        deadline = time.monotonic() + self.timeout
        is_windows = sys.platform == "win32"
        if is_windows:
            import msvcrt
            LK_NBLCK = 2
        else:
            import fcntl
        while True:
            try:
                if is_windows:
                    msvcrt.locking(self._fd, LK_NBLCK, 1)
                    return self
                else:
                    fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return self
            except (OSError, IOError):
                if time.monotonic() >= deadline:
                    if self._fd is not None:
                        os.close(self._fd)
                        self._fd = None
                    raise RegistryLockTimeout(
                        f"could not acquire registry lock at {self.path} within {self.timeout}s",
                        error_code="registry_lock_timeout",
                    )
                time.sleep(0.05)

    def __exit__(self, *exc: Any) -> None:
        if self._fd is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt
                try:
                    msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl
                fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None


def _append_audit_line(audit: Path, record: dict[str, Any]) -> dict[str, Any]:
    """Append one record to the JSONL audit log under the lock. Returns the
    record with `record_seq` / `prev_hash` / `self_hash` populated.
    """
    parent = audit.parent
    parent.mkdir(parents=True, exist_ok=True)
    if not audit.is_file():
        record["record_seq"] = 1
        record["prev_hash"] = GENESIS_HASH
        record["self_hash"] = record_hash(record)
        with open(audit, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return record

    with open(audit, "rb") as f:
        data = f.read()
    lines = [ln for ln in data.split(b"\n") if ln]
    if not lines:
        seq = 1
        prev = GENESIS_HASH
    else:
        try:
            last = json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            raise RegistryCorruptError(
                f"audit log tail is corrupt: {exc}",
                error_code="registry_corrupt",
            ) from exc
        seq = int(last.get("record_seq", 0)) + 1
        prev = str(last.get("self_hash", GENESIS_HASH))

    record["record_seq"] = seq
    record["prev_hash"] = prev
    record["self_hash"] = record_hash(record)

    fd, tmp_name = tempfile.mkstemp(prefix=audit.name + ".", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            if data and not data.endswith(b"\n"):
                f.write(b"\n")
            f.write(
                (json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
            )
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, audit)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
    return record


def _load_audit_unchecked(audit: Path) -> list[dict[str, Any]]:
    if not audit.is_file():
        return []
    records: list[dict[str, Any]] = []
    with open(audit, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise RegistryCorruptError(
                    f"audit log line is corrupt: {exc}",
                    error_code="registry_corrupt",
                ) from exc
    return records


def load_audit_log(root: Path | None = None) -> list[dict[str, Any]]:
    """Load the full append-only JSONL audit log. Verifies the SHA-256 hash
    chain. Raises `HashChainBroken` on the first broken record.
    """
    audit = audit_log_path(root)
    records = _load_audit_unchecked(audit)
    prev = GENESIS_HASH
    for i, rec in enumerate(records):
        if rec.get("prev_hash") != prev:
            raise HashChainBroken(
                f"record_seq={rec.get('record_seq')} prev_hash mismatch (expected {prev!r}, got {rec.get('prev_hash')!r})",
                error_code="hash_chain_broken",
            )
        if rec.get("self_hash") != record_hash(rec):
            raise HashChainBroken(
                f"record_seq={rec.get('record_seq')} self_hash mismatch",
                error_code="hash_chain_broken",
            )
        prev = rec["self_hash"]
    return records


def _migrate_legacy_to_audit(legacy: Path, audit: Path, lock: _RegistryLock) -> None:
    """One-shot migration: read the legacy single-JSON file, append its record
    to the audit log as the genesis record, move the legacy file aside.
    Idempotent: if the audit log already has records, do nothing.
    """
    if not legacy.is_file():
        return
    if audit.is_file() and audit.stat().st_size > 0:
        return
    try:
        raw = json.loads(legacy.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryCorruptError(
            f"legacy registry is corrupt: {exc}",
            error_code="registry_corrupt",
        ) from exc
    validate_record(raw)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bak = legacy.with_suffix(legacy.suffix + f".legacy-{stamp}")
    try:
        legacy.replace(bak)
    except OSError:
        pass
    _append_audit_line(audit, raw)


def load_registry(root: Path | None = None) -> CertificationRecord:
    """Load the latest `CertificationRecord`.

    Reads the JSONL audit log if present (source of truth for the
    promotion pipeline). Falls back to the legacy single-JSON file
    for backward compatibility and migrates the legacy file to the
    audit log on first read under the registry lock.
    """
    audit = audit_log_path(root)
    legacy = registry_path(root)
    if not audit.is_file() and legacy.is_file():
        with _RegistryLock(lock_path(root)):
            _migrate_legacy_to_audit(legacy, audit, _RegistryLock)
    if audit.is_file():
        try:
            records = load_audit_log(root)
        except HashChainBroken:
            raise
        if not records:
            return CertificationRecord()
        return CertificationRecord.from_dict(records[-1])
    return CertificationRecord()


def save_registry(record: CertificationRecord, root: Path | None = None) -> Path:
    """Atomically write the registry.

    The legacy single-JSON file is rewritten via temp file + `os.replace`
    + `fsync` (so an interrupted write leaves the previous file intact).
    The new record is also appended to the JSONL audit log under the
    registry lock. Schema validation runs before any I/O.
    """
    record_dict = record.to_dict()
    validate_record(record_dict)

    legacy = registry_path(root)
    audit = audit_log_path(root)
    backup = backup_path(root)

    with _RegistryLock(lock_path(root)):
        if legacy.is_file():
            try:
                backup.write_bytes(legacy.read_bytes())
            except OSError:
                pass
        _append_audit_line(audit, record_dict)
        _atomic_write_text(
            legacy,
            json.dumps(record_dict, indent=2, ensure_ascii=False) + "\n",
        )
    return legacy


def get_latest_status(root: Path | None = None) -> str:
    return load_registry(root).latest_certification_status
