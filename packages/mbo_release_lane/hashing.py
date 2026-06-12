"""Content hashing for deterministic MBO lane artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_file(path: Path, chunk_size: int = 65536) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def sha256_json_payload(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_hashes(
    *,
    raw_dbn: Path | None,
    events_jsonl: Path | None,
    validation: dict[str, Any],
) -> dict[str, str]:
    out: dict[str, str] = {}
    if raw_dbn and raw_dbn.is_file():
        out["raw_input_sha256"] = sha256_file(raw_dbn)
    if events_jsonl and events_jsonl.is_file():
        out["normalized_events_sha256"] = sha256_file(events_jsonl)
    out["validation_report_sha256"] = sha256_json_payload(validation)
    return out
