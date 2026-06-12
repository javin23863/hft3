#!/usr/bin/env python3
"""Normalize the double-named NPZ files left by hot-universe batch conversions.

Pattern: {SYM}_{EVENT}_{SYM}_mbo.npz (the event_id already carried the symbol,
so the converter prefixed it again). Canonical: {SYM}_{EVENT}_mbo.npz.

  - canonical absent              -> rename
  - canonical present, same hash  -> delete the double-named duplicate
  - canonical present, different  -> move double-named to <lake>/quarantine/dup_conflicts/

    python scripts/fix_double_named_npz.py [--dry-run]
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

_DOUBLE_RE = re.compile(r"^(?P<sym>[A-Z0-9]+\.[A-Za-z0-9.]+?)_(?P<event>.+?)_(?P=sym)_mbo\.npz$")


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
    conflicts = lake_root(_REPO) / "quarantine" / "dup_conflicts"
    renamed = deleted = conflicted = 0
    for p in sorted(root.glob("*.npz")):
        m = _DOUBLE_RE.match(p.name)
        if not m:
            continue
        canonical = root / f"{m.group('sym')}_{m.group('event')}_mbo.npz"
        if not canonical.is_file():
            print(f"RENAME {p.name} -> {canonical.name}")
            if not args.dry_run:
                p.rename(canonical)
            renamed += 1
        elif _sha(p) == _sha(canonical):
            print(f"DELETE dup {p.name}")
            if not args.dry_run:
                p.unlink()
            deleted += 1
        else:
            print(f"CONFLICT {p.name} (differs from {canonical.name})")
            if not args.dry_run:
                conflicts.mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), str(conflicts / p.name))
            conflicted += 1
    print(f"\nrenamed={renamed} deleted={deleted} conflicts={conflicted} dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
