"""Release calendar CSV parsing — no hardcoded event-type or filename lists."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterator

from economic_event_universe.registry import event_definitions

CalendarRow = tuple[str, str, str, str, str, str, str]
# event_type, release_date, release_time, timezone, row_status, source, source_url


def parse_row_status(row: dict, *, from_seed_dir: bool) -> str:
    """Infer row_status from CSV. Explicit column wins; never use filename allowlists."""
    explicit = str(row.get("row_status", "") or "").strip().upper()
    if explicit in ("SOURCED", "SEED", "RULE_BASED"):
        return explicit
    if from_seed_dir:
        return "SEED"
    if str(row.get("source", "") or "").strip() and str(row.get("source_url", "") or "").strip():
        return "SOURCED"
    return "SEED"


def iter_calendar_rows(
    path: Path,
    *,
    from_seed_dir: bool,
    accept_seed: bool,
) -> Iterator[CalendarRow]:
    """Yield calendar row fields including agency source metadata."""
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            et = str(row.get("event_type", "")).strip()
            if not et or et not in event_definitions():
                continue
            status = parse_row_status(row, from_seed_dir=from_seed_dir)
            if status == "SEED" and not accept_seed:
                continue
            if status not in ("SOURCED", "SEED", "RULE_BASED"):
                continue
            yield (
                et,
                str(row["release_date"]),
                str(row.get("release_time") or ""),
                str(row.get("timezone") or ""),
                status,
                str(row.get("source") or ""),
                str(row.get("source_url") or ""),
            )


def sourced_event_types_in_dir(calendar_dir: Path) -> set[str]:
    """Event types with at least one SOURCED row in release_calendars/."""
    out: set[str] = set()
    if not calendar_dir.is_dir():
        return out
    for path in sorted(calendar_dir.glob("*.csv")):
        for et, _, _, _, status, _, _ in iter_calendar_rows(path, from_seed_dir=False, accept_seed=True):
            if status == "SOURCED":
                out.add(et)
    return out


def sourced_calendar_filenames(calendar_dir: Path) -> set[str]:
    """CSV filenames under release_calendars/ that contain at least one SOURCED row."""
    out: set[str] = set()
    if not calendar_dir.is_dir():
        return out
    for path in sorted(calendar_dir.glob("*.csv")):
        for _, _, _, _, status, _, _ in iter_calendar_rows(path, from_seed_dir=False, accept_seed=True):
            if status == "SOURCED":
                out.add(path.name)
                break
    return out


def unsourced_release_calendar_warnings(calendar_dir: Path) -> list[str]:
    """Rows in release_calendars/ missing source+source_url (treated as SEED, excluded from events.csv)."""
    warnings: list[str] = []
    if not calendar_dir.is_dir():
        return warnings
    for path in sorted(calendar_dir.glob("*.csv")):
        with path.open(newline="", encoding="utf-8") as f:
            for i, row in enumerate(csv.DictReader(f), start=2):
                et = str(row.get("event_type", "")).strip()
                if not et or et not in event_definitions():
                    continue
                if parse_row_status(row, from_seed_dir=False) == "SEED":
                    warnings.append(
                        f"{path.name}:{i} {et} {row.get('release_date')} "
                        "missing source+source_url or row_status=SOURCED — skipped"
                    )
    return warnings
