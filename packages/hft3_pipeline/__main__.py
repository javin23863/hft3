"""HFT3 Pipeline CLI — unified entry point for research-to-live lifecycle."""
import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "packages"))
sys.path.insert(0, str(REPO_ROOT / "apps"))

from hft3_pipeline.run_mode import RunContext, RunMode
from hft3_pipeline.manifest import EvidenceGrade, PipelineManifest, StageStatus
from hft3_pipeline import stages


def cmd_inventory(args):
    inv = stages.stage_inventory(REPO_ROOT)
    print(json.dumps(inv.to_dict(), indent=2))
    return 0


def cmd_status(args):
    inv = stages.stage_inventory(REPO_ROOT)
    print(f"Repo: {inv.repo_root}")
    print(f"Branch: {inv.repo_branch}")
    print(f"Commit: {inv.repo_commit}")
    print(f"\nCapabilities:")
    print(f"  vectorbt: {inv.vectorbt_available}")
    print(f"  hftbacktest: {inv.hftbacktest_available}")
    print(f"  metrics_engine: {inv.metrics_engine_available}")
    print(f"  certification_registry: {inv.certification_registry_available}")
    print(f"  trade_manager: {inv.trade_manager_available}")
    print(f"  workbench: {inv.workbench_available}")
    print(f"\nLanes ({len(inv.lanes)}):")
    for lane in inv.lanes:
        print(f"  {lane.lane_id}: {lane.status} (data={lane.data_status}, features={lane.feature_status})")
    if inv.blockers:
        print(f"\nBlockers ({len(inv.blockers)}):")
        for b in inv.blockers:
            print(f"  - {b}")
    return 0


def cmd_run(args):
    from datetime import datetime, timezone
    import uuid as _uuid
    
    run_mode = RunMode[args.run_mode.upper()] if args.run_mode else RunMode.REAL_RESEARCH
    run_id = f"RUN-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{_uuid.uuid4().hex[:8]}"
    
    run_ctx = RunContext(
        run_mode=run_mode, run_id=run_id, lane_id=args.lane, model_id=args.model,
        symbol=args.symbol, event_id=args.event, session_id=args.session_id,
        group_id=args.group_id,
    )
    rc = _run_single_model(REPO_ROOT, run_ctx)
    return rc


def _write_manifests(repo_root: Path, pipeline_manifest, vectorbt_manifest, hft_manifest):
    """Write all manifests to disk for audit trail."""
    artifacts_dir = repo_root / "artifacts" / "pipeline_runs" / pipeline_manifest.run_id
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    
    # Write pipeline manifest
    (artifacts_dir / "pipeline_manifest.json").write_text(
        json.dumps(pipeline_manifest.to_dict(), indent=2), encoding="utf-8"
    )
    
    # Write VectorBT filter manifest
    if vectorbt_manifest:
        (artifacts_dir / "vectorbt_filter_manifest.json").write_text(
            json.dumps(vectorbt_manifest.to_dict(), indent=2), encoding="utf-8"
        )
    
    # Write HFT truth manifest
    if hft_manifest:
        (artifacts_dir / "hft_truth_manifest.json").write_text(
            json.dumps(hft_manifest.to_dict(), indent=2), encoding="utf-8"
        )
    
    print(f"  Manifests written to: {artifacts_dir}")


def _discover_models_for_lane(repo_root: Path, lane_id: str, model_filter: Optional[List[str]] = None) -> List[str]:
    """Read model IDs from configs/model_search_spaces.yaml for a given lane."""
    config_path = repo_root / "configs" / "model_search_spaces.yaml"
    if not config_path.is_file():
        print(f"  ERROR: config not found at {config_path}")
        return []
    import yaml
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    models = []
    for key, space in data.get("search_spaces", {}).items():
        if space.get("lane_id") == lane_id:
            mid = space.get("model_id")
            if mid:
                models.append(mid)
    models = sorted(set(models))
    if model_filter:
        models = [m for m in models if m in model_filter]
    return models


