#!/usr/bin/env python3
"""Generate deterministic HftBacktest scenario manifest from VectorBT screening."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import repo_root

from backtest_pipeline.src.hft_campaign.manifest import (
    ManifestGenerationConfig,
    generate_scenario_manifest,
    write_scenario_manifest,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate HftBacktest campaign scenario manifest")
    parser.add_argument("--screening-artifact", required=True)
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--source-npz", required=True)
    parser.add_argument("--latency-model", required=True)
    parser.add_argument("--fill-queue-model", required=True)
    parser.add_argument("--candidate-ids", default="")
    parser.add_argument("--select-all-replay-eligible", action="store_true")
    parser.add_argument("--stress", default="")
    parser.add_argument("--out", required=True)
    parser.add_argument("--transitional-allowed", action="store_true")
    args = parser.parse_args()

    candidate_ids = tuple(c.strip() for c in args.candidate_ids.split(",") if c.strip())
    stress = tuple(s.strip() for s in args.stress.split(",") if s.strip())
    cfg = ManifestGenerationConfig(
        screening_artifact_path=Path(args.screening_artifact),
        repo_root=repo_root(),
        event_id=args.event_id,
        source_npz_path=Path(args.source_npz),
        latency_model_path=Path(args.latency_model),
        fill_queue_model_path=Path(args.fill_queue_model),
        candidate_ids=candidate_ids,
        select_all_replay_eligible=args.select_all_replay_eligible,
        stress_dimensions=stress,
        transitional_allowed=args.transitional_allowed,
    )
    scenarios, reasons = generate_scenario_manifest(cfg)
    write_scenario_manifest(Path(args.out), scenarios, reasons=reasons)
    if not scenarios:
        print("No scenarios generated:", reasons)
        return 1
    print(f"Wrote {len(scenarios)} scenarios to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
