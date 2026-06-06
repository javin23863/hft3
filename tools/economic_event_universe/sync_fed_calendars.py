#!/usr/bin/env python3
"""Fetch Fed calendars from Fed.gov + FRED and write SOURCED release_calendars/*.csv."""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from collections import Counter
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import data_system_root, setup_repo_paths

setup_repo_paths()

from economic_event_universe.fetchers.env import fred_api_key
from economic_event_universe.fetchers.fed import fetch_fomc_rows
from economic_event_universe.fetchers.fed_beige import fetch_beige_rows
from economic_event_universe.fetchers.fed_speakers import fetch_speaker_rows
from economic_event_universe.fetchers.fred_fed_releases import fetch_h41_rows, fetch_indpro_rows
from economic_event_universe.walk_forward_years import backtest_year_range

FIELDNAMES = [
    "release_date",
    "event_type",
    "source",
    "source_url",
    "timezone",
    "release_time",
]

OUTPUTS: list[tuple[str, object]] = [
    ("fed_fomc.csv", fetch_fomc_rows),
    ("fed_indpro.csv", fetch_indpro_rows),
    ("fed_h41.csv", fetch_h41_rows),
    ("fed_beige_book.csv", fetch_beige_rows),
    ("fed_speakers.csv", fetch_speaker_rows),
]


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        for row in rows:
            w.writerow({k: row[k] for k in FIELDNAMES})


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync Fed SOURCED release calendars")
    parser.add_argument("--start-year", type=int, default=None)
    parser.add_argument("--end-year", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--rebuild-events", action="store_true", help="Run build_events_from_calendar.py after sync")
    args = parser.parse_args()

    start, end = backtest_year_range(_REPO)
    if args.start_year is not None:
        start = args.start_year
    if args.end_year is not None:
        end = args.end_year

    if not fred_api_key():
        print("warning: FRED_API_KEY missing; INDPRO/H41 sync will fail", file=sys.stderr)

    cal_dir = data_system_root() / "config" / "release_calendars"
    totals: Counter[str] = Counter()

    for fname, fetcher in OUTPUTS:
        try:
            rows = fetcher(start_year=start, end_year=end)  # type: ignore[call-arg]
        except Exception as exc:
            print(f"FAIL {fname}: {exc}", file=sys.stderr)
            continue
        for r in rows:
            totals[r["event_type"]] += 1
        out = cal_dir / fname
        if args.dry_run:
            print(f"dry-run {fname}: {len(rows)} rows")
        else:
            _write_csv(out, rows)
            print(f"Wrote {len(rows)} rows -> {out}")
            if fname == "fed_beige_book.csv" and not rows:
                print(
                    "warning: no beige book dates in range — check Fed.gov beigebook{year}.htm",
                    file=sys.stderr,
                )

    print(f"Types: {dict(sorted(totals.items()))} | years {start}-{end}")

    if args.rebuild_events and not args.dry_run:
        script = _REPO / "packages" / "data_system" / "scripts" / "build_events_from_calendar.py"
        subprocess.run([sys.executable, str(script)], check=True)

    return 0 if totals else 1


if __name__ == "__main__":
    raise SystemExit(main())
