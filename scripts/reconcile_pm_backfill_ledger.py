"""Reconcile the spend ledger against PM-option backfill files on disk (2026-06-13).

WHY: scripts/pull_pm_options_backfill.py shares manifest.parquet with other Databento
writers (a concurrent 6-shard download_event_tape.py job). The atomic file lock prevents
clobber but NOT starvation — under a write-burst the pull can time out acquiring the lock
AFTER a chunk has already downloaded. The chunk file lands on disk but its manifest row is
never written (and a retry hits FileExistsError, so it's marked FAILED). Data is complete;
only the ledger row is missing.

This script is the catch-all (same philosophy as manifest_io.rebuild_from_mbo_release_slots):
it walks the PM-option lake dirs, finds every .dbn.zst NOT already recorded by output_path,
prices it with metadata.get_cost, and appends a manifest row. Idempotent: re-running adds
nothing once the ledger is whole. Run AFTER the backfill END.

  python scripts/reconcile_pm_backfill_ledger.py [--dry-run]

Env: HFT3_MANIFEST_PATH=C:\\hft3-lake\\manifest.parquet ; PYTHONPATH=<shims>;<wt>;<wt>\\packages
"""
from __future__ import annotations

import os
import re
import sys
from datetime import date, datetime, timezone

import databento as db

from data_system.src.keystore import load_keys
from data_system.src.manifest_io import append_manifest_record, read_manifest

LAKE = r"C:\hft3-lake\options"
MANIFEST = os.environ.get("HFT3_MANIFEST_PATH", r"C:\hft3-lake\manifest.parquet")

# <root>_<infix>_<YYYY-MM>.dbn.zst ; infix maps to schema
_RE = re.compile(r"^(?P<root>[A-Z0-9]+)_(?P<infix>stats|def)_(?P<tag>\d{4}-\d{2})\.dbn\.zst$")
_INFIX_SCHEMA = {"stats": "statistics", "def": "definition"}


def _next_month(y: int, m: int) -> tuple[int, int]:
    return (y + 1, 1) if m == 12 else (y, m + 1)


def _iter_chunk_files():
    for sub in ("statistics", os.path.join("definitions", "pm")):
        base = os.path.join(LAKE, sub)
        if not os.path.isdir(base):
            continue
        for root_name in os.listdir(base):
            rdir = os.path.join(base, root_name)
            if not os.path.isdir(rdir):
                continue
            for fn in os.listdir(rdir):
                m = _RE.match(fn)
                if m:
                    yield os.path.join(rdir, fn), m.group("root"), _INFIX_SCHEMA[m.group("infix")], m.group("tag")


def _is_complete(path: str) -> bool:
    """Cheap integrity check: file non-empty and DBN-openable with >=1 record readable."""
    try:
        if os.path.getsize(path) == 0:
            return False
        store = db.DBNStore.from_file(path)
        for _ in store:
            return True
        return True  # empty range is a valid (recorded) outcome
    except Exception:
        return False


def main() -> int:
    dry = "--dry-run" in sys.argv
    load_keys()
    client = db.Historical(os.environ["DATABENTO_API_KEY"])

    existing = read_manifest(MANIFEST)
    recorded_paths = set()
    if not existing.empty and "output_path" in existing.columns:
        recorded_paths = {str(p) for p in existing["output_path"].tolist()}

    added = bad = skipped = 0
    cost_added = 0.0
    for path, root, schema, tag in _iter_chunk_files():
        if path in recorded_paths:
            skipped += 1
            continue
        if not _is_complete(path):
            print(f"BAD (incomplete/corrupt, delete+repull): {path}")
            bad += 1
            continue
        y, mo = int(tag[:4]), int(tag[5:7])
        ny, nm = _next_month(y, mo)
        s_utc = datetime(y, mo, 1, tzinfo=timezone.utc)
        e_utc = datetime(ny, nm, 1, tzinfo=timezone.utc)
        try:
            cost = float(client.metadata.get_cost(
                dataset="GLBX.MDP3", schema=schema, symbols=[f"{root}.OPT"],
                stype_in="parent", start=s_utc, end=e_utc))
        except Exception as ex:  # noqa: BLE001
            print(f"PRICE-FAIL {root} {schema} {tag}: {type(ex).__name__}: {ex}")
            bad += 1
            continue
        print(f"{'WOULD ADD' if dry else 'ADD'} {schema} {root} {tag} ${cost:.4f}  {path}")
        if not dry:
            append_manifest_record(MANIFEST, {
                "event_id": f"OPT_PM_{schema}_{root}_{tag}",
                "symbols": f"['{root}.OPT']",
                "requested_symbol": f"{root}.OPT",
                "resolved_symbol": f"{root}.OPT",
                "start_utc": s_utc,
                "end_utc": e_utc,
                "cost": cost,
                "output_path": path,
                "dataset": "GLBX.MDP3",
                "schema": schema,
                "stype_in": "parent",
                "download_time": datetime.now(timezone.utc),
                "reconciled_from_disk": True,
            })
        added += 1
        cost_added += cost

    print(f"\n{'[dry-run] ' if dry else ''}reconcile done: added={added} (${cost_added:.2f}) "
          f"already_recorded={skipped} bad={bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
