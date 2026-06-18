"""HftBacktest campaign orchestrator."""

from __future__ import annotations

import json
import multiprocessing as mp
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from backtest_pipeline.src.hft_campaign.artifacts import write_json_atomic
from backtest_pipeline.src.hft_campaign.combined import build_combined_scenarios, run_combined_replay
from backtest_pipeline.src.hft_campaign.config import HftCampaignConfig, HftCampaignResult, HftScenarioResult
from backtest_pipeline.src.hft_campaign.ontology import (
    assert_campaign_ontology_prerequisites,
    load_vault_gate_receipt,
    scenarios_for_config_stages,
)
from backtest_pipeline.src.hft_campaign.profiling import CampaignMetrics, StageTimer
from backtest_pipeline.src.hft_campaign.scenario import HftReplayScenario
from backtest_pipeline.src.hft_campaign.worker import execute_scenario, HftWorkerContext


def run_hftbacktest_campaign(
    scenarios: list[HftReplayScenario],
    config: HftCampaignConfig,
) -> HftCampaignResult:
    campaign_dir = config.campaign_dir()
    campaign_dir.mkdir(parents=True, exist_ok=True)
    metrics = CampaignMetrics()
    timer = StageTimer()
    t0 = time.perf_counter()

    prereq_reasons = assert_campaign_ontology_prerequisites(config.repo_root)
    if prereq_reasons:
        summary = {
            "status": "fail",
            "campaign_id": config.campaign_id,
            "ontology_prerequisite_failures": prereq_reasons,
            "scenarios_expected": len(scenarios),
            "scenarios_completed": 0,
            "scenarios_failed": 0,
            "scenarios_cached": 0,
            "scenarios_skipped": len(scenarios),
        }
        write_json_atomic(campaign_dir / "campaign_manifest.json", summary)
        return HftCampaignResult(
            campaign_id=config.campaign_id,
            status="fail",
            scenarios_expected=len(scenarios),
            scenarios_completed=0,
            scenarios_failed=0,
            scenarios_cached=0,
            scenarios_skipped=len(scenarios),
            scenario_results=[],
            summary=summary,
            campaign_dir=campaign_dir,
        )

    vault_receipt, _ = load_vault_gate_receipt(config.repo_root)
    write_json_atomic(campaign_dir / "vault_gate_receipt.json", vault_receipt)

    runnable, stage_skipped = scenarios_for_config_stages(scenarios, config.stages)
    stage2_scenarios = [s for s in runnable if s.replay_tier in ("stage2_individual", "stage3_stress")]
    stage1_scenarios = [s for s in runnable if s.replay_tier == "stage1_minimal"]
    ordered = stage1_scenarios + stage2_scenarios

    results: list[HftScenarioResult] = []
    if config.workers <= 1 or len(ordered) <= 1:
        ctx = HftWorkerContext.create(config.repo_root, config.campaign_id, config)
        for scenario in ordered:
            result = execute_scenario(scenario, ctx, config)
            _record_result(metrics, result, ctx)
            results.append(result)
            if ctx.should_recycle(config):
                metrics.worker_restarts += 1
                ctx = HftWorkerContext.create(config.repo_root, config.campaign_id, config)
    else:
        payload = {
            "config": {
                "campaign_id": config.campaign_id,
                "repo_root": str(config.repo_root),
                "workers": config.workers,
                "stages": list(config.stages),
                "resume": config.resume,
                "stepping_mode": config.stepping_mode,
                "max_scenarios_per_worker": config.max_scenarios_per_worker,
                "max_worker_memory_mb": config.max_worker_memory_mb,
                "prepared_data_cache_entries": config.prepared_data_cache_entries,
                "feature_timeline_cache_entries": config.feature_timeline_cache_entries,
                "artifact_write_concurrency": config.artifact_write_concurrency,
                "accelerated_mode": config.accelerated_mode,
                "select_all_replay_eligible": config.select_all_replay_eligible,
                "candidate_ids": list(config.candidate_ids),
                "stress_dimensions": list(config.stress_dimensions),
                "finalist_ids": list(config.finalist_ids),
                "out_dir": str(campaign_dir),
            }
        }
        ctx_mp = mp.get_context("spawn")
        jobs = [(scenario, payload) for scenario in ordered]

        def _run_one(job: tuple[HftReplayScenario, dict[str, Any]]) -> HftScenarioResult:
            scenario, base_payload = job
            c = base_payload["config"]
            cfg = HftCampaignConfig(
                campaign_id=c["campaign_id"],
                repo_root=Path(c["repo_root"]),
                workers=1,
                stages=tuple(c.get("stages") or ()),
                resume=c.get("resume", True),
                stepping_mode=c.get("stepping_mode", "event"),
                max_scenarios_per_worker=c.get("max_scenarios_per_worker", 50),
                max_worker_memory_mb=c.get("max_worker_memory_mb", 4096),
                prepared_data_cache_entries=c.get("prepared_data_cache_entries", 8),
                feature_timeline_cache_entries=c.get("feature_timeline_cache_entries", 4),
                artifact_write_concurrency=c.get("artifact_write_concurrency", 2),
                accelerated_mode=c.get("accelerated_mode", False),
                select_all_replay_eligible=c.get("select_all_replay_eligible", False),
                candidate_ids=tuple(c.get("candidate_ids") or ()),
                stress_dimensions=tuple(c.get("stress_dimensions") or ()),
                finalist_ids=tuple(c.get("finalist_ids") or ()),
                out_dir=Path(c["out_dir"]) if c.get("out_dir") else None,
            )
            worker_ctx = HftWorkerContext.create(cfg.repo_root, cfg.campaign_id, cfg)
            return execute_scenario(scenario, worker_ctx, cfg)

        with ctx_mp.Pool(processes=config.workers) as pool:
            for result in pool.imap_unordered(_run_one, jobs):
                _record_result(metrics, result)
                results.append(result)

    combined_summary: dict[str, Any] | None = None
    summary_status_override = None
    if 4 in config.stages and config.finalist_ids:
        combined = build_combined_scenarios(stage2_scenarios, config.finalist_ids)
        combined_summary = run_combined_replay(combined, campaign_dir=campaign_dir)
        write_json_atomic(campaign_dir / "combined_replay.json", combined_summary)
        if combined_summary.get("status") not in ("pass", "skipped"):
            summary_status_override = "fail"

    elapsed = time.perf_counter() - t0
    summary = metrics.to_summary(wall_clock_s=elapsed, stage_timings=timer.to_dict())
    summary.update(
        {
            "scenarios_expected": len(scenarios),
            "scenarios_runnable": len(runnable),
            "scenarios_completed": sum(1 for r in results if r.status == "completed"),
            "scenarios_failed": sum(1 for r in results if r.status == "failed"),
            "scenarios_cached": sum(1 for r in results if r.cached),
            "scenarios_skipped": len(stage_skipped) + max(0, len(runnable) - len(results)),
            "skipped_scenario_ids": [s.scenario_id for s in stage_skipped],
            "campaign_id": config.campaign_id,
            "combined_replay": combined_summary,
        }
    )
    write_json_atomic(campaign_dir / "campaign_manifest.json", summary)

    status = "pass" if summary["scenarios_failed"] == 0 else "fail"
    if summary_status_override == "fail":
        status = "fail"
    return HftCampaignResult(
        campaign_id=config.campaign_id,
        status=status,
        scenarios_expected=len(scenarios),
        scenarios_completed=summary["scenarios_completed"],
        scenarios_failed=summary["scenarios_failed"],
        scenarios_cached=summary["scenarios_cached"],
        scenarios_skipped=summary["scenarios_skipped"],
        scenario_results=results,
        summary=summary,
        campaign_dir=campaign_dir,
    )


