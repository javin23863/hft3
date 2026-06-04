"""Command line entry point for permanent latency baseline runs."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys

from .recorder import dated_jsonl_path
from .summary import build_summary, write_summary_reports
from .synthetic import SyntheticConfig, run_synthetic


def default_run_id() -> str:
    return "latbase-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Measure placement speed separately from ack latency.")
    parser.add_argument("--mode", choices=["broker", "synthetic"], default="broker")
    parser.add_argument("--repo-root", default=".", help="Repository root for data/ and reports/ outputs.")
    parser.add_argument("--run-id", default="", help="Stable run id. Defaults to timestamped latbase-*.")
    parser.add_argument("--env", default="paper", dest="environment")
    parser.add_argument("--broker", default="rithmic")
    parser.add_argument("--venue", default="", help="Venue label. Defaults to --exchange when omitted.")
    parser.add_argument("--exchange", default="", help="Exchange label used as venue when --venue is omitted.")
    parser.add_argument("--symbol", default="ES")
    parser.add_argument("--duration", type=float, default=300.0)
    parser.add_argument("--strategy", default="latency_probe", dest="strategy_id")
    parser.add_argument("--model-id", default="")
    parser.add_argument("--trade-manager-id", default="")
    parser.add_argument("--samples", type=int, default=None, help="Synthetic sample override for fast verification.")
    parser.add_argument(
        "--update-current-baseline",
        action="store_true",
        help="Write this run summary to reports/latency_baselines/current_baseline.json.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    run_id = args.run_id or default_run_id()
    reports_root = repo_root / "reports" / "latency_baselines"
    baseline_path = reports_root / "current_baseline.json"
    venue = args.venue or args.exchange or args.broker

    if args.mode != "synthetic":
        _write_broker_mode_blocker(repo_root=repo_root, run_id=run_id, args=args)
        print(
            "BROKER_MODE_REQUIRES_EXECUTION_ADAPTER: wire strategy/trade-manager/Rithmic probes "
            "to LatencyRecorder before running broker mode.",
            file=sys.stderr,
        )
        return 2

    sample_path, records = run_synthetic(
        SyntheticConfig(
            repo_root=repo_root,
            run_id=run_id,
            environment=args.environment,
            broker=args.broker,
            venue=venue,
            symbol=args.symbol,
            strategy_id=args.strategy_id,
            model_id=args.model_id or "synthetic_model",
            trade_manager_id=args.trade_manager_id or "synthetic_trade_manager",
            duration_seconds=args.duration,
            samples=args.samples,
        )
    )
    summary = build_summary(
        records,
        run_id=run_id,
        sample_path=sample_path,
        baseline_path=baseline_path,
    )
    json_path, md_path, current_path = write_summary_reports(
        summary,
        reports_root=reports_root,
        update_current_baseline=args.update_current_baseline,
    )
    print(json.dumps({"run_id": run_id, "sample_path": str(sample_path), "summary_json": str(json_path), "summary_md": str(md_path), "current_baseline": str(current_path) if current_path else ""}, indent=2))
    return 0


def _write_broker_mode_blocker(*, repo_root: Path, run_id: str, args: argparse.Namespace) -> None:
    reports_root = repo_root / "reports" / "latency_baselines"
    reports_root.mkdir(parents=True, exist_ok=True)
    sample_path = dated_jsonl_path(repo_root, run_id)
    blocker = {
        "schema_version": "latency_baseline_broker_blocker_v1",
        "run_id": run_id,
        "mode": "broker",
        "status": "blocked",
        "blocker": "BROKER_MODE_REQUIRES_EXECUTION_ADAPTER",
        "reason": "Broker mode must be wired at the real execution boundaries before it can produce placement-speed evidence.",
        "requested_environment": args.environment,
        "requested_broker": args.broker,
        "requested_venue": args.venue or args.exchange or args.broker,
        "requested_symbol": args.symbol,
        "sample_path": str(sample_path),
        "principle": "do_not_treat_ack_latency_as_placement_speed",
    }
    (reports_root / f"{run_id}_broker_blocker.json").write_text(
        json.dumps(blocker, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
