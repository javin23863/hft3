#!/usr/bin/env python3
"""Prepare content-addressed immutable replay data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from hft3_bootstrap import repo_root

from backtest_pipeline.src.hft_campaign.prepared_data import prepare_replay_data


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare HftBacktest replay data")
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--source-npz", required=True)
    parser.add_argument("--lake-manifest-hash", default="")
    parser.add_argument("--force-rebuild", action="store_true")
    args = parser.parse_args()

    prepared = prepare_replay_data(
        source_npz_path=Path(args.source_npz),
        repo_root=repo_root(),
        symbol=args.symbol,
        event_id=args.event_id,
        lake_manifest_hash=args.lake_manifest_hash,
        force_rebuild=args.force_rebuild,
    )
    print(
        json.dumps(
            {
                "prepared_data_hash": prepared.prepared_data_hash,
                "path": str(prepared.path),
                "original_row_count": prepared.original_row_count,
                "final_row_count": prepared.final_row_count,
                "removed_orphan_count": prepared.removed_orphan_count,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