def _run_single_model(repo_root: Path, run_ctx: RunContext, print_prefix: str = "") -> int:
    """Run stages 0-9 for one model. Returns 0 on success, 1 on failure."""
    from datetime import datetime, timezone
    import uuid as _uuid

    run_id = run_ctx.run_id or (f"RUN-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{_uuid.uuid4().hex[:8]}")
    run_ctx.run_id = run_id

    manifest = PipelineManifest(
        run_id=run_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        lane_id=run_ctx.lane_id, model_id=run_ctx.model_id, symbol=run_ctx.symbol,
        event_id=run_ctx.event_id, session_id=run_ctx.session_id, group_id=run_ctx.group_id,
        run_mode=run_ctx.run_mode.value,
    )

    # Single-event smoke detection: only FIXTURE_CI/DEBUG are smoke-only
    if run_ctx.event_id:
        if run_ctx.run_mode in (RunMode.FIXTURE_CI, RunMode.DEBUG):
            manifest.single_event_smoke = True
            manifest.evidence_grade = EvidenceGrade.SMOKE_E2E_SINGLE_EVENT.value
            manifest.blockers.append("smoke_e2e_single_event_only")

    p = print_prefix

    print(f"{p}[Stage 0] Inventory...")
    inv = stages.stage_inventory(repo_root)
    manifest.repo_commit = inv.repo_commit
    manifest.stages["inventory"] = StageStatus.PASSED
    print(f"{p}  Lanes: {len(inv.lanes)}, Models: {len(inv.model_catalog)}")

    print(f"{p}[Stage 1] Data readiness...")
    data_result = stages.stage_data_readiness(repo_root, run_ctx, inv)
    manifest.stages["data_readiness"] = StageStatus.PASSED if data_result.get("status") == "ready" else StageStatus.FAILED
    print(f"{p}  Status: {data_result.get('status')}")
    if data_result.get("status") != "ready":
        manifest.blockers.append(f"data_not_ready: {data_result.get('error')}")
        manifest.next_action = "resolve_data"
        _write_manifests(repo_root, manifest, None, None)
        print(f"{p}  BLOCKED: data not ready")
        return 1

    print(f"{p}[Stage 2] Data fingerprint...")
    feature_result = stages.stage_data_fingerprint(repo_root, run_ctx, data_result)
    manifest.stages["data_fingerprint"] = StageStatus.PASSED if feature_result.get("status") == "ready" else StageStatus.FAILED
    print(f"{p}  Status: {feature_result.get('status')}, Type: {feature_result.get('data_type')}")

    print(f"{p}[Stage 3] VectorBT filter...")
    vectorbt_manifest = stages.stage_vectorbt_filter(repo_root, run_ctx, feature_result, inv)
    manifest.vectorbt_manifest = vectorbt_manifest
    manifest.stages["vectorbt_filter"] = StageStatus.PASSED if vectorbt_manifest.top_n_forwarded > 0 else StageStatus.FAILED
    if vectorbt_manifest.backend == "numpy_fallback":
        run_ctx.synthetic_data_used = True
        manifest.synthetic_data_used = True
        manifest.reason = "vectorbt not available; using numpy fallback (not real vectorbt)"
    print(f"{p}  Tested: {vectorbt_manifest.parameters_tested}, Passed: {vectorbt_manifest.top_n_forwarded}, Backend: {vectorbt_manifest.backend}")
    if vectorbt_manifest.top_n_forwarded == 0:
        print(f"{p}  BLOCKED: no VectorBT candidates passed")
        manifest.blockers.append("no_vectorbt_candidates_passed")
        manifest.next_action = "widen_search_space"
        _write_manifests(repo_root, manifest, vectorbt_manifest, None)
        return 1

    print(f"{p}[Stage 4] HFT truth...")
    hft_manifest = stages.stage_hft_truth(repo_root, run_ctx, vectorbt_manifest, feature_result)
    manifest.hft_truth_manifest = hft_manifest
    manifest.stages["hft_truth"] = StageStatus.PASSED if hft_manifest.promotion_eligible else StageStatus.FAILED
    if hft_manifest.execution_realism.get("subsampled"):
        run_ctx.synthetic_data_used = True
        manifest.synthetic_data_used = True
        if not manifest.reason:
            manifest.reason = "HFT truth used subsampled data"
        else:
            manifest.reason += "; HFT truth used subsampled data"
    print(f"{p}  PnL: {hft_manifest.pnl}, Trades: {hft_manifest.trades}, Eligible: {hft_manifest.promotion_eligible}")
    if not hft_manifest.promotion_eligible:
        manifest.blockers.append(f"hft_not_eligible: {hft_manifest.rejection_reason}")
        manifest.next_action = hft_manifest.next_action

    print(f"{p}[Stage 5] Full metrics...")
    metrics_result = stages.stage_full_metrics(repo_root, run_ctx, hft_manifest)
    manifest.scorecard = metrics_result.get("scorecard", {})
    manifest.stages["full_metrics"] = StageStatus.PASSED
    scorecard = metrics_result.get("scorecard", {})
    print(f"{p}  Grade: {scorecard.get('overall_grade')}, Score: {scorecard.get('overall_score')}")

    print(f"{p}[Stage 6] Robustness...")
    robustness_result = stages.stage_robustness(repo_root, run_ctx, metrics_result)
    manifest.stages["robustness"] = StageStatus[robustness_result.get("status", "SKIPPED")]
    print(f"{p}  Status: {robustness_result.get('status')}")

    print(f"{p}[Stage 7] Promotion...")
    promotion_result = stages.stage_promotion(repo_root, run_ctx, hft_manifest, metrics_result, vectorbt_manifest)
    manifest.promotion_status = promotion_result.get("promotion_status", "UNKNOWN")
    manifest.stages["promotion"] = StageStatus.PASSED if manifest.promotion_status == "PROMOTED" else StageStatus.FAILED
    print(f"{p}  Status: {manifest.promotion_status}, Grade: {promotion_result.get('overall_grade')}")
    if manifest.promotion_status != "PROMOTED":
        manifest.blockers.append("promotion_failed")
        manifest.next_action = "review_metrics"

    print(f"{p}[Stage 8] Trade Manager...")
    tm_result = stages.stage_trade_manager(repo_root, run_ctx, promotion_result, metrics_result, hft_manifest)
    manifest.trade_manager_status = tm_result.get("status", "UNKNOWN")
    manifest.session_id_live = tm_result.get("session_id", "")
    manifest.stages["trade_manager"] = StageStatus.PASSED if tm_result.get("status") == "COMPLETED" else StageStatus.SKIPPED
    print(f"{p}  Status: {tm_result.get('status')}, Session: {tm_result.get('session_id', 'N/A')}")

    print(f"{p}[Stage 9] Workbench truth...")
    wb_result = stages.stage_workbench_truth(repo_root, manifest)
    manifest.stages["workbench_truth"] = StageStatus.PASSED
    print(f"{p}  Status: {wb_result.get('status')}")
    manifest.next_action = "pipeline_complete" if not manifest.blockers else "resolve_blockers"

    _write_manifests(repo_root, manifest, vectorbt_manifest, hft_manifest)

    if not manifest.blockers:
        print(f"{p}  OK: pipeline complete for {run_ctx.model_id}")
        return 0
    else:
        print(f"{p}  BLOCKERS: {', '.join(manifest.blockers)}")
        return 1