def load_scenarios_from_manifest(path: Path) -> list[HftReplayScenario]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [HftReplayScenario.from_dict(row) for row in payload.get("scenarios") or []]


def _record_result(
    metrics: CampaignMetrics,
    result: HftScenarioResult,
    ctx: HftWorkerContext | None = None,
) -> None:
    if result.cached:
        metrics.record_cache_hit()
    elif result.status in ("completed", "failed"):
        metrics.record_cache_miss()
    if ctx is not None:
        metrics.prepared_data_reuse_count += ctx.prepared_data_cache.hits
        metrics.feature_timeline_reuse_count += ctx.feature_timeline_cache.hits
        metrics.cache_evictions += (
            ctx.prepared_data_cache.evictions + ctx.feature_timeline_cache.evictions
        )
    elif result.cache_stats:
        metrics.prepared_data_reuse_count += int(result.cache_stats.get("prepared_data", {}).get("hits", 0))
        metrics.feature_timeline_reuse_count += int(
            result.cache_stats.get("feature_timeline", {}).get("hits", 0)
        )
        metrics.cache_evictions += int(result.cache_stats.get("prepared_data", {}).get("evictions", 0))
        metrics.cache_evictions += int(result.cache_stats.get("feature_timeline", {}).get("evictions", 0))
    if result.replay_result:
        metrics.events_processed += int(result.replay_result.get("steps", 0))
        metrics.orders_submitted += int(result.replay_result.get("order_intent_count", 0))
        fills = result.replay_result.get("fill_events") or []
        metrics.fills_observed += len(fills)
    if result.timings:
        total = sum(result.timings.values())
        metrics.scenario_latencies_s.append(total)
