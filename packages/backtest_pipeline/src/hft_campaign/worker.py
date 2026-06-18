"""Long-lived worker context and per-scenario execution."""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backtest_pipeline.src.hft_campaign.accelerated import annotate_accelerated_replay
from backtest_pipeline.src.hft_campaign.artifacts import (
    scenario_artifact_dir,
    validate_cached_scenario,
    write_failure_artifact,
    write_scenario_artifacts,
)
from backtest_pipeline.src.hft_campaign.config import HftCampaignConfig, HftScenarioResult
from backtest_pipeline.src.hft_campaign.failure import build_failure_diagnostic
from backtest_pipeline.src.hft_campaign.ontology import resolve_certification_status
from backtest_pipeline.src.hft_campaign.profiling import StageTimer
from backtest_pipeline.src.hft_campaign.scenario import HftReplayScenario
from backtest_pipeline.src.hft_campaign.source_lock import build_campaign_source_lock
from backtest_pipeline.src.hft_campaign.validation import validate_stage0_scenario
from backtest_pipeline.src.hftbacktest_realism import (
    run_minimal_official_hftbacktest_replay,
    validate_hftbacktest_data_path,
)


@dataclass
class BoundedCache:
    max_entries: int
    _data: OrderedDict[str, Any] = field(default_factory=OrderedDict)
    hits: int = 0
    misses: int = 0
    evictions: int = 0

    def get(self, key: str) -> Any | None:
        if key in self._data:
            self.hits += 1
            self._data.move_to_end(key)
            return self._data[key]
        self.misses += 1
        return None

    def put(self, key: str, value: Any) -> None:
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = value
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)
            self.evictions += 1

    def stats(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses, "evictions": self.evictions}


@dataclass
class HftWorkerContext:
    repo_root: Path
    campaign_id: str
    prepared_data_cache: BoundedCache
    feature_timeline_cache: BoundedCache
    scenarios_executed: int = 0
    hftbacktest_imported: bool = False
    package_version: str = ""
    repo_commit: str = ""

    @classmethod
    def create(cls, repo_root: Path, campaign_id: str, config: HftCampaignConfig) -> "HftWorkerContext":
        ctx = cls(
            repo_root=Path(repo_root),
            campaign_id=campaign_id,
            prepared_data_cache=BoundedCache(config.prepared_data_cache_entries),
            feature_timeline_cache=BoundedCache(config.feature_timeline_cache_entries),
            repo_commit=_git_commit(repo_root),
        )
        try:
            import hftbacktest  # noqa: F401

            ctx.hftbacktest_imported = True
        except ImportError:
            ctx.hftbacktest_imported = False
        try:
            import importlib.metadata

            ctx.package_version = importlib.metadata.version("hftbacktest")
        except Exception:
            ctx.package_version = "unknown"
        return ctx

    def should_recycle(self, config: HftCampaignConfig) -> bool:
        return self.scenarios_executed >= config.max_scenarios_per_worker


def _git_commit(repo_root: Path) -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except Exception:
        return "unknown"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _replay_execution_passed(replay_payload: dict[str, Any]) -> bool:
    if replay_payload.get("error"):
        return False
    official = str(replay_payload.get("official_hftbacktest_replay_status", "")).lower()
    if official and official not in ("pass", ""):
        return False
    lifecycle = replay_payload.get("order_lifecycle_summary")
    if isinstance(lifecycle, dict):
        lifecycle_status = str(lifecycle.get("status", "")).lower()
        if lifecycle_status in ("fail", "failed", "error"):
            return False
    return True


def _build_strategy(scenario: HftReplayScenario):
    from features_engine.src.hypotheses.registry import get_active_hypotheses
    from features_engine.src.model_registry import get_slug_for_hyp_id, resolve_model_id
    from backtest_pipeline.src.hypothesis_replay_strategy import HypothesisReplayStrategy

    slug = resolve_model_id(scenario.model_id)
    for hyp in get_active_hypotheses():
        legacy = f"HYP_{hyp.hyp_id}"
        if slug == get_slug_for_hyp_id(hyp.hyp_id) or scenario.model_id in (legacy, slug):
            return HypothesisReplayStrategy(hyp, signal_threshold=0.15)
    raise KeyError(f"Unknown model_id for replay: {scenario.model_id}")


