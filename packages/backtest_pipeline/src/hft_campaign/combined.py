"""Stage 4 finalist-only combined replay."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backtest_pipeline.src.hft_campaign.scenario import HftReplayScenario


@dataclass
class CombinedReplaySpec:
    campaign_id: str
    finalist_ids: tuple[str, str, ...]
    shared_account: bool = True
    artifact_class: str = "combined_replay"


def build_combined_scenarios(
    individual_scenarios: list[HftReplayScenario],
    finalist_ids: tuple[str, str, ...],
) -> list[HftReplayScenario]:
    """Build Stage-4 combined scenarios from finalist individual scenarios."""
    by_candidate = {s.candidate_id: s for s in individual_scenarios}
    combined: list[HftReplayScenario] = []
    for candidate_id in finalist_ids:
        base = by_candidate.get(candidate_id)
        if base is None:
            continue
        payload = base.to_dict()
        payload["replay_tier"] = "stage4_combined"
        payload["replay_mode"] = "combined_portfolio"
        payload["scenario_id"] = ""
        combined.append(HftReplayScenario.from_dict(payload))
    return combined


def run_combined_replay(
    scenarios: list[HftReplayScenario],
    *,
    campaign_dir: Path,
) -> dict[str, Any]:
    """Run shared-account combined replay for finalists.

    Combined replay intentionally shares capital/inventory across candidates.
    It must never substitute for individual Stage-2 evidence.
    """
    if len(scenarios) < 2:
        return {
            "status": "skipped",
            "reason": "combined_replay_requires_at_least_two_finalists",
            "artifact_class": "combined_replay",
        }

    out_dir = Path(campaign_dir) / "combined_replay"
    out_dir.mkdir(parents=True, exist_ok=True)
    return {
        "status": "fail",
        "failure_kind": "combined_replay_engine_not_implemented",
        "artifact_class": "combined_replay",
        "finalist_ids": [s.candidate_id for s in scenarios],
        "combined_replay_dir": str(out_dir),
        "note": "Stage-4 shared-account combined replay is not yet wired; campaign fails closed",
    }
