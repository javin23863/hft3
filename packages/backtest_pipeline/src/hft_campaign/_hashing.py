"""Canonical hashing helpers for HftBacktest campaign artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

VOLATILE_SCENARIO_KEYS = frozenset(
    {
        "scenario_id",
        "scenario_hash",
        "created_at_utc",
        "updated_at_utc",
        "timestamp_utc",
    }
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def sha256_hex(value: Any) -> str:
    if isinstance(value, (str, bytes)):
        payload = value if isinstance(value, bytes) else value.encode("utf-8")
    else:
        payload = canonical_json(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_mapping(payload: Mapping[str, Any], *, exclude: frozenset[str] | None = None) -> str:
    excluded = exclude or frozenset()
    stripped = {k: v for k, v in payload.items() if k not in excluded}
    return sha256_hex(stripped)
