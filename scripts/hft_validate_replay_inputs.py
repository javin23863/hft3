#!/usr/bin/env python3
"""Validate HftBacktest replay inputs (Stage 0)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import repo_root

from backtest_pipeline.src.hft_campaign.manifest import (
    ManifestGenerationConfig,
    generate_scenario_manifest,
)
from backtest_pipeline.src.hft_campaign.validation import validate_stage0_scenario


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate HftBacktest replay inputs")
    parser.add_argument("--screening-artifact", required=True)
    parser.add_argument("--candidate-ids", default="")
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--source-npz", required=True)
    parser.add_argument("--latency-model", required=True)
    parser.add_argument("--fill-queue-model", required=True)
    parser.add_argument("--transitional-allowed", action="store_true")
    args = parser.parse_args()

    root = repo_root()
    candidate_ids = tuple(c.strip() for c in args.candidate_ids.split(",") if c.strip())
    cfg = ManifestGenerationConfig(
        screening_artifact_path=Path(args.screening_artifact),
        repo_root=root,
        event_id=args.event_id,
        source_npz_path=Path(args.source_npz),
        latency_model_path=Path(args.latency_model),
        fill_queue_model_path=Path(args.fill_queue_model),
        candidate_ids=candidate_ids,
        select_all_replay_eligible=not candidate_ids,
        transitional_allowed=args.transitional_allowed,
    )
    scenarios, reasons = generate_scenario_manifest(cfg)
    if reasons:
        print(json.dumps({"validation_reasons": reasons}, indent=2))
    if not scenarios:
        return 1
    failures = []
    for scenario in scenarios:
        result = validate_stage0_scenario(scenario, repo_root=root)
        if not result.ok:
            failures.append({"scenario_id": scenario.scenario_id, "reasons": result.reasons})
    print(json.dumps({"scenarios": len(scenarios), "failures": failures}, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
