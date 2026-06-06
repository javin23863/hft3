#!/usr/bin/env python3
"""Sync all agency release calendars → SOURCED release_calendars/*.csv."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import data_system_root, setup_repo_paths

setup_repo_paths()

from economic_event_universe.fetchers.adp import fetch_all_adp_rows
from economic_event_universe.fetchers.baker_hughes import fetch_all_baker_hughes_rows
from economic_event_universe.fetchers.bea import fetch_all_bea_rows
from economic_event_universe.fetchers.bls import fetch_all_bls_rows
from economic_event_universe.fetchers.census import fetch_all_census_rows
from economic_event_universe.fetchers.dol_claims import fetch_all_dol_rows
from economic_event_universe.fetchers.eia import fetch_all_eia_rows
from economic_event_universe.fetchers.env import fred_api_key
from economic_event_universe.fetchers.fed import fetch_fomc_rows
from economic_event_universe.fetchers.fed_beige import fetch_beige_rows
from economic_event_universe.fetchers.fed_speakers import fetch_speaker_rows
from economic_event_universe.fetchers.fred_fed_releases import fetch_h41_rows, fetch_indpro_rows
from economic_event_universe.fetchers.ism import fetch_all_ism_rows
from economic_event_universe.fetchers.nar import fetch_all_nar_rows
from economic_event_universe.fetchers.sync_util import write_calendar_csv
from economic_event_universe.fetchers.treasury import fetch_all_treasury_rows
from economic_event_universe.walk_forward_years import backtest_year_range


def _sync_all(*, start_year: int, end_year: int) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}

    # Fed
    for fname, fetcher in (
        ("fed_fomc.csv", fetch_fomc_rows),
        ("fed_indpro.csv", fetch_indpro_rows),
        ("fed_h41.csv", fetch_h41_rows),
        ("fed_beige_book.csv", fetch_beige_rows),
        ("fed_speakers.csv", fetch_speaker_rows),
    ):
        try:
            out[fname] = fetcher(start_year=start_year, end_year=end_year)  # type: ignore[call-arg]
        except Exception as exc:
            print(f"warning: {fname}: {exc}", file=sys.stderr)

    for fetcher in (
        fetch_all_bls_rows,
        fetch_all_bea_rows,
        fetch_all_census_rows,
        fetch_all_ism_rows,
        fetch_all_dol_rows,
        fetch_all_adp_rows,
        fetch_all_nar_rows,
        fetch_all_eia_rows,
        fetch_all_baker_hughes_rows,
        fetch_all_treasury_rows,
    ):
        try:
            out.update(fetcher(start_year=start_year, end_year=end_year))
        except Exception as exc:
            print(f"warning: {fetcher.__name__}: {exc}", file=sys.stderr)

    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync all SOURCED macro release calendars")
    parser.add_argument("--start-year", type=int, default=None)
    parser.add_argument("--end-year", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--rebuild-events",
        action="store_true",
        help="Run build_events_from_calendar.py with rule-based types after sync",
    )
    args = parser.parse_args()

    start, end = backtest_year_range(_REPO)
    if args.start_year is not None:
        start = args.start_year
    if args.end_year is not None:
        end = args.end_year

    if not fred_api_key():
        print("warning: FRED_API_KEY missing; FRED bootstrap rows will be skipped", file=sys.stderr)

    cal_dir = data_system_root() / "config" / "release_calendars"
    files = _sync_all(start_year=start, end_year=end)
    totals: Counter[str] = Counter()

    for fname, rows in sorted(files.items()):
        for r in rows:
            totals[r["event_type"]] += 1
        path = cal_dir / fname
        if args.dry_run:
            print(f"dry-run {fname}: {len(rows)} rows")
        else:
            write_calendar_csv(path, rows)
            print(f"Wrote {len(rows)} rows -> {path}")

    print(f"Types: {len(totals)} event types, {sum(totals.values())} rows | years {start}-{end}")
    for et, n in sorted(totals.items()):
        print(f"  {et}: {n}")

    if args.rebuild_events and not args.dry_run:
        script = _REPO / "packages" / "data_system" / "scripts" / "build_events_from_calendar.py"
        subprocess.run([sys.executable, str(script)], check=True)

    return 0 if totals else 1


if __name__ == "__main__":
    raise SystemExit(main())
