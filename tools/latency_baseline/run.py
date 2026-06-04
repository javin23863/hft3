"""Command line entry point for permanent latency baseline runs."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path

from .recorder import dated_jsonl_path
from .summary import build_summary, write_summary_reports
from .synthetic import SyntheticConfig, run_synthetic


def default_run_id() -> str:
    return "latbase-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Measure placement speed separately from ack latency. "
            "Python is synthetic/reporting only; real broker probes are native C++."
        )
    )
    parser.add_argument("--mode", choices=["broker", "synthetic"], default="synthetic")
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
    parser.add_argument(
        "--samples",
        type=int,
        default=None,
        help="Sample count override for synthetic mode. Real broker sample counts are set on the native C++ probe.",
    )
    parser.add_argument(
        "--interaction-mode",
        default="offensive_only",
        choices=[
            "offensive_only",
            "defensive_always_active",
            "defensive_pre_action_only",
            "defensive_during_action",
            "defensive_post_action",
            "concurrent_offensive_defensive",
            "hybrid_configuration",
        ],
        help="Model interaction mode used by the generated capability report.",
    )
    parser.add_argument("--opportunity-decay-us", type=float, default=1_000.0)
    parser.add_argument("--competitor-tick-to-send-us", type=float, default=None)
    parser.add_argument("--arbitration-latency-us", type=float, default=0.0)
    parser.add_argument("--defensive-activation-latency-us", type=float, default=0.0)
    parser.add_argument("--hybrid-coordination-latency-us", type=float, default=0.0)
    parser.add_argument("--queue-position-penalty-us", type=float, default=0.0)
    parser.add_argument("--max-pending-orders", type=int, default=1)
    parser.add_argument("--max-pending-quantity", type=float, default=1.0)
    parser.add_argument("--max-pending-notional", type=float, default=0.0)
    parser.add_argument("--stale-pending-timeout-us", type=float, default=500_000.0)
    parser.add_argument("--cancel-replace-throttle-us", type=float, default=50_000.0)
    parser.add_argument("--side", choices=["BUY", "SELL"], default="BUY")
    parser.add_argument("--qty", type=int, default=1)
    parser.add_argument(
        "--limit-price",
        type=float,
        default=None,
        help="Optional explicit probe limit price. If omitted, latency_probe derives a passive price from live market data.",
    )
    parser.add_argument("--ack-timeout-sec", type=float, default=20.0)
    parser.add_argument("--cancel-after-ack", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--poll-interval-us",
        type=int,
        default=0,
        help="Broker event polling sleep in microseconds. Default 0 busy-spins for hot latency baselines.",
    )
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

    if args.mode == "broker":
        blocker = _write_broker_mode_blocker(repo_root=repo_root, run_id=run_id, args=args)
        print(json.dumps(blocker, indent=2))
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
    _attach_capability_inputs(summary, args)
    json_path, md_path, current_path = write_summary_reports(
        summary,
        reports_root=reports_root,
        update_current_baseline=args.update_current_baseline,
    )
    print(json.dumps({"run_id": run_id, "sample_path": str(sample_path), "summary_json": str(json_path), "summary_md": str(md_path), "current_baseline": str(current_path) if current_path else ""}, indent=2))
    return 0


def _attach_capability_inputs(summary: dict[str, object], args: argparse.Namespace) -> None:
    summary["capability_inputs"] = {
        "model_interaction_mode": args.interaction_mode,
        "opportunity_decay_us": args.opportunity_decay_us,
        "competitor_tick_to_send_us": args.competitor_tick_to_send_us,
        "arbitration_latency_us": args.arbitration_latency_us,
        "defensive_activation_latency_us": args.defensive_activation_latency_us,
        "hybrid_coordination_latency_us": args.hybrid_coordination_latency_us,
        "queue_position_penalty_us": args.queue_position_penalty_us,
        "pending_exposure": {
            "max_pending_orders": args.max_pending_orders,
            "max_pending_quantity": args.max_pending_quantity,
            "max_pending_notional": args.max_pending_notional,
            "stale_pending_timeout_us": args.stale_pending_timeout_us,
            "cancel_replace_throttle_us": args.cancel_replace_throttle_us,
        },
    }


def _write_broker_mode_blocker(
    *,
    repo_root: Path,
    run_id: str,
    args: argparse.Namespace,
    exc: Exception | None = None,
) -> dict[str, object]:
    reports_root = repo_root / "reports" / "latency_baselines"
    reports_root.mkdir(parents=True, exist_ok=True)
    sample_path = dated_jsonl_path(repo_root, run_id)
    blocker = {
        "schema_version": "latency_baseline_broker_blocker_v1",
        "run_id": run_id,
        "mode": "broker",
        "status": "blocked",
        "blocker": "BROKER_MODE_REPLACED_BY_NATIVE_CPP_PROBE",
        "reason": (
            "Python broker mode is not a hot path. Real Rithmic placement-speed evidence "
            "must come from rithmic_gateway/tools/rithmic_latency_probe.cpp."
        ),
        "requested_environment": args.environment,
        "requested_broker": args.broker,
        "requested_venue": args.venue or args.exchange or args.broker,
        "requested_symbol": args.symbol,
        "sample_path": str(sample_path),
        "principle": "hot_paths_are_native_cpp_no_python_wrappers",
        "authority": {
            "hot_path_language": "c++",
            "wrapper": "none",
            "target": "rithmic_latency_probe",
        },
        "command_hint": (
            "cmake --build build --target rithmic_latency_probe --config Release; "
            "run ./build/rithmic_gateway/rithmic_latency_probe with RITHMIC_PROBE_* env"
        ),
        "error": str(exc) if exc else "",
    }
    (reports_root / f"{run_id}_broker_blocker.json").write_text(
        json.dumps(blocker, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return blocker


if __name__ == "__main__":
    raise SystemExit(main())
