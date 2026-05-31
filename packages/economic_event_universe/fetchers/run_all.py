"""Run all propose-only fetchers."""

from __future__ import annotations

import argparse

from economic_event_universe.fetchers import bea, bls, census, fed, ism
from hft3_bootstrap import setup_repo_paths


def main() -> int:
    setup_repo_paths()
    parser = argparse.ArgumentParser(prog="economic_event_universe.fetchers.run_all")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--write", action="store_true", help="Write proposal JSON (still not release_calendars/)")
    args = parser.parse_args()
    dry = not args.write
    total = 0
    for mod in (bls, bea, fed, census, ism):
        rows = mod.propose(dry_run=dry)
        print(f"{mod.__name__.split('.')[-1]}: {len(rows)} proposed rows")
        total += len(rows)
    print(f"total proposed rows: {total} (dry_run={dry})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
