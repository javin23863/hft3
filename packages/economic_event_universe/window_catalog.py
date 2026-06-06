"""Build point-in-time windows for all 44 catalog event types (seed + sourced + rule-based)."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator

import pytz

from economic_event_universe.holidays import apply_holiday_adjustment
from economic_event_universe.registry import (
    catalog_event_types,
    default_cme_symbols,
    event_definitions,
    get_event_def,
)
from economic_event_universe.windows import download_window, window_name_for

_SOURCED_CALENDAR_FILES = frozenset({"bls_cpi.csv", "bls_nfp.csv", "prop_flatten.csv"})


@dataclass(frozen=True)
class CatalogWindow:
    event_id: str
    event_type: str
    release_date: str
    window_name: str
    start_utc: datetime
    end_utc: datetime
    row_status: str  # SOURCED | SEED | RULE_BASED
    duration_seconds: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "release_date": self.release_date,
            "window_name": self.window_name,
            "start_utc": self.start_utc.isoformat(),
            "end_utc": self.end_utc.isoformat(),
            "row_status": self.row_status,
            "duration_seconds": self.duration_seconds,
        }


def _event_id(event_type: str, release_date: str, window_name: str) -> str:
    y, m, d = release_date.split("-")
    return f"{event_type}_{y}_{m}_{d}_{window_name}"


def _anchor_and_window(
    event_type: str,
    release_date: str,
    release_time: str,
    timezone: str,
) -> tuple[datetime, datetime, datetime, str, int]:
    wn = window_name_for(event_type)
    start_off, end_off = download_window(event_type)
    tz = pytz.timezone(timezone)
    local_dt = datetime.strptime(f"{release_date} {release_time}", "%Y-%m-%d %H:%M:%S")
    anchor = tz.localize(local_dt).astimezone(pytz.UTC)
    start_utc = anchor + timedelta(seconds=start_off)
    end_utc = anchor + timedelta(seconds=end_off)
    return anchor, start_utc, end_utc, wn, int((end_utc - start_utc).total_seconds())


def _row_to_window(
    event_type: str,
    release_date: str,
    release_time: str,
    timezone: str,
    row_status: str,
) -> CatalogWindow | None:
    if event_type not in event_definitions():
        return None
    adj = apply_holiday_adjustment(event_type, date.fromisoformat(release_date))
    rd = adj.isoformat()
    cfg = get_event_def(event_type)
    rt = release_time or str(cfg.get("anchor_time", "08:30:00"))
    tz = timezone or str(cfg.get("timezone", "America/New_York"))
    _, start_utc, end_utc, wn, dur = _anchor_and_window(event_type, rd, rt, tz)
    return CatalogWindow(
        event_id=_event_id(event_type, rd, wn),
        event_type=event_type,
        release_date=rd,
        window_name=wn,
        start_utc=start_utc,
        end_utc=end_utc,
        row_status=row_status,
        duration_seconds=dur,
    )


def _iter_csv_calendar_rows(
    path: Path,
    *,
    accept_seed: bool,
) -> Iterator[tuple[str, str, str, str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            et = str(row.get("event_type", "")).strip()
            if not et or et not in event_definitions():
                continue
            status = str(row.get("row_status", "") or "").upper()
            if not status:
                status = "SOURCED" if path.name in _SOURCED_CALENDAR_FILES else "SEED"
            if status == "SEED" and not accept_seed:
                continue
            if status not in ("SOURCED", "SEED", "RULE_BASED"):
                continue
            rd = str(row["release_date"])
            rt = str(row.get("release_time") or "")
            tz = str(row.get("timezone") or "")
            yield et, rd, rt, tz, status


def _iter_rule_based_windows(
    start_year: int = 2018,
    end_year: int = 2025,
) -> Iterator[CatalogWindow]:
    """Generate rule-based session rows for types without seed calendars."""
    rule_types = {
        et
        for et, cfg in event_definitions().items()
        if cfg.get("schedule") == "rule_based"
    }
    start = date(start_year, 1, 1)
    end = date(end_year, 12, 31)

    if "FRIDAY_CLOSE" in rule_types:
        d = start
        while d <= end:
            if d.weekday() == 4:  # Friday
                w = _row_to_window(
                    "FRIDAY_CLOSE",
                    d.isoformat(),
                    str(get_event_def("FRIDAY_CLOSE").get("anchor_time", "16:00:00")),
                    str(get_event_def("FRIDAY_CLOSE").get("timezone", "America/New_York")),
                    "RULE_BASED",
                )
                if w:
                    yield w
            d += timedelta(days=1)

    if "CASH_EQUITY_OPEN" in rule_types:
        d = start
        while d <= end:
            if d.weekday() < 5:
                w = _row_to_window(
                    "CASH_EQUITY_OPEN",
                    d.isoformat(),
                    str(get_event_def("CASH_EQUITY_OPEN").get("anchor_time", "09:30:00")),
                    str(get_event_def("CASH_EQUITY_OPEN").get("timezone", "America/New_York")),
                    "RULE_BASED",
                )
                if w:
                    yield w
            d += timedelta(days=1)

    if "PROP_REOPEN" in rule_types:
        d = start
        while d <= end:
            if d.weekday() < 5:
                w = _row_to_window(
                    "PROP_REOPEN",
                    d.isoformat(),
                    str(get_event_def("PROP_REOPEN").get("anchor_time", "17:00:00")),
                    str(get_event_def("PROP_REOPEN").get("timezone", "America/Chicago")),
                    "RULE_BASED",
                )
                if w:
                    yield w
            d += timedelta(days=1)


def iter_catalog_windows(
    repo_root: Path,
    *,
    include_seed: bool = True,
    include_rule_based: bool = True,
    start_year: int = 2018,
    end_year: int = 2025,
) -> list[CatalogWindow]:
    """All point-in-time windows for 44 types; deduped by event_id (SOURCED wins)."""
    by_id: dict[str, CatalogWindow] = {}
    priority = {"SOURCED": 3, "SEED": 2, "RULE_BASED": 1}

    cal_dirs = [
        repo_root / "packages" / "data_system" / "config" / "release_calendars",
        repo_root / "artifacts" / "calendar_proposals" / "seed_scaffold",
    ]
    for cal_dir in cal_dirs:
        if not cal_dir.is_dir():
            continue
        is_seed_dir = cal_dir.name == "seed_scaffold"
        if is_seed_dir and not include_seed:
            continue
        for path in sorted(cal_dir.glob("*.csv")):
            accept_seed = include_seed if is_seed_dir else True
            for et, rd, rt, tz, status in _iter_csv_calendar_rows(path, accept_seed=accept_seed):
                w = _row_to_window(et, rd, rt, tz, status)
                if not w:
                    continue
                yr = int(w.release_date[:4])
                if yr < start_year or yr > end_year:
                    continue
                prev = by_id.get(w.event_id)
                if prev is None or priority.get(w.row_status, 0) > priority.get(prev.row_status, 0):
                    by_id[w.event_id] = w

    if include_rule_based:
        for w in _iter_rule_based_windows(start_year, end_year):
            if w.event_id not in by_id:
                by_id[w.event_id] = w

    return sorted(by_id.values(), key=lambda x: (x.event_type, x.release_date, x.event_id))


def count_windows_by_type(windows: Iterable[CatalogWindow]) -> dict[str, int]:
    out: dict[str, int] = {et: 0 for et in catalog_event_types()}
    for w in windows:
        out[w.event_type] = out.get(w.event_type, 0) + 1
    return out


def npz_slot_coverage(
    repo_root: Path,
    windows: Iterable[CatalogWindow],
    symbols: tuple[str, ...] | None = None,
) -> tuple[int, int]:
    from data_system.src.npz_resolver import resolve_npz_for_event

    syms = symbols or default_cme_symbols()
    present = 0
    missing = 0
    for w in windows:
        for sym in syms:
            _, ok, _ = resolve_npz_for_event(repo_root, w.event_id, sym, syms)
            if ok:
                present += 1
            else:
                missing += 1
    return present, missing


def iter_missing_npz_slots(
    repo_root: Path,
    windows: Iterable[CatalogWindow],
    symbols: tuple[str, ...] | None = None,
) -> list[tuple[CatalogWindow, str]]:
    from data_system.src.npz_resolver import resolve_npz_for_event

    syms = symbols or default_cme_symbols()
    out: list[tuple[CatalogWindow, str]] = []
    for w in windows:
        for sym in syms:
            _, ok, _ = resolve_npz_for_event(repo_root, w.event_id, sym, syms)
            if not ok:
                out.append((w, sym))
    return out
