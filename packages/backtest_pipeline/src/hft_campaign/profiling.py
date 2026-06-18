"""Stage-level profiling for HftBacktest campaign runs."""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator


STAGE_NAMES = (
    "worker_initialization",
    "hftbacktest_import",
    "screening_artifact_validation",
    "candidate_replay_eligibility_validation",
    "prepared_data_lookup",
    "prepared_data_load",
    "feature_timeline_load",
    "engine_construction",
    "strategy_construction",
    "market_data_adapter_construction",
    "replay_loop",
    "strategy_evaluation",
    "order_submission",
    "order_response_processing",
    "order_state_scanning",
    "feature_synchronization",
    "fill_accounting",
    "audit_construction",
    "artifact_validation",
    "artifact_writing",
)


@dataclass
class StageTimer:
    stages: dict[str, float] = field(default_factory=dict)
    _starts: dict[str, float] = field(default_factory=dict)

    @contextmanager
    def measure(self, stage: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            self.stages[stage] = self.stages.get(stage, 0.0) + elapsed

    def to_dict(self) -> dict[str, float]:
        return dict(self.stages)


@dataclass
class CampaignMetrics:
    events_processed: int = 0
    orders_submitted: int = 0
    fills_observed: int = 0
    worker_restarts: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    cache_evictions: int = 0
    prepared_data_reuse_count: int = 0
    feature_timeline_reuse_count: int = 0
    scenario_latencies_s: list[float] = field(default_factory=list)

    def record_cache_hit(self) -> None:
        self.cache_hits += 1

    def record_cache_miss(self) -> None:
        self.cache_misses += 1

    def record_cache_eviction(self) -> None:
        self.cache_evictions += 1

    def cache_hit_rate(self) -> float:
        total = self.cache_hits + self.cache_misses
        return self.cache_hits / total if total else 0.0

    def p50_scenario_latency(self) -> float:
        if not self.scenario_latencies_s:
            return 0.0
        ordered = sorted(self.scenario_latencies_s)
        return ordered[len(ordered) // 2]

    def p95_scenario_latency(self) -> float:
        if not self.scenario_latencies_s:
            return 0.0
        ordered = sorted(self.scenario_latencies_s)
        idx = min(len(ordered) - 1, int(len(ordered) * 0.95))
        return ordered[idx]

    def to_summary(self, *, wall_clock_s: float, stage_timings: dict[str, float]) -> dict[str, Any]:
        eps = self.events_processed / wall_clock_s if wall_clock_s > 0 else 0.0
        return {
            "events_processed": self.events_processed,
            "orders_submitted": self.orders_submitted,
            "fills_observed": self.fills_observed,
            "worker_restarts": self.worker_restarts,
            "cache_hit_rate": self.cache_hit_rate(),
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "cache_evictions": self.cache_evictions,
            "prepared_data_reuse_count": self.prepared_data_reuse_count,
            "feature_timeline_reuse_count": self.feature_timeline_reuse_count,
            "events_per_second": eps,
            "p50_scenario_latency_s": self.p50_scenario_latency(),
            "p95_scenario_latency_s": self.p95_scenario_latency(),
            "time_by_stage_s": stage_timings,
            "wall_clock_s": wall_clock_s,
        }
