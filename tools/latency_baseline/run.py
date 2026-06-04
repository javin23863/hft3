"""Command line entry point for permanent latency baseline runs."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sys
import time

from .recorder import LatencyRecorder, dated_jsonl_path
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
        try:
            sample_path, records, broker_artifacts = _run_broker_baseline(
                repo_root=repo_root,
                run_id=run_id,
                venue=venue,
                args=args,
            )
        except Exception as exc:
            _write_broker_mode_blocker(repo_root=repo_root, run_id=run_id, args=args, exc=exc)
            print(f"BROKER_LATENCY_BASELINE_FAILED: {exc}", file=sys.stderr)
            return 2
        summary = build_summary(
            records,
            run_id=run_id,
            sample_path=sample_path,
            baseline_path=baseline_path,
        )
        summary["broker_artifacts"] = broker_artifacts
        summary["broker_mode"] = {
            "status": "observed" if records else "missing",
            "broker": args.broker,
            "environment": args.environment,
            "requested_symbol": args.symbol,
            "resolved_symbol": broker_artifacts.get("resolved_symbol", args.symbol),
            "venue": venue,
            "order_action": "new",
            "cancel_after_ack": bool(args.cancel_after_ack),
            "note": "placement speed uses local monotonic probes; acknowledgment latency is reported separately",
        }
        json_path, md_path, current_path = write_summary_reports(
            summary,
            reports_root=reports_root,
            update_current_baseline=args.update_current_baseline,
        )
        print(json.dumps({"run_id": run_id, "sample_path": str(sample_path), "summary_json": str(json_path), "summary_md": str(md_path), "current_baseline": str(current_path) if current_path else "", "broker_artifacts": broker_artifacts}, indent=2))
        return 0

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


def _run_broker_baseline(
    *,
    repo_root: Path,
    run_id: str,
    venue: str,
    args: argparse.Namespace,
) -> tuple[Path, list[dict], dict[str, str]]:
    if args.broker.lower() != "rithmic":
        raise ValueError("broker mode currently supports --broker rithmic")

    packages_path = repo_root / "packages"
    if str(packages_path) not in sys.path:
        sys.path.insert(0, str(packages_path))

    from data_system.rithmic_trial.config import load_config
    from data_system.rithmic_trial.connector import build_connector

    os.environ.setdefault("RITHMIC_TRIAL_ENABLED", "1")
    if args.environment.lower() == "paper":
        os.environ.setdefault("RITHMIC_ENDPOINT_PROFILE", "paper_chicago")
        os.environ.setdefault(
            "RITHMIC_API_CONFIG",
            "packages/data_system/config/rithmic_api_paper.yaml",
        )
        os.environ.setdefault("RITHMIC_CAPTURE_ENVIRONMENT", "rithmic_paper")

    cfg = load_config("packages/data_system/config/rithmic_trial.yaml")
    connector = build_connector(cfg)
    symbol = _resolve_probe_symbol(args.symbol)
    exchange = args.exchange or cfg.exchange or venue
    raw_dir = repo_root / "runtime" / "latency_baselines" / run_id
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_events_path = raw_dir / "rithmic_events.ndjson"
    recorder = LatencyRecorder(
        repo_root=repo_root,
        run_id=run_id,
        environment=args.environment,
        broker=args.broker,
        venue=venue,
        symbol=symbol,
        strategy_id=args.strategy_id,
        model_id=args.model_id,
        trade_manager_id=args.trade_manager_id,
    )
    events: list[dict] = []

    def record_events(batch: list[dict]) -> None:
        if not batch:
            return
        for ev in batch:
            rec = dict(ev)
            rec.setdefault("run_id", run_id)
            rec.setdefault("symbol", symbol)
            rec.setdefault("exchange", exchange)
            events.append(rec)

    try:
        connector.connect()
        connector.subscribe_mbo(symbol, exchange)
        market_event = _wait_for_market_event(
            connector=connector,
            record_events=record_events,
            deadline=time.monotonic() + min(max(float(args.duration), 1.0), 30.0),
        )
        market_event_received_ts = int(market_event["local_monotonic_receive_ns"])
        features_ready_ts = time.perf_counter_ns()
        price = args.limit_price
        if price is None:
            price = _derive_probe_price(market_event, side=args.side)
        decision_ready_ts = time.perf_counter_ns()
        risk_check_ready_ts = time.perf_counter_ns()
        order_ready_ts = time.perf_counter_ns()
        client_order_id = connector.send_order(symbol, args.side, args.qty, price)
        ack_event = _wait_for_order_response(
            connector=connector,
            client_order_id=client_order_id,
            record_events=record_events,
            deadline=time.monotonic() + float(args.ack_timeout_sec),
        )
        submit_event = _find_order_submit(events, client_order_id)
        order_send_ts = int(
            (submit_event or {}).get("ts_emit_ns")
            or (submit_event or {}).get("local_monotonic_receive_ns")
            or order_ready_ts
        )
        ack_received_ts = int(ack_event["local_monotonic_receive_ns"]) if ack_event else None
        success = bool(ack_event and ack_event.get("event_type") == "order_ack")
        reject_reason = "" if success else str((ack_event or {}).get("event_type") or "ack_timeout")
        timestamps = {
            "market_event_received_ts": market_event_received_ts,
            "features_ready_ts": features_ready_ts,
            "decision_ready_ts": decision_ready_ts,
            "risk_check_ready_ts": risk_check_ready_ts,
            "order_ready_ts": order_ready_ts,
            "order_send_ts": order_send_ts,
            "ack_received_ts": ack_received_ts,
        }
        if success and args.cancel_after_ack:
            broker_order_id = str(ack_event.get("broker_order_id") or "")
            if broker_order_id:
                cancel_send_ts = time.perf_counter_ns()
                connector.cancel_order(broker_order_id)
                cancel_ack = _wait_for_cancel_ack(
                    connector=connector,
                    client_order_id=client_order_id,
                    broker_order_id=broker_order_id,
                    record_events=record_events,
                    deadline=time.monotonic() + float(args.ack_timeout_sec),
                )
                timestamps["cancel_send_ts"] = cancel_send_ts
                if cancel_ack:
                    timestamps["cancel_ack_received_ts"] = int(cancel_ack["local_monotonic_receive_ns"])
        record = recorder.write_sample(
            order_action="new",
            side=args.side,
            order_type="limit",
            quantity=args.qty,
            timestamps=timestamps,
            success=success,
            reject_reason=reject_reason,
        )
    finally:
        connector.close()
        _flush_events(raw_events_path, events)

    artifacts = {
        "raw_events_path": str(raw_events_path),
        "resolved_symbol": symbol,
        "requested_symbol": args.symbol,
        "event_count": str(len(events)),
        "client_order_id": str(client_order_id) if "client_order_id" in locals() else "",
        "derived_limit_price": str(price) if "price" in locals() else "",
    }
    return recorder.sample_path(), [record], artifacts


def _wait_for_market_event(*, connector, record_events, deadline: float) -> dict:
    while time.monotonic() < deadline:
        batch = connector.poll_events()
        record_events(batch)
        market_events = [
            ev for ev in batch if ev.get("event_type") in {"quote", "trade"} and ev.get("local_monotonic_receive_ns")
        ]
        if market_events:
            return market_events[-1]
        time.sleep(0.005)
    raise TimeoutError("no market data event observed before order placement")


def _wait_for_order_response(*, connector, client_order_id: str, record_events, deadline: float) -> dict | None:
    while time.monotonic() < deadline:
        batch = connector.poll_events()
        record_events(batch)
        for ev in batch:
            if _matches_client_order(ev, client_order_id) and ev.get("event_type") in {
                "order_ack",
                "reject",
                "order_failure",
            }:
                return ev
        time.sleep(0.005)
    return None


def _wait_for_cancel_ack(
    *,
    connector,
    client_order_id: str,
    broker_order_id: str,
    record_events,
    deadline: float,
) -> dict | None:
    while time.monotonic() < deadline:
        batch = connector.poll_events()
        record_events(batch)
        for ev in batch:
            if ev.get("event_type") == "cancel" and (
                _matches_client_order(ev, client_order_id)
                or str(ev.get("broker_order_id") or "") == broker_order_id
            ):
                return ev
        time.sleep(0.005)
    return None


def _matches_client_order(ev: dict, client_order_id: str) -> bool:
    return any(
        str(ev.get(field) or "") == client_order_id
        for field in ("client_order_id", "order_id", "user_msg", "tag")
    )


def _find_order_submit(events: list[dict], client_order_id: str) -> dict | None:
    for ev in events:
        if ev.get("event_type") == "order_submit" and _matches_client_order(ev, client_order_id):
            return ev
    return None


def _flush_events(path: Path, events: list[dict]) -> None:
    if not events:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev, sort_keys=True, allow_nan=False) + "\n")


def _derive_probe_price(event: dict, *, side: str) -> float:
    price = event.get("bid_price") or event.get("ask_price") or event.get("price")
    if price is None:
        raise ValueError("cannot derive probe limit price from market event")
    price = float(price)
    # Keep the latency probe passive in paper by moving away from the touch.
    return price - 1000.0 if side == "BUY" else price + 1000.0


def _resolve_probe_symbol(symbol: str) -> str:
    upper = symbol.upper()
    if any(ch.isdigit() for ch in upper):
        return upper
    quarterly_roots = {"ES", "MES", "NQ", "MNQ", "YM", "MYM", "RTY", "M2K"}
    if upper not in quarterly_roots:
        return upper
    now = datetime.now(UTC)
    quarters = [(3, "H"), (6, "M"), (9, "U"), (12, "Z")]
    month_code = "Z"
    for month, code in quarters:
        if now.month <= month:
            month_code = code
            break
    return f"{upper}{month_code}{now.year % 10}"


def _write_broker_mode_blocker(
    *,
    repo_root: Path,
    run_id: str,
    args: argparse.Namespace,
    exc: Exception | None = None,
) -> None:
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
        "error": str(exc) if exc else "",
    }
    (reports_root / f"{run_id}_broker_blocker.json").write_text(
        json.dumps(blocker, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
