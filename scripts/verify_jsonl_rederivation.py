#!/usr/bin/env python3
"""Gate for the events.jsonl purge: prove jsonl is exactly re-derivable.

Samples N random mbo_release slots that have raw.dbn.zst + events.jsonl +
hashes.json + release_event_path.json, then for each:
  1. confirms the existing events.jsonl matches hashes.json's
     normalized_events_sha256 (on-disk integrity), and
  2. re-derives events from raw.dbn.zst via mbo_release_lane's parser and
     confirms the regenerated jsonl hashes identically.
All N must double-match before the purge is allowed.

    python scripts/verify_jsonl_rederivation.py [--n 5] [--seed 42]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
for _p in [str(_REPO), str(_REPO / "packages")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mbo_release_lane.databento_mbo_parser import parse_databento_mbo_file  # noqa: E402
from mbo_release_lane.hashing import sha256_file  # noqa: E402
from mbo_release_lane.storage import mbo_release_root, write_events_jsonl  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    root = mbo_release_root(_REPO)
    print(f"mbo_release root: {root}")
    candidates = []
    for hashes in root.glob("*/*/hashes.json"):
        slot = hashes.parent
        if all((slot / f).is_file() for f in ("raw.dbn.zst", "events.jsonl", "release_event_path.json")):
            candidates.append(slot)
    print(f"complete slots: {len(candidates)}")
    if len(candidates) < args.n:
        print("FAIL: not enough complete slots")
        return 1

    random.seed(args.seed)
    sample = random.sample(candidates, args.n)
    failures = 0
    for slot in sample:
        rec = json.loads((slot / "hashes.json").read_text(encoding="utf-8"))
        recorded = rec.get("normalized_events_sha256")
        meta = json.loads((slot / "release_event_path.json").read_text(encoding="utf-8"))["release_event_path"]
        tag = f"{slot.parent.name}/{slot.name}"

        on_disk = sha256_file(slot / "events.jsonl")
        disk_ok = on_disk == recorded

        events = parse_databento_mbo_file(
            slot / "raw.dbn.zst",
            release_id=meta["release_id"],
            symbol=meta["symbol"],
            dataset_id=meta["dataset_id"],
        )
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / "events.jsonl"
            write_events_jsonl(events, tmp)
            rederived = sha256_file(tmp)
        rederive_ok = rederived == recorded

        status = "OK" if (disk_ok and rederive_ok) else "MISMATCH"
        if status != "OK":
            failures += 1
        print(f"{status}  {tag}  disk={'match' if disk_ok else 'DIFF'} rederive={'match' if rederive_ok else 'DIFF'} events={len(events)}")

    if failures:
        print(f"GATE FAILED: {failures}/{args.n} mismatched — DO NOT PURGE")
        return 1
    print(f"GATE PASSED: {args.n}/{args.n} slots double-matched — events.jsonl purge is safe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
