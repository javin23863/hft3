#!/usr/bin/env python3
"""Deprecated wrapper — use: python -m crypto_lane.pipeline backfill-blockspace."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "packages"))

from crypto_lane.src.ingest.mempool_pull import backfill_blockspace_from_node  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2024-01-01")
    p.add_argument("--end", default="2024-12-31")
    p.add_argument("--step-hours", type=int, default=1)
    args = p.parse_args()
    n = backfill_blockspace_from_node(
        start=args.start,
        end=args.end,
        step_hours=args.step_hours,
    )
    return 0 if n else 1


if __name__ == "__main__":
    raise SystemExit(main())
