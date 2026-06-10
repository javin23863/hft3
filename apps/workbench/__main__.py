"""CLI entrypoint: python -m workbench run|campaign|list ..."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from hft3_bootstrap import setup_repo_paths

_REPO = setup_repo_paths()


def _load_repo_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(_REPO / ".env")
    except ImportError:
        pass


def main(argv: list[str] | None = None) -> int:
    _load_repo_env()
    parser = argparse.ArgumentParser(prog="workbench", description="Microstructure workbench")
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="Run model backtest on event window")
    run_p.add_argument("--model", required=True, help="Model slug (e.g. SPREAD_BLOWOUT_RECOMPRESSION) or legacy HYP_N")
    run_p.add_argument("--event-id", required=True)
    run_p.add_argument("--symbol", default=None, help="Research symbol e.g. MES.v.0")
    run_p.add_argument("--chi404-summary", default="runtime/latency_reports/latency_summary.json")
    run_p.add_argument("--seed", type=int, default=42)
    run_p.add_argument("--history-years", type=float, default=0.0)
    run_p.add_argument("--enforce-history-gate", action="store_true")
    run_p.add_argument("--full-sweep", action="store_true", help="Run full latency band matrix")

    run_p.add_argument("--composition", default=None, help="JSON file with ModelComposition")
    run_p.add_argument(
        "--defensive",
        default=None,
        help="Defensive stubs: MODEL:phase[:budget_us],... e.g. PDF_MODEL_9:before,PDF_MODEL_11:during",
    )

    camp_p = sub.add_parser("campaign", help="B4 walk-forward campaign")
    camp_p.add_argument("--model", required=True)
    camp_p.add_argument("--symbol", default="MES.v.0")
    camp_p.add_argument("--chi404-summary", default="runtime/latency_reports/latency_summary.json")
    camp_p.add_argument("--campaign-id", default=None)
    camp_p.add_argument("--seed", type=int, default=42)
    camp_p.add_argument("--enforce-history-gate", action="store_true")
    camp_p.add_argument("--full-sweep", action="store_true")
    camp_p.add_argument("--dry-run", action="store_true")
    camp_p.add_argument("--download-missing", action="store_true")
    camp_p.add_argument("--allow-partial", action="store_true")
    camp_p.add_argument("--trial", action="store_true", help="Fast UI smoke: skip WFC, partial NPZ OK")
    camp_p.add_argument("--record-sim-shadow", choices=["PASS", "FAIL"], default=None)
    camp_p.add_argument("--record-paper-shadow", choices=["PASS", "FAIL"], default=None)
    camp_p.add_argument("--composition", default=None, help="JSON file with ModelComposition")
    camp_p.add_argument(
        "--defensive",
        default=None,
        help="Defensive stubs: MODEL:phase[:budget_us],...",
    )

    sub.add_parser("list", help="List registered models")

    setup_p = sub.add_parser("setup", help="Bootstrap environment for AI or human use")
    setup_p.add_argument("--json", action="store_true", help="Machine-readable output")
    setup_p.add_argument("--rebuild-graph", action="store_true", help="Rebuild graphify-out/ if missing")

    verify_p = sub.add_parser("verify", help="Workbench preflight check — imports, UI tests, and required files")
    verify_p.add_argument("--json", action="store_true", help="Machine-readable output")

    download_p = sub.add_parser("download", help="Download missing NPZ for a model/symbol")
    download_p.add_argument("--model", required=True)
    download_p.add_argument("--symbol", default="MES.v.0")
    download_p.add_argument("--max-cost-usd", type=float, default=30.0)
    download_p.add_argument(
        "--scope",
        choices=("full_universe", "sourced_runnable", "promotion_campaign"),
        default="full_universe",
        help="Event scope for data planning/backfill.",
    )
    download_p.add_argument("--dry-run", action="store_true")
    download_p.add_argument("--json", action="store_true")

    status_p = sub.add_parser("status", help="Current state snapshot — campaigns, data, certification")
    status_p.add_argument("--json", action="store_true")

    fabric_p = sub.add_parser("feature-fabric", help="Generate catalog-backed cross-lane feature fabric artifacts")
    fabric_p.add_argument("--source", default="cme_rithmic", help="Workbench source or lane name")
    fabric_p.add_argument("--output-root", default=None, help="Optional artifact output root")
    fabric_p.add_argument("--json", action="store_true", help="Machine-readable output")

    fresh_p = sub.add_parser("fresh-start", help="Create a fresh all-lane no-leakage run boundary")
    fresh_p.add_argument("--scope", default="all-lanes", choices=("all-lanes",))
    fresh_p.add_argument("--confirm-hard-delete", action="store_true")
    fresh_p.add_argument("--run-id", default=None)
    fresh_p.add_argument("--json", action="store_true", help="Machine-readable output")

    all_lanes_p = sub.add_parser("all-lanes", help="Write the active all-lane run plan")
    all_lanes_p.add_argument("--run-id", required=True)
    all_lanes_p.add_argument("--execute", action="store_true", help="Execute models when the full executor is wired")
    all_lanes_p.add_argument("--json", action="store_true", help="Machine-readable output")

    leak_p = sub.add_parser("leakage-detect", help="Run the active-run data leakage detector")
    leak_p.add_argument("--run-id", default=None, help="Optional active all-lane run id")
    leak_p.add_argument("--json", action="store_true", help="Machine-readable output")

    ibkr_p = sub.add_parser("ibkr-endpoint", help="Check equities lane IBKR headless socket/API endpoint")
    ibkr_p.add_argument("--config", default=None, help="Optional IBKR endpoint YAML path")
    ibkr_p.add_argument("--connect", action="store_true", help="Attempt a real ibapi headless handshake")
    ibkr_p.add_argument("--start-gateway", action="store_true", help="Start the configured local TWS/IB Gateway if no socket is open")
    ibkr_p.add_argument("--startup-timeout-sec", type=float, default=20.0)
    ibkr_p.add_argument("--timeout-sec", type=float, default=2.0)
    ibkr_p.add_argument("--json", action="store_true", help="Machine-readable output")

    args = parser.parse_args(argv)

    if args.command == "list":
        from workbench.src.registry.unified_registry import list_models

        for mid in list_models():
            print(mid)
        return 0

    if args.command == "setup":
        from workbench.src.setup import setup as run_setup
        from workbench.src.json_output import emit_json

        result = run_setup(_REPO, rebuild_graph_flag=args.rebuild_graph)
        if getattr(args, "json", False):
            emit_json(result)
        else:
            print(f"Python: {result['python']['python']} {'OK' if result['python']['python_ok'] else 'FAIL'}")
            print(f"Dependencies: {'OK' if result['dependencies']['all_present'] else 'MISSING ' + ', '.join(result['dependencies']['missing'])}")
            print(f"Environment: {'OK' if result['env']['env_ok'] else 'INCOMPLETE'}")
            print(f"NPZ data: {result['npz']['npz_count']} files ({result['npz']['npz_total_size_mb']} MB)")
            print(f"Graphify: {'OK' if result['graphify']['graph_present'] else 'MISSING — run with --rebuild-graph'}")
            if result.get("graph_rebuild"):
                print(f"Graph rebuild: {'OK' if result['graph_rebuild']['rebuilt'] else 'FAILED'}")
            core = "PASS" if result["all_ok"] else "FAIL"
            data = "OK" if result["npz"]["npz_count"] > 0 else "MISSING"
            graph = "OK" if result["graphify"]["graph_present"] else "MISSING"
            print(f"\nCore (python+deps+env): {core}  |  NPZ data: {data}  |  Graph: {graph}")
        return 0 if result["all_ok"] else 1

    if args.command == "verify":
        from workbench.src.verify import verify as run_verify
        from workbench.src.json_output import emit_json

        result = run_verify(_REPO)
        if getattr(args, "json", False):
            emit_json(result)
        else:
            print(f"Grader tests: {'PASS' if result['tests']['passed'] else 'FAIL'}")
            for f in result["files"]:
                print(f"  {f['label']}: {'OK' if f['exists'] else 'MISSING'}")
            print(f"\nOverall: {'PASS' if result['all_ok'] else 'FAIL'}")
        return 0 if result["all_ok"] else 1

    if args.command == "download":
        from apps.workbench.scripts.backfill_catalog import main as backfill_main

        argv = [
            "--model",
            args.model,
            "--symbol",
            args.symbol,
            "--max-cost-usd",
            str(args.max_cost_usd),
            "--scope",
            args.scope,
        ]
        if args.dry_run:
            argv.append("--dry-run")
        else:
            argv.append("--download-missing")
        if getattr(args, "json", False):
            argv.append("--json")
        return backfill_main(argv)

    if args.command == "status":
        from workbench.src.json_output import emit_json
        from workbench.src.setup import scan_npz, check_graphify
        from workbench.src.registry.unified_registry import list_models

        npz = scan_npz(_REPO)
        graph = check_graphify(_REPO)
        models = list_models()
        promo_dir = _REPO / "research_cards" / "promotion"
        promoted = list(promo_dir.glob("*.json")) if promo_dir.is_dir() else []
        result = {
            "models_registered": len(models),
            "npz_files": npz["npz_count"],
            "npz_total_mb": npz["npz_total_size_mb"],
            "graph_ready": graph["graph_present"],
            "promoted_candidates": len(promoted),
        }
        if getattr(args, "json", False):
            emit_json(result)
        else:
            for k, v in result.items():
                print(f"{k}: {v}")
        return 0

    if args.command == "feature-fabric":
        from workbench.src.run.feature_fabric import ensure_catalog_feature_fabric, source_to_lane

        result = ensure_catalog_feature_fabric(
            _REPO,
            source_to_lane(args.source),
            output_root=Path(args.output_root) if args.output_root else None,
        )
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"Feature fabric: {result.get('status')}")
            print(f"Rows: {result.get('row_count', 0)}")
            print(f"Rejected: {result.get('rejected_count', 0)}")
            for label, path in (result.get("artifact_paths") or {}).items():
                print(f"  {label}: {path}")
        return 0 if result.get("status") == "PASS" else 1

    if args.command == "fresh-start":
        from workbench.src.run.fresh_start import FreshStartError, fresh_start

        try:
            result = fresh_start(
                _REPO,
                scope=args.scope,
                confirm_hard_delete=args.confirm_hard_delete,
                run_id=args.run_id,
            )
        except FreshStartError as exc:
            result = {"status": "FAIL", "error": str(exc)}
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(f"Fresh start failed: {exc}")
            return 1
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"Fresh all-lane run: {result['run_id']}")
            print(f"Deleted generated paths: {result['deleted_count']}")
            print(f"Active manifest: {result['active_run']}")
        return 0

    if args.command == "all-lanes":
        from workbench.src.run.all_lanes import run_all_lanes

        try:
            result = run_all_lanes(_REPO, args.run_id, execute=args.execute)
        except NotImplementedError as exc:
            result = {"status": "FAIL", "error": str(exc), "run_id": args.run_id}
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(f"All-lane run failed: {exc}")
            return 1
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"All-lane plan: {result['run_id']}")
            print(f"Planned models: {result['planned_model_count']}")
            print(f"Run dir: {result['run_dir']}")
            print(f"Leakage detection: {result['leakage_detection_status']}")
            print(f"Leakage report: {result['leakage_detection_path']}")
        return 0 if result.get("status") == "PASS" else 1

    if args.command == "leakage-detect":
        from workbench.src.run.leakage_detector import run_leakage_detection

        result = run_leakage_detection(_REPO, run_id=args.run_id)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"Leakage detection: {result['status']}")
            print(f"Run ID: {result.get('run_id', '')}")
            for blocker in result.get("blocking", []):
                print(f"  {blocker.get('gate', '')}: {blocker.get('reason', '')}")
            paths = result.get("artifact_paths") or {}
            if paths.get("json"):
                print(f"Report: {paths['json']}")
        return 0 if result.get("status") == "PASS" else 1

    if args.command == "ibkr-endpoint":
        from equities_lane.src.ibkr_endpoint import endpoint_status

        result = endpoint_status(
            _REPO,
            config_path=Path(args.config) if args.config else None,
            connect=args.connect,
            start_gateway=args.start_gateway,
            startup_timeout_sec=args.startup_timeout_sec,
            timeout_sec=args.timeout_sec,
            write_status=True,
        )
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"IBKR endpoint: {result.get('status')}")
            print(f"Profile: {result.get('profile')}")
            print(f"Socket: {result.get('host')}:{result.get('port')}")
            print(f"API: {(result.get('api') or {}).get('api_client_status')}")
            print(f"Status artifact: {result.get('runtime_status_path')}")
            for gate in result.get("blocking_gates") or []:
                print(f"  BLOCKING {gate.get('gate')}: {gate.get('reason')}")
        return 0 if result.get("status") in {"READY_TO_CONNECT", "CONNECTED"} else 1

    if args.command == "campaign":
        from workbench.src.run.campaign_runner import record_paper_shadow, record_sim_shadow, run_campaign

        repo = _REPO
        if args.record_sim_shadow and args.campaign_id:
            record_sim_shadow(repo, args.campaign_id, args.record_sim_shadow)
            print(json.dumps({"campaign_id": args.campaign_id, "sim_shadow_status": args.record_sim_shadow}))
            return 0

        if args.record_paper_shadow and args.campaign_id:
            record_paper_shadow(repo, args.campaign_id, args.record_paper_shadow)
            print(json.dumps({"campaign_id": args.campaign_id, "paper_shadow_status": args.record_paper_shadow}))
            return 0

        chi404 = repo / args.chi404_summary
        audit = args.enforce_history_gate or args.full_sweep or True
        from workbench.src.run.composition_cli import load_composition

        composition = load_composition(
            args.model,
            composition_path=Path(args.composition) if args.composition else None,
            defensive_spec=args.defensive,
        )
        result = run_campaign(
            repo,
            args.model,
            args.symbol,
            chi404_summary=chi404 if chi404.is_file() else None,
            seed=args.seed,
            audit_grade=audit,
            dry_run=args.dry_run,
            download_missing=args.download_missing,
            allow_partial=args.allow_partial,
            trial_mode=args.trial,
            campaign_id=args.campaign_id,
            composition=composition,
        )
        print(json.dumps(
            {
                "campaign_id": result.campaign_id,
                "status": result.status,
                "artifact_dir": result.artifact_dir,
                "periods": [
                    {"name": p.name, "gate_pass": p.gate_pass, "expectancy": p.expectancy}
                    for p in result.periods
                ],
            },
            indent=2,
        ))
        return 0 if result.status in {"PASS", "DRY_RUN"} else 1

    if args.command != "run":
        parser.print_help()
        return 1

    from workbench.src.run.composition_cli import load_composition
    from workbench.src.run.engine import WorkbenchEngine

    repo = _REPO
    engine = WorkbenchEngine(repo)
    chi404 = repo / args.chi404_summary
    composition = load_composition(
        args.model,
        composition_path=Path(args.composition) if args.composition else None,
        defensive_spec=args.defensive,
    )
    out = engine.run(
        args.model,
        args.event_id,
        symbol=args.symbol,
        chi404_summary=chi404 if chi404.is_file() else None,
        seed=args.seed,
        history_years_available=args.history_years,
        skip_history_gate=not args.enforce_history_gate,
        fast_sweep=not args.full_sweep,
        composition=composition,
    )
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
