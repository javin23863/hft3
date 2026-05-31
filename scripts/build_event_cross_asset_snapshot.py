#!/usr/bin/env python3
"""Build cross-asset L3 snapshot tensor for an event (offline research)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from economic_event_universe.snapshot import DefaultSnapshotProvider
from hfc3.events.l3_event_snapshot_tensor import write_l3_event_tensor
from hft3_bootstrap import repo_root, setup_repo_paths


def main() -> int:
    setup_repo_paths()
    parser = argparse.ArgumentParser(description="Build cross-asset event snapshot tensor")
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--symbols", default="", help="Comma-separated; default from events.csv")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--offset-sec", type=int, default=None, help="Single-offset frame via provider")
    args = parser.parse_args()

    repo = repo_root()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] or None
    if args.offset_sec is not None:
        provider = DefaultSnapshotProvider(repo)
        frame = provider.collect(args.event_id, args.offset_sec, symbols or [])
        out = repo / "runtime" / "event_snapshots" / f"{args.event_id}_offset_{args.offset_sec}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"metadata": frame.metadata, "rows": frame.rows}, indent=2), encoding="utf-8")
        print(f"Wrote {out}")
        return 0

    out_dir = Path(args.output_dir) if args.output_dir else repo / "runtime" / "event_snapshots"
    parquet, meta = write_l3_event_tensor(repo, args.event_id, output_dir=out_dir, symbols=symbols)
    print(f"Wrote {parquet}")
    print(f"Wrote {meta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
