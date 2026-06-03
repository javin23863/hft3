"""JSONL cache helpers for offline world-event evidence."""

from __future__ import annotations

import json
from pathlib import Path

from research_pipeline.world_events.models import WorldEventRecord


def world_event_cache_dir(repo_root: Path) -> Path:
    return repo_root / "artifacts" / "world_events" / "gdelt" / "events"


def cache_path_for_event_date(repo_root: Path, event_date: str) -> Path:
    if len(event_date) != 8 or not event_date.isdigit():
        raise ValueError("event_date must be YYYYMMDD")
    return world_event_cache_dir(repo_root) / f"{event_date}.jsonl"


def write_world_event_cache(repo_root: Path, records: tuple[WorldEventRecord, ...] | list[WorldEventRecord]) -> list[Path]:
    grouped: dict[str, list[WorldEventRecord]] = {}
    for record in records:
        grouped.setdefault(record.event_date, []).append(record)

    written: list[Path] = []
    for event_date, group in sorted(grouped.items()):
        path = cache_path_for_event_date(repo_root, event_date)
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(record.to_dict(), sort_keys=True) for record in group]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written.append(path)
    return written


def read_world_event_cache(path: Path) -> tuple[WorldEventRecord, ...]:
    records: list[WorldEventRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(WorldEventRecord.from_dict(json.loads(line)))
    return tuple(records)


def source_ref_for_world_event(repo_root: Path, cache_path: Path, record: WorldEventRecord) -> str:
    rel = cache_path.relative_to(repo_root).as_posix()
    if not rel.startswith("artifacts/world_events/gdelt/events/") or not rel.endswith(".jsonl"):
        raise ValueError("cache_path is not a canonical GDELT world-event cache path")
    if cache_path.stem != record.event_date:
        raise ValueError("cache_path date must match world-event record date")
    if not record.event_id.isdigit():
        raise ValueError("GDELT event_id must be numeric for canonical source_ref")
    return f"{rel}:{record.event_id}"


__all__ = [
    "cache_path_for_event_date",
    "read_world_event_cache",
    "source_ref_for_world_event",
    "world_event_cache_dir",
    "write_world_event_cache",
]
