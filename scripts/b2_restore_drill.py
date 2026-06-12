#!/usr/bin/env python3
"""B2 restore drill: prove the archive round-trips bit-exact.

Samples N random records from the lake catalog (<npz_root>/manifest.json),
downloads each file from the B2 mirror via rclone into a temp dir, and
sha256-verifies against the catalog hash. All N must match.

    python scripts/b2_restore_drill.py [--n 50] [--seed 1] [--remote hft3-b2:Hft3repo/lake/npz]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
for _p in [str(_REPO), str(_REPO / "packages")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from data_system.src.npz_resolver import npz_root  # noqa: E402

RCLONE = os.path.expandvars(
    r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Rclone.Rclone_Microsoft.Winget.Source_8wekyb3d8bbwe\rclone-v1.74.3-windows-amd64\rclone.exe"
)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--remote", default="hft3-b2:Hft3repo/lake/npz")
    args = ap.parse_args()

    catalog = json.loads((npz_root(_REPO) / "manifest.json").read_text(encoding="utf-8"))
    random.seed(args.seed)
    sample = random.sample(catalog, args.n)

    fails = 0
    with tempfile.TemporaryDirectory() as td:
        for i, rec in enumerate(sample, 1):
            name = Path(rec["npz_path"]).name
            dest = Path(td) / name
            r = subprocess.run([RCLONE, "copyto", f"{args.remote}/{name}", str(dest)],
                               capture_output=True, text=True, timeout=300)
            if r.returncode != 0 or not dest.is_file():
                print(f"FAIL  [{i}/{args.n}] {name}: download failed rc={r.returncode} {r.stderr.strip()[:120]}")
                fails += 1
                continue
            got = sha256(dest)
            ok = got == rec["sha256"]
            print(f"{'OK  ' if ok else 'FAIL'}  [{i}/{args.n}] {name}")
            if not ok:
                fails += 1
            dest.unlink(missing_ok=True)

    if fails:
        print(f"RESTORE DRILL FAILED: {fails}/{args.n}")
        return 1
    print(f"RESTORE DRILL PASSED: {args.n}/{args.n} bit-exact from B2")
    return 0


if __name__ == "__main__":
    sys.exit(main())
