"""Repair the NPZ lake: rename doubled-symbol files to the canonical convention.

A converter wrote some windows as ``<sym>_<event>_<sym>_mbo.npz`` (symbol doubled),
which the lake index parses to a mangled event_id that never matches events.csv —
so the real data sits invisible while the screen sees only empty/absent canonical
files. This renames each doubled file to ``<sym>_<event>_mbo.npz`` (only when no
canonical already exists, so it never clobbers good data). Dry-run by default.
"""
from __future__ import annotations

import argparse
import glob
import os
import re

_DBL = re.compile(r"^(?P<s>[A-Z]+\.v\.\d+)_(?P<rest>.+)_(?P=s)_mbo\.npz$")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Rename doubled-symbol NPZ to canonical names")
    ap.add_argument("--root", default=os.environ.get("HFT3_NPZ_ROOT", "data/npz"))
    ap.add_argument("--apply", action="store_true", help="actually rename (default: dry run)")
    a = ap.parse_args(argv)

    plan, skipped = [], 0
    for f in glob.glob(os.path.join(a.root, "*_mbo.npz")):
        m = _DBL.match(os.path.basename(f))
        if not m:
            continue
        canon = os.path.join(a.root, f"{m.group('s')}_{m.group('rest')}_mbo.npz")
        if os.path.exists(canon):
            skipped += 1  # never clobber an existing canonical
            continue
        plan.append((f, canon))

    print(f"root: {a.root}")
    print(f"doubled-name files to rename : {len(plan)}")
    print(f"skipped (canonical exists)   : {skipped}")
    for src, dst in plan[:5]:
        print("  ", os.path.basename(src), "->", os.path.basename(dst))

    if not a.apply:
        print("DRY RUN — pass --apply to rename.")
        return 0
    n = 0
    for src, dst in plan:
        os.rename(src, dst)
        n += 1
    print(f"renamed {n} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
