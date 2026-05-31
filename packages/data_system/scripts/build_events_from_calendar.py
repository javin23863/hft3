#!/usr/bin/env python3
"""Merge sourced release calendars into packages/data_system/config/events.csv."""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from datetime import date
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import data_system_root, setup_repo_paths

setup_repo_paths()

from economic_event_universe.holidays import apply_holiday_adjustment
from economic_event_universe.registry import event_definitions, research_ready_types
from economic_event_universe.windows import download_window, window_name_for

_SOURCED_CALENDAR_FILES = frozenset({"bls_cpi.csv", "bls_nfp.csv", "prop_flatten.csv"})


def _event_id(event_type: str, release_date: str, window_name: str) -> str:
    y, m, d = release_date.split("-")
    return f"{event_type}_{y}_{m}_{d}_{window_name}"


def _load_existing(events_csv: Path) -> dict[str, dict]:
    if not events_csv.is_file():
        return {}
    with events_csv.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return {r["event_id"]: r for r in rows}


def _template_for(event_type: str) -> dict:
    cfg = event_definitions()[event_type]
    start, end = download_window(event_type)
    return {
        "window_name": window_name_for(event_type),
        "start_offset_seconds": start,
        "end_offset_seconds": end,
        "symbols": str(cfg.get("symbol_universe", "")),
        # Mirrors context_priority for CSV audit; resolve tie-break uses YAML table only.
        "priority": int(cfg.get("context_priority", 50)),
        "effective_date": "2018-01-01",
        "notes": f"{event_type} from release calendar",
    }


def _calendar_rows(calendar_dir: Path, *, include_seed: bool = False) -> list[dict]:
    ready = set(research_ready_types())
    out: list[dict] = []
    for path in sorted(calendar_dir.glob("*.csv")):
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                et = str(row["event_type"])
                if et not in ready and not include_seed:
                    continue
                if et not in event_definitions():
                    continue
                row_status = str(row.get("row_status", "") or "").upper()
                if not row_status:
                    if path.name not in _SOURCED_CALENDAR_FILES:
                        continue
                    row_status = "SOURCED"
                if row_status == "SEED" and not include_seed:
                    continue
                tmpl = _template_for(et)
                rd = str(row["release_date"])
                adj = apply_holiday_adjustment(et, date.fromisoformat(rd))
                rd = adj.isoformat()
                rt = row.get("release_time") or event_definitions()[et].get("anchor_time", "08:30:00")
                tz = row.get("timezone") or event_definitions()[et].get("timezone", "America/New_York")
                eid = _event_id(et, rd, tmpl["window_name"])
                out.append(
                    {
                        "event_id": eid,
                        "event_type": et,
                        "release_date": rd,
                        "release_time": rt,
                        "timezone": tz,
                        "window_name": tmpl["window_name"],
                        "start_offset_seconds": tmpl["start_offset_seconds"],
                        "end_offset_seconds": tmpl["end_offset_seconds"],
                        "symbols": tmpl["symbols"],
                        "priority": tmpl["priority"],
                        "source": row.get("source", event_definitions()[et].get("agency", "")),
                        "source_url": row.get("source_url", event_definitions()[et].get("official_source_url", "")),
                        "effective_date": tmpl["effective_date"],
                        "notes": "SEED_PLACEHOLDER: replace with sourced agency date before research use"
                        if row_status == "SEED"
                        else tmpl["notes"],
                    }
                )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Build events.csv from release calendars")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--include-seed",
        action="store_true",
        help="Include SEED placeholder calendar rows (not for production research)",
    )
    args = parser.parse_args()

    events_csv = data_system_root() / "config" / "events.csv"
    calendar_dir = data_system_root() / "config" / "release_calendars"

    existing = _load_existing(events_csv)
    merged = dict(existing)
    if not args.include_seed:
        merged = {
            eid: row
            for eid, row in merged.items()
            if "SEED_PLACEHOLDER" not in str(row.get("notes", ""))
        }
    new_rows = _calendar_rows(calendar_dir, include_seed=args.include_seed)
    added = updated = 0
    for row in new_rows:
        eid = row["event_id"]
        if eid not in merged:
            added += 1
        elif merged[eid] != row:
            updated += 1
        merged[eid] = row

    fieldnames = [
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
    rows = [merged[k] for k in sorted(merged.keys())]
    counts = Counter(r["event_type"] for r in rows)

    if args.dry_run:
        print(f"dry-run: {len(rows)} total rows ({added} added, {updated} updated)")
        for et, n in sorted(counts.items()):
            print(f"  {et}: {n}")
        return 0

    with events_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {events_csv}")
    for et, n in sorted(counts.items()):
        print(f"  {et}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