def execute_scenario(
    scenario: HftReplayScenario,
    worker_context: HftWorkerContext,
    config: HftCampaignConfig,
) -> HftScenarioResult:
    timer = StageTimer()
    start_utc = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()
    campaign_dir = config.campaign_dir()
    out_dir = scenario_artifact_dir(campaign_dir, scenario.scenario_id)

    if config.resume:
        ok, cache_reasons = validate_cached_scenario(
            out_dir,
            scenario,
            repo_commit=worker_context.repo_commit,
            package_version=worker_context.package_version,
        )
        if ok:
            return HftScenarioResult(
                scenario_id=scenario.scenario_id,
                status="cached",
                cached=True,
                artifact_dir=out_dir,
            )

    with timer.measure("screening_artifact_validation"):
        stage0 = validate_stage0_scenario(scenario, repo_root=worker_context.repo_root)
    if not stage0.ok:
        failure = build_failure_diagnostic(
            campaign_id=config.campaign_id,
            scenario_id=scenario.scenario_id,
            candidate_id=scenario.candidate_id,
            model_id=scenario.model_id,
            symbol=scenario.symbol,
            event_id=scenario.event_id,
            worker_pid=os.getpid(),
            stage_name="stage0_input_validation",
            failure_kind="input_validation_failure",
            input_hashes={
                "screening": scenario.upstream_screening_artifact_hash,
                "prepared_data": scenario.prepared_data_hash,
            },
            effective_replay_configuration=scenario.execution_hash_payload(),
            hftbacktest_package_version=worker_context.package_version,
            code_commit=worker_context.repo_commit,
            start_timestamp_utc=start_utc,
            elapsed_s=time.perf_counter() - t0,
        )
        failure["validation_reasons"] = stage0.reasons
        write_failure_artifact(out_dir, failure)
        return HftScenarioResult(
            scenario_id=scenario.scenario_id,
            status="failed",
            failure_kind="input_validation_failure",
            failure=failure,
            artifact_dir=out_dir,
        )

    prepared_meta = worker_context.prepared_data_cache.get(scenario.prepared_data_hash)
    if prepared_meta is None:
        prepared_meta = {"path": str(scenario.prepared_data_path)}
        worker_context.prepared_data_cache.put(scenario.prepared_data_hash, prepared_meta)

    source_lock, source_lock_reasons = build_campaign_source_lock(worker_context.repo_root)
    data_validation = validate_hftbacktest_data_path(scenario.prepared_data_path)

    try:
        if scenario.replay_tier == "stage1_minimal":
            with timer.measure("replay_loop"):
                candidate_row = _candidate_row(stage0.screening_artifact, scenario.candidate_id)
                latency_model = _load_json(scenario.latency_model_path)
                fill_queue_model = _load_json(scenario.fill_queue_model_path)
                replay_payload, replay_reasons = run_minimal_official_hftbacktest_replay(
                    data_npz_path=scenario.prepared_data_path,
                    selected_id=scenario.candidate_id,
                    selected_candidate=candidate_row,
                    fill_queue_model=fill_queue_model,
                    latency_model=latency_model,
                )
                replay_payload["stepping_mode"] = scenario.stepping_mode
                replay_payload["replay_tier"] = scenario.replay_tier
                if replay_reasons:
                    raise RuntimeError(";".join(replay_reasons))
        else:
            with timer.measure("engine_construction"):
                replay_payload = _run_full_replay_session(scenario, timer)
    except Exception as exc:
        failure = build_failure_diagnostic(
            campaign_id=config.campaign_id,
            scenario_id=scenario.scenario_id,
            candidate_id=scenario.candidate_id,
            model_id=scenario.model_id,
            symbol=scenario.symbol,
            event_id=scenario.event_id,
            worker_pid=os.getpid(),
            stage_name="replay_execution",
            failure_kind="replay_engine_error",
            exc=exc,
            input_hashes={
                "screening": scenario.upstream_screening_artifact_hash,
                "prepared_data": scenario.prepared_data_hash,
            },
            effective_replay_configuration=scenario.execution_hash_payload(),
            hftbacktest_package_version=worker_context.package_version,
            code_commit=worker_context.repo_commit,
            start_timestamp_utc=start_utc,
            elapsed_s=time.perf_counter() - t0,
            cache_state={
                "prepared_data": worker_context.prepared_data_cache.stats(),
                "feature_timeline": worker_context.feature_timeline_cache.stats(),
            },
        )
        write_failure_artifact(out_dir, failure)
        return HftScenarioResult(
            scenario_id=scenario.scenario_id,
            status="failed",
            failure_kind="replay_engine_error",
            failure=failure,
            timings=timer.to_dict(),
            artifact_dir=out_dir,
        )

    if config.accelerated_mode or scenario.accelerated_mode:
        replay_payload = annotate_accelerated_replay(replay_payload)

    replay_passed = _replay_execution_passed(replay_payload)
    certification_status = resolve_certification_status(
        scenario_feature_plane=scenario.feature_plane_status,
        replay_tier=scenario.replay_tier,
        transitional_handoff=scenario.transitional_handoff,
        accelerated_mode=config.accelerated_mode or scenario.accelerated_mode,
        source_lock_reasons=source_lock_reasons,
        replay_passed=replay_passed,
        source_lock=source_lock,
    )

    replay_summary = {
        "scenario_id": scenario.scenario_id,
        "candidate_id": scenario.candidate_id,
        "status": "pass" if replay_passed else "fail",
        "stepping_mode": replay_payload.get("stepping_mode", scenario.stepping_mode),
        "replay_tier": scenario.replay_tier,
        "feature_plane_status": scenario.feature_plane_status,
        "certification_status": certification_status,
    }

    with timer.measure("artifact_writing"):
        write_scenario_artifacts(
            out_dir,
            scenario=scenario,
            replay_result=replay_payload,
            replay_summary=replay_summary,
            timings=timer.to_dict(),
            resource_usage={"worker_pid": os.getpid()},
            source_lock=source_lock,
            data_validation=data_validation,
            fill_rows=replay_payload.get("fill_events", []),
            repo_commit=worker_context.repo_commit,
            package_version=worker_context.package_version,
        )

    worker_context.scenarios_executed += 1
    elapsed = time.perf_counter() - t0
    cache_stats = {
        "prepared_data": worker_context.prepared_data_cache.stats(),
        "feature_timeline": worker_context.feature_timeline_cache.stats(),
    }
    if not replay_passed:
        return HftScenarioResult(
            scenario_id=scenario.scenario_id,
            status="failed",
            failure_kind="replay_result_failed",
            replay_result=replay_payload,
            timings=timer.to_dict(),
            resource_usage={"elapsed_s": elapsed, "worker_pid": os.getpid()},
            artifact_dir=out_dir,
            cache_stats=cache_stats,
        )
    return HftScenarioResult(
        scenario_id=scenario.scenario_id,
        status="completed",
        replay_result=replay_payload,
        timings=timer.to_dict(),
        resource_usage={"elapsed_s": elapsed, "worker_pid": os.getpid()},
        artifact_dir=out_dir,
        cache_stats=cache_stats,
    )


