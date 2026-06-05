"""Unified economic event universe service.

This module is the public read API for event metadata, calendar rows, runnable
campaign rows, and event snapshot artifacts.
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from hft3_bootstrap import repo_root

from economic_event_universe.holidays import apply_holiday_adjustment
from economic_event_universe.labels import row_to_event_context
from economic_event_universe.registry import event_definitions, get_event_def
from economic_event_universe.windows import download_window, window_name_for

VALID_ROW_STATUSES = frozenset({"SOURCED", "SEED", "DISABLED", "RETIRED"})
RUNNABLE_ROW_STATUS = "SOURCED"


@dataclass(frozen=True)
class EventTypeRecord:
    event_type: str
    status: str
    agency: str
    official_source_url: str
    anchor_time: str
    timezone: str
    event_context: str
    window_name: str
    symbols: str
    schedule: str
    context_priority: int
    universe_defined: bool = True


@dataclass(frozen=True)
class CalendarRowRecord:
    event_type: str
    release_date: str
    release_time: str
    timezone: str
    source: str
    source_url: str
    row_status: str
    source_file: str
    event_context: str
    runnable_eligible: bool
    universe_defined: bool


@dataclass(frozen=True)
class RunnableEventRecord:
    event_id: str
    event_type: str
    release_date: str
    release_time: str
    timezone: str
    window_name: str
    start_offset_seconds: int
    end_offset_seconds: int
    symbols: str
    priority: int
    source: str
    source_url: str
    effective_date: str
    notes: str
    row_status: str
    source_file: str
    event_context: str
    universe_defined: bool
    runnable_eligible: bool


def config_root(root: Path | None = None) -> Path:
    base = root or repo_root()
    return base / "packages" / "economic_event_universe" / "config"


def calendar_root(root: Path | None = None) -> Path:
    return config_root(root) / "calendars"


def sourced_calendar_dir(root: Path | None = None) -> Path:
    return calendar_root(root) / "sourced"


def seed_calendar_dir(root: Path | None = None) -> Path:
    return calendar_root(root) / "seed"


def events_csv_path(root: Path | None = None) -> Path:
    base = root or repo_root()
    return base / "packages" / "data_system" / "config" / "events.csv"


def list_event_types() -> list[dict[str, Any]]:
    records: list[EventTypeRecord] = []
    for et, cfg in sorted(event_definitions().items()):
        records.append(
            EventTypeRecord(
                event_type=et,
                status=str(cfg.get("status", "")),
                agency=str(cfg.get("agency", "")),
                official_source_url=str(cfg.get("official_source_url", "")),
                anchor_time=str(cfg.get("anchor_time", "")),
                timezone=str(cfg.get("timezone", "")),
                event_context=str(cfg.get("event_context_label", "")),
                window_name=str(cfg.get("window_name", "")),
                symbols=str(cfg.get("symbol_universe", "")),
                schedule=str(cfg.get("schedule", "")),
                context_priority=int(cfg.get("context_priority", 50)),
            )
        )
    return [asdict(r) for r in records]


def event_type_status(event_type: str) -> str:
    return str(get_event_def(event_type).get("status", ""))


def resolve_event_context(event_type: str, window_name: str | None = None) -> str:
    cfg = get_event_def(event_type)
    return row_to_event_context(event_type, window_name or str(cfg.get("window_name", "TIGHT")))


def _calendar_files(root: Path | None, *, include_seed: bool) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    sourced = sourced_calendar_dir(root)
    if sourced.is_dir():
        files.extend((p, RUNNABLE_ROW_STATUS) for p in sorted(sourced.glob("*.csv")))
    if include_seed:
        seed = seed_calendar_dir(root)
        if seed.is_dir():
            files.extend((p, "SEED") for p in sorted(seed.glob("*.csv")))
    return files


def _read_calendar_file(path: Path, default_status: str) -> Iterable[CalendarRowRecord]:
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            et = str(row.get("event_type", "")).strip()
            row_status = str(row.get("row_status", "")).strip().upper()
            if not row_status:
                raise ValueError(f"{path}: calendar row for {et or '<missing>'} missing row_status")
            if row_status != default_status and default_status == "SEED":
                raise ValueError(f"{path}: seed calendar row has row_status={row_status}, expected SEED")
            if row_status == "SEED" and default_status == RUNNABLE_ROW_STATUS:
                raise ValueError(f"{path}: SEED row belongs under config/calendars/seed/")
            if row_status not in VALID_ROW_STATUSES:
                raise ValueError(f"{path}: invalid row_status={row_status}")
            defs = event_definitions()
            universe_defined = et in defs
            cfg = defs.get(et, {})
            window_name = str(cfg.get("window_name", "TIGHT"))
            yield CalendarRowRecord(
                event_type=et,
                release_date=str(row.get("release_date", "")).strip(),
                release_time=str(row.get("release_time", cfg.get("anchor_time", ""))).strip(),
                timezone=str(row.get("timezone", cfg.get("timezone", ""))).strip(),
                source=str(row.get("source", cfg.get("agency", ""))).strip(),
                source_url=str(row.get("source_url", cfg.get("official_source_url", ""))).strip(),
                row_status=row_status,
                source_file=str(path),
                event_context=row_to_event_context(et, window_name) if universe_defined else "",
                runnable_eligible=row_status == RUNNABLE_ROW_STATUS and universe_defined,
                universe_defined=universe_defined,
            )


def list_calendar_rows(
    root: Path | None = None,
    *,
    include_seed: bool = True,
    statuses: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    allowed = {s.upper() for s in statuses} if statuses else None
    records: list[CalendarRowRecord] = []
    for path, default_status in _calendar_files(root, include_seed=include_seed):
        for record in _read_calendar_file(path, default_status):
            if allowed and record.row_status not in allowed:
                continue
            records.append(record)
    records.sort(key=lambda r: (r.release_date, r.event_type, r.source_file))
    return [asdict(r) for r in records]


def _event_id(event_type: str, release_date: str, window_name: str) -> str:
    y, m, d = release_date.split("-")
    return f"{event_type}_{y}_{m}_{d}_{window_name}"


def _template_for(event_type: str) -> dict[str, Any]:
    cfg = get_event_def(event_type)
    start, end = download_window(event_type)
    return {
        "window_name": window_name_for(event_type),
        "start_offset_seconds": start,
        "end_offset_seconds": end,
        "symbols": str(cfg.get("symbol_universe", "")),
        "priority": int(cfg.get("context_priority", 50)),
        "effective_date": "2018-01-01",
        "notes": f"{event_type} from economic_event_universe sourced calendar",
    }


def list_runnable_events(root: Path | None = None) -> list[dict[str, Any]]:
    records: list[RunnableEventRecord] = []
    for row in list_calendar_rows(root, include_seed=False, statuses=[RUNNABLE_ROW_STATUS]):
        et = str(row["event_type"])
        if not row["universe_defined"]:
            continue
        tmpl = _template_for(et)
        rd = apply_holiday_adjustment(et, date.fromisoformat(str(row["release_date"]))).isoformat()
        event_id = _event_id(et, rd, str(tmpl["window_name"]))
        records.append(
            RunnableEventRecord(
                event_id=event_id,
                event_type=et,
                release_date=rd,
                release_time=str(row["release_time"]),
                timezone=str(row["timezone"]),
                window_name=str(tmpl["window_name"]),
                start_offset_seconds=int(tmpl["start_offset_seconds"]),
                end_offset_seconds=int(tmpl["end_offset_seconds"]),
                symbols=str(tmpl["symbols"]),
                priority=int(tmpl["priority"]),
                source=str(row["source"]),
                source_url=str(row["source_url"]),
                effective_date=str(tmpl["effective_date"]),
                notes=str(tmpl["notes"]),
                row_status=str(row["row_status"]),
                source_file=str(row["source_file"]),
                event_context=str(row["event_context"]),
                universe_defined=bool(row["universe_defined"]),
                runnable_eligible=bool(row["runnable_eligible"]),
            )
        )
    records.sort(key=lambda r: (r.event_type, r.release_date, r.event_id))
    return [asdict(r) for r in records]


def snapshot_artifacts_for_event(event_id: str, root: Path | None = None) -> list[dict[str, Any]]:
    base = root or repo_root()
    candidates = [
        base / "runtime" / "event_snapshots",
        base / "artifacts" / "event_snapshots",
        base / "artifacts" / "research_cards",
    ]
    out: list[dict[str, Any]] = []
    for directory in candidates:
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob(f"{event_id}*")):
            if path.is_file():
                out.append(
                    {
                        "event_id": event_id,
                        "path": str(path),
                        "name": path.name,
                        "size_bytes": path.stat().st_size,
                        "suffix": path.suffix,
                    }
                )
    return out


def inventory(root: Path | None = None) -> dict[str, Any]:
    types = list_event_types()
    calendars = list_calendar_rows(root, include_seed=True)
    runnable = list_runnable_events(root)
    calendar_counts = Counter(row["event_type"] for row in calendars)
    status_counts = Counter(row["row_status"] for row in calendars)
    runnable_counts = Counter(row["event_type"] for row in runnable)
    type_rows: list[dict[str, Any]] = []
    for record in types:
        et = str(record["event_type"])
        dates = [str(r["release_date"]) for r in calendars if r["event_type"] == et]
        type_rows.append(
            {
                **record,
                "calendar_row_count": int(calendar_counts.get(et, 0)),
                "runnable_row_count": int(runnable_counts.get(et, 0)),
                "first_release_date": min(dates) if dates else "",
                "last_release_date": max(dates) if dates else "",
            }
        )
    return {
        "canonical_config_root": str(config_root(root)),
        "generated_events_csv": str(events_csv_path(root)),
        "event_type_count": len(types),
        "calendar_row_count": len(calendars),
        "runnable_event_count": len(runnable),
        "row_status_counts": dict(sorted(status_counts.items())),
        "event_types": type_rows,
    }
