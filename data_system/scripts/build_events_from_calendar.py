#!/usr/bin/env python3
"""Merge sourced release calendars into data_system/config/events.csv."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

EVENTS_CSV = _REPO / "data_system" / "config" / "events.csv"
CALENDAR_DIR = _REPO / "data_system" / "config" / "release_calendars"

TEMPLATES = {
    ("CPI", "TIGHT"): {
        "window_name": "TIGHT",
        "start_offset_seconds": -30,
        "end_offset_seconds": 300,
        "symbols": "MES.v.0,MNQ.v.0,ES.v.0,NQ.v.0,ZN.v.0,ZB.v.0",
        "priority": 1,
        "effective_date": "2018-01-01",
        "notes": "CPI tight window",
    },
    ("NFP", "TIGHT"): {
        "window_name": "TIGHT",
        "start_offset_seconds": -30,
        "end_offset_seconds": 300,
        "symbols": "MES.v.0,MNQ.v.0,ES.v.0,NQ.v.0,ZN.v.0,ZB.v.0",
        "priority": 1,
        "effective_date": "2018-01-01",
        "notes": "NFP tight window",
    },
    ("PROP_FLATTEN_TOPSTEP", "MAIN"): {
        "window_name": "MAIN",
        "start_offset_seconds": -1500,
        "end_offset_seconds": 600,
        "symbols": "MES.v.0,MNQ.v.0,ES.v.0,NQ.v.0",
        "priority": 1,
        "effective_date": "2024-01-01",
        "notes": "forced-flat window",
    },
}


def _event_id(event_type: str, release_date: str, window_name: str) -> str:
    y, m, d = release_date.split("-")
    return f"{event_type}_{y}_{m}_{d}_{window_name}"


def _load_existing() -> dict[str, dict]:
    if not EVENTS_CSV.is_file():
        return {}
    with EVENTS_CSV.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return {r["event_id"]: r for r in rows}


def _calendar_rows() -> list[dict]:
    out: list[dict] = []
    for path in sorted(CALENDAR_DIR.glob("*.csv")):
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                et = row["event_type"]
                if et in ("CPI", "NFP"):
                    tmpl_key = (et, "TIGHT")
                elif et == "PROP_FLATTEN_TOPSTEP":
                    tmpl_key = (et, "MAIN")
                else:
                    continue
                tmpl = TEMPLATES[tmpl_key]
                eid = _event_id(et, row["release_date"], tmpl["window_name"])
                out.append(
                    {
                        "event_id": eid,
                        "event_type": et,
                        "release_date": row["release_date"],
                        "release_time": row["release_time"],
                        "timezone": row["timezone"],
                        "window_name": tmpl["window_name"],
                        "start_offset_seconds": tmpl["start_offset_seconds"],
                        "end_offset_seconds": tmpl["end_offset_seconds"],
                        "symbols": tmpl["symbols"],
                        "priority": tmpl["priority"],
                        "source": row["source"],
                        "source_url": row["source_url"],
                        "effective_date": tmpl["effective_date"],
                        "notes": tmpl["notes"],
                    }
                )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Build events.csv from release calendars")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    existing = _load_existing()
    merged = dict(existing)
    for row in _calendar_rows():
        merged[row["event_id"]] = row

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

    if args.dry_run:
        for r in rows:
            print(r["event_id"], r["release_date"], r["source"])
        return 0

    with EVENTS_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {EVENTS_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
