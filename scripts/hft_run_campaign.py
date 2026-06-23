#!/usr/bin/env python3
"""Run an HftBacktest replay campaign."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import repo_root

from backtest_pipeline.src.hft_campaign.config import HftCampaignConfig
from backtest_pipeline.src.hft_campaign.runner import load_scenarios_from_manifest, run_hftbacktest_campaign


def main() -> int:
    parser = argparse.ArgumentParser(description="Run HftBacktest campaign")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--stages", default="0,1,2")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--accelerated", action="store_true")
    parser.add_argument("--finalist-file", default="")
    args = parser.parse_args()

    stages = tuple(int(s.strip()) for s in args.stages.split(",") if s.strip())
    finalist_ids: tuple[str, ...] = ()
    if args.finalist_file:
        payload = json.loads(Path(args.finalist_file).read_text(encoding="utf-8"))
        finalist_ids = tuple(str(x) for x in payload.get("finalist_ids") or [])

    scenarios = load_scenarios_from_manifest(Path(args.manifest))
    if 1 in stages and 2 not in stages:
        scenarios = [s for s in scenarios if s.replay_tier == "stage1_minimal"] or scenarios

    config = HftCampaignConfig(
        campaign_id=args.campaign_id,
        repo_root=repo_root(),
        workers=max(1, args.workers),
        stages=stages,
        resume=args.resume,
        accelerated_mode=args.accelerated,
        finalist_ids=finalist_ids,
    )
    result = run_hftbacktest_campaign(scenarios, config)
    print(json.dumps(result.summary, indent=2))
    return 0 if result.status == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