def cmd_run_all(args):
    lane = args.lane
    symbol = args.symbol
    event = args.event
    models = _discover_models_for_lane(REPO_ROOT, lane, args.models)
    if not models:
        print(f"No models found for lane '{lane}'")
        return 1

    run_mode = RunMode[args.run_mode.upper()] if args.run_mode else RunMode.REAL_RESEARCH
    print(f"Lane: {lane}, Models: {len(models)} — {', '.join(models)}")
    print(f"Symbol: {symbol or 'default'}, Event: {event or 'default'}")
    print("=" * 60)

    for idx, model_id in enumerate(models):
        print(f"\n[{idx + 1}/{len(models)}] {model_id}")
        print("-" * 40)
        run_ctx = RunContext(
            run_mode=run_mode, run_id="", lane_id=lane, model_id=model_id,
            symbol=symbol, event_id=event, session_id=args.session_id,
            group_id=args.group_id,
        )
        rc = _run_single_model(REPO_ROOT, run_ctx, print_prefix="  ")
        if rc != 0:
            print(f"\nFAIL-FAST: {model_id} failed, stopping pipeline")
            return rc
        print()

    print("=" * 60)
    print(f"All {len(models)} models completed successfully")
    return 0


def cmd_resume(args):
    print(f"resume not yet implemented. Run ID: {args.run_id}")
    return 1


