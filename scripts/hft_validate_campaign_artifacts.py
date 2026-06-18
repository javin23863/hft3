#!/usr/bin/env python3
"""Validate HftBacktest campaign artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from hft3_bootstrap import repo_root

from backtest_pipeline.src.hft_campaign.artifacts import validate_cached_scenario
from backtest_pipeline.src.hft_campaign.runner import load_scenarios_from_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate HftBacktest campaign artifacts")
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()

    root = repo_root()
    campaign_dir = root / "artifacts" / "hftbacktest_campaigns" / args.campaign_id
    scenarios = load_scenarios_from_manifest(Path(args.manifest))
    failures = []
    for scenario in scenarios:
        scenario_dir = campaign_dir / "scenarios" / scenario.scenario_id
        ok, reasons = validate_cached_scenario(scenario_dir, scenario, repo_commit="", package_version="")
        if not ok:
            failures.append({"scenario_id": scenario.scenario_id, "reasons": reasons})
    print(json.dumps({"failures": failures}, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
