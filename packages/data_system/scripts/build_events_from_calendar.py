#!/usr/bin/env python3
"""Merge sourced release calendars into packages/data_system/config/events.csv."""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import data_system_root, repo_root, setup_repo_paths

setup_repo_paths()

from economic_event_universe.calendar_io import sourced_event_types_in_dir, unsourced_release_calendar_warnings
from economic_event_universe.events_csv_builder import (
    _resolve_year_range,
    is_manual_events_csv_row,
    iter_events_csv_rows,
)
from economic_event_universe.walk_forward_years import backtest_year_range

EVENTS_CSV_FIELDS = [
    "event_id",
    "event_type",
    "release_date",
    "release_time",
    "timezone",
    "window_name",
    "start_offset_seconds",
    "end_offset_seconds",
    "symbols",
    "priority",
    "source",
    "source_url",
    "effective_date",
    "notes",
]


def _load_existing(events_csv: Path) -> dict[str, dict]:
    if not events_csv.is_file():
        return {}
    with events_csv.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return {r["event_id"]: r for r in rows}


def build_merged_rows(
    *,
    include_seed: bool = False,
    start_year: int | None = None,
    end_year: int | None = None,
) -> dict[str, dict]:
    events_csv = data_system_root() / "config" / "events.csv"
    existing = _load_existing(events_csv)
    manual = {eid: row for eid, row in existing.items() if is_manual_events_csv_row(row)}

    calendar_rows = iter_events_csv_rows(
        repo_root(),
        include_seed=include_seed,
        start_year=start_year,
        end_year=end_year,
    )
    cal_dir = data_system_root() / "config" / "release_calendars"
    for warning in unsourced_release_calendar_warnings(cal_dir):
        print(f"warning: {warning}", file=sys.stderr)
    merged = dict(manual)
    for row in calendar_rows:
        merged[row["event_id"]] = row
    return merged


def main() -> int:
    parser = argparse.ArgumentParser(description="Build events.csv from release calendars")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--include-seed",
        action="store_true",
        help="Include SEED placeholder calendar rows (not for production research)",
    )
    parser.add_argument("--start-year", type=int, default=None, help="Override walk-forward start year")
    parser.add_argument("--end-year", type=int, default=None, help="Override walk-forward end year")
    args = parser.parse_args()

    events_csv = data_system_root() / "config" / "events.csv"
    existing = _load_existing(events_csv)
    merged = build_merged_rows(
        include_seed=args.include_seed,
        start_year=args.start_year,
        end_year=args.end_year,
    )

    added = sum(1 for eid in merged if eid not in existing)
    updated = sum(1 for eid, row in merged.items() if eid in existing and existing[eid] != row)
    rows = [merged[k] for k in sorted(merged.keys())]
    counts = Counter(r["event_type"] for r in rows)

    if args.dry_run:
        start, end = _resolve_year_range(repo_root(), args.start_year, args.end_year)
        print(f"dry-run: {len(rows)} total rows ({added} added, {updated} updated)")
        print(f"  year_range: {start}-{end} | include_seed={args.include_seed}")
        for et, n in sorted(counts.items()):
            print(f"  {et}: {n}")
        return 0

    with events_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=EVENTS_CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {events_csv}")
    for et, n in sorted(counts.items()):
        print(f"  {et}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
