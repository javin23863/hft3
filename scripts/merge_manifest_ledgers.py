#!/usr/bin/env python3
"""One-time + reusable merge of split Databento spend ledgers.

The historical ledger lived in the old data-lake clone; the live downloader
wrote a second, cwd-relative ledger in the active repo. This merges any number
of manifest.parquet files into the canonical lake ledger (HFT3_MANIFEST_PATH),
dedupes exact logical duplicates, and prints spend totals so BudgetManager
accounting can be reconciled.

    python scripts/merge_manifest_ledgers.py SRC [SRC ...] --dest C:/hft3-lake/manifest.parquet
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

DEDUP_KEYS = ["event_id", "download_time", "output_path"]


def load(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    print(f"  {path}: rows={len(df)} cost_sum=${df['cost'].sum():.4f}")
    return df


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sources", nargs="+", type=Path)
    ap.add_argument("--dest", type=Path,
                    default=Path(os.environ.get("HFT3_MANIFEST_PATH", "data/manifest.parquet")))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    frames = []
    print("Sources:")
    for src in args.sources:
        if not src.is_file():
            print(f"  {src}: MISSING — skipped")
            continue
        frames.append(load(src))
    if args.dest.is_file() and args.dest.resolve() not in [s.resolve() for s in args.sources if s.is_file()]:
        print("Dest (pre-existing, included):")
        frames.append(load(args.dest))
    if not frames:
        print("Nothing to merge.")
        return 1

    merged = pd.concat(frames, ignore_index=True)
    before = len(merged)
    keys = [k for k in DEDUP_KEYS if k in merged.columns]
    merged = merged.drop_duplicates(subset=keys, keep="first").reset_index(drop=True)
    merged = merged.sort_values("download_time").reset_index(drop=True)
    print(f"Merged: rows={before} -> {len(merged)} after dedup on {keys}")
    print(f"TOTAL SPEND: ${merged['cost'].sum():.4f}")

    if args.dry_run:
        print("Dry run — dest not written.")
        return 0

    if args.dest.is_file():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = args.dest.with_suffix(f".pre_merge_{stamp}.parquet")
        shutil.copy2(args.dest, backup)
        print(f"Backup: {backup}")
    tmp = args.dest.with_suffix(".tmp.parquet")
    merged.to_parquet(tmp, index=False)
    os.replace(tmp, args.dest)
    print(f"Wrote {args.dest} ({args.dest.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
