"""Storage layout for MBO release event paths."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def mbo_release_root(repo_root: Path) -> Path:
    return repo_root / "data" / "mbo_release"


def release_slot_dir(repo_root: Path, release_id: str, symbol: str) -> Path:
    safe_sym = symbol.replace("/", "_")
    return mbo_release_root(repo_root) / release_id / safe_sym


def raw_dbn_path(slot_dir: Path) -> Path:
    return slot_dir / "raw.dbn.zst"


def events_jsonl_path(slot_dir: Path) -> Path:
    return slot_dir / "events.jsonl"


def release_event_path_manifest(slot_dir: Path) -> Path:
    return slot_dir / "release_event_path.json"


def validation_report_path(slot_dir: Path) -> Path:
    return slot_dir / "validation.json"


def hashes_path(slot_dir: Path) -> Path:
    return slot_dir / "hashes.json"


def write_events_jsonl(events: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, sort_keys=True) + "\n")


def read_events_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def copy_raw_dbn(src: Path, slot_dir: Path) -> Path:
    slot_dir.mkdir(parents=True, exist_ok=True)
    dest = raw_dbn_path(slot_dir)
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
    return dest


def build_release_event_path(
    *,
    release_id: str,
    release_name: str,
    scheduled_release_timestamp: str,
    actual_release_timestamp: str,
    symbol: str,
    venue: str,
    window_start: str,
    window_end: str,
    events_ref: str,
    event_count: int,
    first_sequence: int | None,
    last_sequence: int | None,
    sequence_gap_count: int,
    source_vendor: str,
    dataset_id: str,
    validation_status: str,
) -> dict[str, Any]:
    return {
        "release_event_path": {
            "release_id": release_id,
            "release_name": release_name,
            "scheduled_release_timestamp": scheduled_release_timestamp,
            "actual_release_timestamp": actual_release_timestamp,
            "symbol": symbol,
            "venue": venue,
            "window_start": window_start,
            "window_end": window_end,
            "raw_mbo_events_ref": events_ref,
            "event_count": event_count,
            "first_sequence": first_sequence,
            "last_sequence": last_sequence,
            "sequence_gap_count": sequence_gap_count,
            "source_vendor": source_vendor,
            "dataset_id": dataset_id,
            "validation_status": validation_status,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        }
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def load_release_event_path(slot_dir: Path) -> dict[str, Any] | None:
    p = release_event_path_manifest(slot_dir)
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))
