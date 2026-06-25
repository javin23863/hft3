#!/usr/bin/env python3
"""Build research-only point-in-time RL training rows from fs_v1 feature stores."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (_REPO_ROOT, _REPO_ROOT / "packages", _REPO_ROOT / "apps"):
    value = str(_path)
    if value not in sys.path:
        sys.path.insert(0, value)

from hft3_bootstrap import setup_repo_paths  # noqa: E402

setup_repo_paths()

from data_system.src.feature_store import feature_store_root  # noqa: E402
from research_pipeline.rl_training_data import build_rl_training_data  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=_REPO_ROOT)
    parser.add_argument("--feature-store-root", type=Path, default=None)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--event-id", action="append", required=True)
    parser.add_argument("--feature", action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--reward-horizon-rows", type=int, default=1)
    parser.add_argument("--reward-horizon-ns", type=int, default=None)
    parser.add_argument("--feature-latency-ms", type=float, default=1.0)
    parser.add_argument("--spread-cost-multiplier", type=float, default=0.05)
    parser.add_argument("--max-rows", type=int, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo_root)
    root = Path(args.feature_store_root) if args.feature_store_root else feature_store_root(repo_root)
    result = build_rl_training_data(
        repo_root=repo_root,
        feature_store_root=root,
        symbol=args.symbol,
        event_ids=args.event_id,
        feature_names=args.feature,
        output_dir=args.output_dir,
        reward_horizon_rows=args.reward_horizon_rows,
        reward_horizon_ns=args.reward_horizon_ns,
        feature_latency_ms=args.feature_latency_ms,
        spread_cost_multiplier=args.spread_cost_multiplier,
        max_rows=args.max_rows,
    )
    print(json.dumps(result.manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
