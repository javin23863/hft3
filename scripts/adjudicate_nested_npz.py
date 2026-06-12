#!/usr/bin/env python3
"""Resolve the nested <npz_root>/npz/ duplicate tree (Jun 7 mis-rooted job).

For every file in the nested dir:
  - byte-identical to the parent copy -> delete the nested copy
  - differs -> re-derive ground truth from the mbo_release slot:
      * derived matches one copy -> keep that content in the parent location,
        drop the other
      * slot not derivable or derived matches neither -> keep parent, move
        nested to <lake>/quarantine/nested_conflicts/ for manual review
Removes the nested dir when emptied.

    python scripts/adjudicate_nested_npz.py [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
for _p in [str(_REPO), str(_REPO / "packages")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from data_system.src.npz_resolver import npz_root, lake_root  # noqa: E402
from mbo_release_lane.npz_adapter import derive_npz_from_release, has_deriveable_mbo  # noqa: E402

_NAME_RE = re.compile(r"^(?P<symbol>[A-Z0-9]+\.[A-Za-z0-9.]+?)_(?P<event_id>.+?)_mbo\.npz$")


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = npz_root(_REPO)
    nested = root / "npz"
    if not nested.is_dir():
        print("no nested dir — nothing to do")
        return 0
    conflicts = lake_root(_REPO) / "quarantine" / "nested_conflicts"
    identical = resolved = quarantined = orphans = 0

    for np_file in sorted(nested.glob("*.npz")):
        parent = root / np_file.name
        if not parent.is_file():
            print(f"ORPHAN (nested-only) {np_file.name} -> promote to parent")
            if not args.dry_run:
                shutil.move(str(np_file), str(parent))
            orphans += 1
            continue
        if _sha(np_file) == _sha(parent):
            if not args.dry_run:
                np_file.unlink()
            identical += 1
            continue

        m = _NAME_RE.match(np_file.name)
        derived_hash = None
        if m and has_deriveable_mbo(_REPO, m.group("event_id"), m.group("symbol")):
            import tempfile

            with tempfile.TemporaryDirectory() as td:
                out = derive_npz_from_release(_REPO, m.group("event_id"), m.group("symbol"), npz_dir=Path(td))
                if out and out.is_file():
                    derived_hash = _sha(out)
                    if derived_hash == _sha(parent):
                        print(f"RESOLVED {np_file.name}: parent matches derived — drop nested")
                        if not args.dry_run:
                            np_file.unlink()
                        resolved += 1
                        continue
                    if derived_hash == _sha(np_file):
                        print(f"RESOLVED {np_file.name}: nested matches derived — replace parent")
                        if not args.dry_run:
                            parent.unlink()
                            shutil.move(str(np_file), str(parent))
                        resolved += 1
                        continue
        print(f"CONFLICT {np_file.name}: derived={'n/a' if derived_hash is None else 'matches neither'} — quarantine nested")
        if not args.dry_run:
            conflicts.mkdir(parents=True, exist_ok=True)
            shutil.move(str(np_file), str(conflicts / np_file.name))
        quarantined += 1

    if not args.dry_run and not any(nested.iterdir()):
        nested.rmdir()
        print("nested dir removed")
    print(f"\nidentical_deleted={identical} resolved={resolved} quarantined={quarantined} promoted_orphans={orphans} dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