def _candidate_row(screening: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    for row in screening.get("promoted") or []:
        if str(row.get("candidate_id")) == candidate_id:
            return dict(row)
    return {"candidate_id": candidate_id, "symbol": "MES"}


def _run_full_replay_session(scenario: HftReplayScenario, timer: StageTimer) -> dict[str, Any]:
    from replay.replay_session import ReplaySession, ReplaySessionConfig

    with timer.measure("strategy_construction"):
        strategy = _build_strategy(scenario)
    latency_model = _load_json(scenario.latency_model_path)
    entry_ms = float(latency_model.get("order_entry_latency_ms") or latency_model.get("latency_p50_ms") or 1.0)
    cfg = ReplaySessionConfig(
        npz_path=str(scenario.prepared_data_path),
        latency_ms=entry_ms,
        step_mode=scenario.stepping_mode,
        product=scenario.symbol.split(".")[0],
        run_id=scenario.scenario_id,
        prepared_data=True,
        latency_model_path=str(scenario.latency_model_path),
        fill_queue_model_path=str(scenario.fill_queue_model_path),
    )
    with timer.measure("replay_loop"):
        result = ReplaySession(cfg, strategy).run()
    result["stepping_mode"] = scenario.stepping_mode
    result["replay_tier"] = scenario.replay_tier
    result["prepared_data_hash"] = scenario.prepared_data_hash
    return result


def worker_entry(payload: dict[str, Any]) -> dict[str, Any]:
    scenario = HftReplayScenario.from_dict(payload["scenario"])
    config = HftCampaignConfig(**payload["config"])
    ctx = HftWorkerContext.create(config.repo_root, config.campaign_id, config)
    result = execute_scenario(scenario, ctx, config)
    return {
        "scenario_id": result.scenario_id,
        "status": result.status,
        "cached": result.cached,
        "failure_kind": result.failure_kind,
        "timings": result.timings,
    }
