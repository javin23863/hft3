"""CLI entrypoint: python -m workbench run|campaign|list ..."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


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
    run_p.add_argument("--model", required=True, help="HYP_N or PDF_MODEL_N")
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
    camp_p.add_argument("--composition", default=None, help="JSON file with ModelComposition")
    camp_p.add_argument(
        "--defensive",
        default=None,
        help="Defensive stubs: MODEL:phase[:budget_us],...",
    )

    sub.add_parser("list", help="List registered models")

    args = parser.parse_args(argv)

    if args.command == "list":
        from workbench.src.registry.unified_registry import list_models

        for mid in list_models():
            print(mid)
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
    )
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
