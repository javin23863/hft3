#!/usr/bin/env python3
"""Train a bounded research-only deep RL policy artifact."""

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

from research_pipeline.rl_agents import (  # noqa: E402
    train_deep_rl_policy_artifact,
    write_rl_deep_policy_artifact,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-data", type=Path, required=True)
    parser.add_argument("--feature", action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cuda")
    parser.add_argument("--steps", type=int, default=1000)
    parser.add_argument("--batch-size", type=int, default=4096)
    parser.add_argument("--hidden-dim", type=int, default=64)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--eval-fraction", type=float, default=0.2)
    parser.add_argument("--max-rows", type=int, default=1_000_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--resume-checkpoint", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    artifact = train_deep_rl_policy_artifact(
        training_data_path=args.training_data,
        feature_names=args.feature,
        output_dir=args.output_dir,
        device=args.device,
        seed=args.seed,
        max_rows=args.max_rows,
        steps=args.steps,
        batch_size=args.batch_size,
        hidden_dim=args.hidden_dim,
        learning_rate=args.learning_rate,
        eval_fraction=args.eval_fraction,
        resume_checkpoint=args.resume_checkpoint,
    )
    write_rl_deep_policy_artifact(args.output_dir / "deep_rl_policy_artifact.json", artifact)
    print(json.dumps(artifact, indent=2, sort_keys=True))
    return 0 if artifact["status"] == "trained_research_only" else 2


if __name__ == "__main__":
    raise SystemExit(main())
