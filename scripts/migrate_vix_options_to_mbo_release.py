#!/usr/bin/env python3
"""Move data/vix_options/cmbp1 into unified data/mbo_release/{event_id}/VIX.OPT slots."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

LEGACY_ROOT = _REPO / "data" / "vix_options" / "cmbp1"
README_LEGACY = _REPO / "data" / "vix_options" / "README.md"


def _as_iso(value) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def migrate(*, dry_run: bool = False) -> dict:
    from data_system.src.event_data_resolver import VIX_OPT_SYMBOL
    from economic_event_universe.events_csv_builder import resolve_download_scope_windows
    from mbo_release_lane.constants import PRIORITY_DOWNLOAD_EVENT_TYPES
    from mbo_release_lane.download import filter_windows_by_event_type, resolve_download_exclusions
    from mbo_release_lane.sensor_adapter import write_minimal_vix_release_manifest
    from mbo_release_lane.storage import raw_dbn_path, release_slot_dir

    windows = resolve_download_scope_windows(_REPO, "macro_releases")
    windows = filter_windows_by_event_type(
        windows, exclude_event_types=resolve_download_exclusions()
    )
    windows = [w for w in windows if w.event_type in PRIORITY_DOWNLOAD_EVENT_TYPES]
    by_id = {w.event_id: w for w in windows}

    moved = 0
    skipped = 0
    errors: list[dict] = []

    if not LEGACY_ROOT.is_dir():
        return {"moved": 0, "skipped": 0, "note": "legacy root absent"}

    for slot in sorted(LEGACY_ROOT.iterdir()):
        if not slot.is_dir():
            continue
        event_id = slot.name
        legacy_raw = slot / "raw.dbn.zst"
        if not legacy_raw.is_file() or legacy_raw.stat().st_size == 0:
            skipped += 1
            continue

        dest_slot = release_slot_dir(_REPO, event_id, VIX_OPT_SYMBOL)
        dest_raw = raw_dbn_path(dest_slot)

        if dest_raw.is_file() and dest_raw.stat().st_size > 0:
            skipped += 1
            continue

        if dry_run:
            moved += 1
            continue

        try:
            dest_slot.mkdir(parents=True, exist_ok=True)
            shutil.move(str(legacy_raw), str(dest_raw))
            meta = slot / "slot.json"
            if meta.is_file():
                shutil.copy2(meta, dest_slot / "slot.json")

            w = by_id.get(event_id)
            if w:
                write_minimal_vix_release_manifest(
                    _REPO,
                    event_id,
                    window_start=_as_iso(w.start_utc),
                    window_end=_as_iso(w.end_utc),
                    event_count=1,
                )
            else:
                write_minimal_vix_release_manifest(
                    _REPO,
                    event_id,
                    window_start=datetime.now(timezone.utc).isoformat(),
                    window_end=datetime.now(timezone.utc).isoformat(),
                    event_count=1,
                )
            moved += 1
        except Exception as exc:
            errors.append({"event_id": event_id, "error": str(exc)})

    if not dry_run and moved > 0:
        README_LEGACY.parent.mkdir(parents=True, exist_ok=True)
        README_LEGACY.write_text(
            "VIX.OPT cmbp-1 raw files moved to data/mbo_release/{event_id}/VIX.OPT/.\n"
            "Use scripts/report_priority_lane_coverage.py for inventory.\n",
            encoding="utf-8",
        )

    return {"moved": moved, "skipped": skipped, "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    report = migrate(dry_run=args.dry_run)
    print(json.dumps(report, indent=2))
    return 0 if not report.get("errors") else 1


if __name__ == "__main__":
    raise SystemExit(main())
