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
    run_p.add_argument("--model", required=True, help="Canonical slug from model_registry.yaml (python -m workbench list)")
    run_p.add_argument("--event-id", required=True)
    run_p.add_argument("--symbol", default=None, help="Research symbol e.g. MES.v.0")
    run_p.add_argument("--chi404-summary", default="runtime/latency_reports/latency_summary.json")
    run_p.add_argument("--seed", type=int, default=42)
    run_p.add_argument("--history-years", type=float, default=0.0)
    run_p.add_argument("--enforce-history-gate", action="store_true")
    run_p.add_argument("--full-sweep", action="store_true", help="Run full latency band matrix")
    run_p.add_argument(
        "--imbalance-ablation-full",
        action="store_true",
        help="Run all 8 imbalance ablation modes (slow; default is 3-mode fast sweep)",
    )

    run_p.add_argument("--composition", default=None, help="JSON file with ModelComposition")
    run_p.add_argument(
        "--defensive",
        default=None,
        help="Defensive stubs: SLUG:phase[:budget_us],... e.g. QUANTUM_SPREAD_DEFENSE:before,HAWKES_TOXIC_FLOW:during",
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
    camp_p.add_argument("--composition", default=None, help="JSON file with ModelComposition")
    camp_p.add_argument(
        "--defensive",
        default=None,
        help="Defensive stubs: MODEL:phase[:budget_us],...",
    )

    sub.add_parser("list", help="List registered models")

    aut_p = sub.add_parser(
        "autonomous",
        help="Run all configured models through the real campaign pipeline",
    )
    aut_p.add_argument("--symbol", action="append", default=None, help="Symbol to run (repeat for multiple). Default: MES.v.0")
    aut_p.add_argument("--campaign-id", default=None)
    aut_p.add_argument(
        "--trial", action="store_true", help="Smoke mode: skip WFC, allow partial NPZ"
    )
    aut_p.add_argument("--include-kinds", nargs="*", default=None)
    aut_p.add_argument("--job-filter", nargs="*", default=None)
    aut_p.add_argument("--download-missing", action="store_true")
    aut_p.add_argument(
        "--as-subprocess",
        action="store_true",
        help="Spawn the all_lanes orchestrator in a child process (so control.json is the only IPC). Default: in-process.",
    )

    verify_data_p = sub.add_parser(
        "verify-data",
        help="Fail-closed MBO NPZ preflight for a single event/symbol",
    )
    verify_data_p.add_argument("--event-id", required=True)
    verify_data_p.add_argument("--symbol", required=True, help="Research symbol e.g. NQ.v.0")
    verify_data_p.add_argument("--json", action="store_true", help="Machine-readable output")

    abl_p = sub.add_parser(
        "imbalance-ablation",
        help="Replay-backed imbalance ablation matrix (requires NPZ)",
    )
    abl_p.add_argument("--model", required=True, help="Canonical slug (python -m workbench list)")
    abl_p.add_argument(
        "--event-id",
        required=True,
        help="Macro event id from packages/data_system/config/events.csv (CPI, NFP, PROP_FLATTEN, ...)",
    )
    abl_p.add_argument("--symbol", default=None)
    abl_p.add_argument("--npz", default=None, help="Override NPZ path")
    abl_p.add_argument("--seed", type=int, default=42)
    abl_p.add_argument(
        "--full",
        action="store_true",
        help="All 8 ablation modes (default: 3-mode fast sweep)",
    )
    abl_p.add_argument("--output", default=None, help="JSON output path")

    args = parser.parse_args(argv)

    if args.command == "list":
        from workbench.src.registry.unified_registry import list_models

        for mid in list_models():
            print(mid)
        return 0

    if args.command == "autonomous":
        from workbench.src.run.all_lanes import run_all_lanes

        symbols = args.symbol or ["MES.v.0"]
        if args.as_subprocess:
            import subprocess

            cmd = [sys.executable, "-m", "workbench", "autonomous", "--symbol", *symbols]
            if args.campaign_id:
                cmd.extend(["--campaign-id", args.campaign_id])
            if args.trial:
                cmd.append("--trial")
            if args.include_kinds:
                cmd.extend(["--include-kinds", *args.include_kinds])
            if args.job_filter:
                cmd.extend(["--job-filter", *args.job_filter])
            if args.download_missing:
                cmd.append("--download-missing")
            proc = subprocess.Popen(cmd, cwd=str(_REPO))
            print(json.dumps({"pid": proc.pid, "command": cmd}, indent=2))
            return 0
        out = run_all_lanes(
            _REPO,
            symbols=symbols,
            campaign_id=args.campaign_id,
            include_kinds=tuple(args.include_kinds) if args.include_kinds else None,
            audit_grade=not args.trial,
            trial_mode=args.trial,
            download_missing=args.download_missing,
            job_filter=args.job_filter,
        )
        print(json.dumps({"artifact_dir": str(out)}, indent=2))
        return 0

    if args.command == "verify-data":
        from workbench.src.verify_data import verify_data as run_verify_data

        result = run_verify_data(_REPO, event_id=args.event_id, symbol=args.symbol)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            status = "PASS" if result.get("ok") else "FAIL"
            print(f"verify-data: {status}")
            print(f"  event_id: {result.get('event_id')}")
            print(f"  symbol: {result.get('symbol')}")
            if result.get("symbol_used"):
                print(f"  symbol_used: {result.get('symbol_used')}")
            if result.get("npz_path"):
                print(f"  npz_path: {result.get('npz_path')}")
            if not result.get("ok"):
                print(f"  error: {result.get('error', 'NPZ missing')}")
                if result.get("sync_command"):
                    print(f"  sync: {result.get('sync_command')}")
        return 0 if result.get("ok") else 1

    if args.command == "imbalance-ablation":
        from workbench.src.imbalance.ablation_runner import run_imbalance_ablation_matrix

        _results, summary = run_imbalance_ablation_matrix(
            _REPO,
            args.model,
            args.event_id,
            npz_path=Path(args.npz) if args.npz else None,
            symbol=args.symbol,
            seed=args.seed,
            ablation_full=args.full,
            fast_sweep=not args.full,
        )
        text = json.dumps(summary, indent=2)
        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
        print(text)
        return 0

    if args.command == "campaign":
        from workbench.src.run.campaign_runner import record_sim_shadow, run_campaign

        repo = _REPO
        if args.record_sim_shadow and args.campaign_id:
            record_sim_shadow(repo, args.campaign_id, args.record_sim_shadow)
            print(json.dumps({"campaign_id": args.campaign_id, "sim_shadow_status": args.record_sim_shadow}))
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
        imbalance_ablation_full=args.imbalance_ablation_full,
    )
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
