"""Campaign configuration and result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class HftCampaignConfig:
    campaign_id: str
    repo_root: Path
    workers: int = 1
    stages: tuple[int, ...] = (0, 1, 2)
    resume: bool = True
    stepping_mode: str = "event"
    max_scenarios_per_worker: int = 50
    max_worker_memory_mb: int = 4096
    prepared_data_cache_entries: int = 8
    feature_timeline_cache_entries: int = 4
    artifact_write_concurrency: int = 2
    accelerated_mode: bool = False
    select_all_replay_eligible: bool = False
    candidate_ids: tuple[str, ...] = ()
    stress_dimensions: tuple[str, ...] = ()
    finalist_ids: tuple[str, ...] = ()
    out_dir: Path | None = None

    def campaign_dir(self) -> Path:
        if self.out_dir is not None:
            return self.out_dir
        from workbench.src.artifacts.paths import hftbacktest_campaign_dir

        return hftbacktest_campaign_dir(self.campaign_id)


@dataclass
class HftScenarioResult:
    scenario_id: str
    status: str
    cached: bool = False
    failure_kind: str | None = None
    failure: dict[str, Any] | None = None
    replay_result: dict[str, Any] | None = None
    timings: dict[str, float] = field(default_factory=dict)
    resource_usage: dict[str, Any] = field(default_factory=dict)
    artifact_dir: Path | None = None
    cache_stats: dict[str, dict[str, int]] = field(default_factory=dict)


@dataclass
class HftCampaignResult:
    campaign_id: str
    status: str
    scenarios_expected: int = 0
    scenarios_completed: int = 0
    scenarios_failed: int = 0
    scenarios_cached: int = 0
    scenarios_skipped: int = 0
    scenario_results: list[HftScenarioResult] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    campaign_dir: Path | None = None
