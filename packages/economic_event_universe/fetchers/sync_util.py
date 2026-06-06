"""Shared helpers for writing SOURCED release_calendars/*.csv."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

FIELDNAMES = [
    "release_date",
    "event_type",
    "source",
    "source_url",
    "timezone",
    "release_time",
]


def write_calendar_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sorted_rows = sorted(rows, key=lambda r: (r["event_type"], r["release_date"]))
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        for row in sorted_rows:
            w.writerow({k: row[k] for k in FIELDNAMES})


def filter_year_range(
    rows: Iterable[dict],
    *,
    start_year: int,
    end_year: int,
) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        yr = int(str(row["release_date"])[:4])
        if start_year <= yr <= end_year:
            out.append(row)
    return out
