#!/usr/bin/env python3
"""Batch derive NPZ from existing raw DBN files. Scans data/mbo_release/ for
release slots with valid raw DBN but no NPZ, then calls the standard converter."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
from hft3_bootstrap import setup_repo_paths
setup_repo_paths()

from mbo_release_lane.npz_adapter import derive_npz_from_release, has_deriveable_mbo
from mbo_release_lane.storage import raw_dbn_path, release_slot_dir
from data_system.src.npz_resolver import npz_filename


def main() -> int:
    repo = _REPO
    npz_dir = repo / "data" / "npz"
    mbo_dir = repo / "data" / "mbo_release"

    if not mbo_dir.is_dir():
        print("no data/mbo_release/", file=sys.stderr)
        return 1

    # Collect all (release_id, symbol) with raw DBN
    pending: list[tuple[str, str, Path]] = []
    skipped_existing = 0
    skipped_no_dbn = 0

    for rel_dir in sorted(mbo_dir.iterdir()):
        if not rel_dir.is_dir():
            continue
        release_id = rel_dir.name
        for sym_dir in sorted(rel_dir.iterdir()):
            if not sym_dir.is_dir():
                continue
            symbol = sym_dir.name.replace("_", "/", 1).replace("_", ".", 1)
            # Check for raw DBN
            slot = release_slot_dir(repo, release_id, symbol)
            raw = raw_dbn_path(slot)
            if not raw.is_file() or raw.stat().st_size == 0:
                skipped_no_dbn += 1
                continue
            # Check NPZ existence
            npz_path = npz_dir / npz_filename(symbol, release_id)
            if npz_path.is_file() and npz_path.stat().st_size > 0:
                skipped_existing += 1
                continue
            pending.append((release_id, symbol, raw))

    print(f"scanned: {len(pending) + skipped_existing + skipped_no_dbn} slots")
    print(f"npz exists: {skipped_existing}")
    print(f"no raw dbn: {skipped_no_dbn}")
    print(f"need derive: {len(pending)}")

    derived = 0
    failed = 0
    total = len(pending)

    for i, (release_id, symbol, raw) in enumerate(pending):
        try:
            result = derive_npz_from_release(repo, release_id, symbol)
            if result:
                derived += 1
            else:
                failed += 1
        except Exception as ex:
            failed += 1
            print(f"  [{i+1}/{total}] ERROR {release_id} {symbol}: {ex}", flush=True)
        if (i + 1) % 100 == 0:
            print(f"  [{i+1}/{total}] derived={derived} failed={failed}", flush=True)

    print(f"\nDone: derived={derived} failed={failed}")
    out = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "pending": total,
        "derived": derived,
        "failed": failed,
        "skipped_existing": skipped_existing,
        "skipped_no_dbn": skipped_no_dbn,
    }
    out_path = repo / "runtime" / "data_downloads" / "derive_npz_batch.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2))
    print(f"Wrote: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())