def cmd_explain(args):
    print(f"explain not yet implemented. Run ID: {args.run_id}")
    return 1


def cmd_trade_manager_status(args):
    inv = stages.stage_inventory(REPO_ROOT)
    if not inv.trade_manager_available:
        print("Trade Manager: NOT AVAILABLE")
        return 1
    print("Trade Manager: AVAILABLE")
    print("  Active models: check runtime/sessions/")
    return 0


def cmd_workbench_truth(args):
    print("Workbench truth: run the pipeline to generate truth object")
    return 0


def main():
    parser = argparse.ArgumentParser(prog="hft3_pipeline", description="HFT3 Pipeline Orchestrator")
    subparsers = parser.add_subparsers(dest="command", required=True)
    p_inv = subparsers.add_parser("inventory", help="Show repo and lane inventory")
    p_status = subparsers.add_parser("status", help="Show pipeline status")
    p_run = subparsers.add_parser("run", help="Run full pipeline for a model")
    p_run.add_argument("--lane", required=True, help="Lane ID (cme_futures, equities_low_float, options_parity, crypto)")
    p_run.add_argument("--model", required=True, help="Model ID (e.g., SPREAD_BLOWOUT_RECOMPRESSION)")
    p_run.add_argument("--symbol", help="Symbol (e.g., MES.v.0)")
    p_run.add_argument("--event", help="Event ID (e.g., CPI_2024_09_11_TIGHT)")
    p_run.add_argument("--session-id", help="Session ID (for equities lane)")
    p_run.add_argument("--group-id", help="Group ID (for options lane)")
    p_run.add_argument("--run-mode", default="REAL_RESEARCH", help="Run mode (REAL_RESEARCH, PAPER_REPLAY, FIXTURE_CI, etc.)")
    p_run.add_argument("--output", help="Output path for manifest JSON")
    p_run_all = subparsers.add_parser("run-all", help="Run pipeline for all models in a lane")
    p_run_all.add_argument("--lane", required=True)
    p_run_all.add_argument("--models", nargs="*", help="Filter to specific model IDs")
    p_run_all.add_argument("--symbol", help="Symbol (e.g., MES.v.0)")
    p_run_all.add_argument("--event", help="Event ID (e.g., CPI_2024_09_11_TIGHT)")
    p_run_all.add_argument("--session-id", help="Session ID (for equities lane)")
    p_run_all.add_argument("--group-id", help="Group ID (for options lane)")
    p_run_all.add_argument("--run-mode", default="REAL_RESEARCH", help="Run mode (REAL_RESEARCH, PAPER_REPLAY, FIXTURE_CI, etc.)")
    p_resume = subparsers.add_parser("resume", help="Resume a previous run")
    p_resume.add_argument("--run-id", required=True)
    p_explain = subparsers.add_parser("explain", help="Explain a previous run")
    p_explain.add_argument("--run-id", required=True)
    p_tm = subparsers.add_parser("trade-manager-status", help="Show Trade Manager status")
    p_wb = subparsers.add_parser("workbench-truth", help="Show Workbench truth")
    args = parser.parse_args()
    if args.command == "inventory":
        return cmd_inventory(args)
    elif args.command == "status":
        return cmd_status(args)
    elif args.command == "run":
        return cmd_run(args)
    elif args.command == "run-all":
        return cmd_run_all(args)
    elif args.command == "resume":
        return cmd_resume(args)
    elif args.command == "explain":
        return cmd_explain(args)
    elif args.command == "trade-manager-status":
        return cmd_trade_manager_status(args)
    elif args.command == "workbench-truth":
        return cmd_workbench_truth(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())