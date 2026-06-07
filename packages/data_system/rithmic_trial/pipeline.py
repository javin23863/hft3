from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from data_system.rithmic_trial.capture.trial_capture import TrialCapture
from data_system.rithmic_trial.config import load_config
from data_system.rithmic_trial.connector import build_connector
from data_system.rithmic_trial.convert.hftbacktest_converter import convert_to_npz
from data_system.rithmic_trial.normalize.mapper import normalize_file
from data_system.rithmic_trial.reports.emit_reports import emit_all_reports
from data_system.rithmic_trial.schema.normalized_v1 import SCHEMA_VERSION
from data_system.rithmic_trial.validate.book_reconstruction import reconstruct_book
from data_system.rithmic_trial.validate.quality_checks import validate_events
from data_system.rithmic_trial.platform import is_chi404_runtime, is_windows
from data_system.rithmic_trial.endpoint_status import EXTERNAL_ENDPOINT_PROFILE


def cmd_capture(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    if not cfg.enabled and not args.force:
        print("Rithmic trial lane disabled. Set enabled: true or RITHMIC_TRIAL_ENABLED=1")
        return 1
    if cfg.connector.lower() in ("rtrader", "rtrader_bridge", "r-trader", "rithmic_api", "rithmicapi", "api"):
        if is_windows() or not is_chi404_runtime():
            print(
                "ERROR: Rithmic trial capture runs on CHI404 only (BLUEPRINT §4). "
                "Use fixture connector for local tests; do not capture broker data from a workstation.",
                file=sys.stderr,
            )
            return 1
    connector = build_connector(cfg)
    connector.connect()
    symbol = args.symbol or cfg.symbol
    exchange = getattr(args, "exchange", None) or cfg.exchange
    if hasattr(connector, "subscribe_mbo"):
        connector.subscribe_mbo(symbol, exchange)
    capture_date = args.date or _utc_date()
    if args.force:
        cleanup_paths = [
            cfg.raw_dir(capture_date, symbol) / "events.ndjson",
            cfg.raw_dir(capture_date, symbol) / "manifest.json",
            cfg.normalized_dir(capture_date, symbol) / "events.ndjson",
            cfg.replay_dir(capture_date, symbol) / f"{symbol}_{capture_date}_trial.npz",
        ]
        cleanup_paths.extend((cfg.reports_dir(capture_date) / symbol).glob("*.json"))
        for path in cleanup_paths:
            if path.is_file():
                path.unlink()
    capture = TrialCapture(
        cfg,
        date=capture_date,
        symbol=symbol,
        event_id=getattr(args, "event_id", None),
    )
    deadline = time.time() + args.duration_sec
    total = 0
    try:
        while time.time() < deadline:
            batch = connector.poll_events()
            total += capture.append_raw(batch)
            time.sleep(args.poll_interval_sec)
    finally:
        connector.close()
    limitations = connector.limitations()
    detected = connector.detected_event_types()
    missing = limitations.get("missing_event_types", [])
    manifest_path = capture.finalize(detected, missing, limitations)
    print(f"Captured {total} events -> {capture.raw_path}")
    print(f"Manifest -> {manifest_path}")
    return 0 if total > 0 else 1


def _utc_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _utc_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _endpoint_artifact_label(cfg) -> str:
    profile = str((cfg.rithmic or {}).get("endpoint_profile") or os.environ.get("RITHMIC_ENDPOINT_PROFILE") or "")
    if profile == EXTERNAL_ENDPOINT_PROFILE:
        return "rithmic_external"
    return "rithmic_test"


def cmd_order_latency_burst(args: argparse.Namespace) -> int:
    print(
        "ERROR: PYTHON_ORDER_LATENCY_BURST_DISABLED_USE_NATIVE_CPP_PROBE. "
        "Build and run rithmic_gateway/tools/rithmic_latency_probe.cpp for real "
        "Rithmic placement-speed and submit-to-ack evidence.",
        file=sys.stderr,
    )
    return 2


def cmd_process(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    symbol = args.symbol or cfg.symbol
    date = args.date
    raw_path = cfg.raw_dir(date, symbol) / "events.ndjson"
    manifest_path = cfg.raw_dir(date, symbol) / "manifest.json"
    if not raw_path.exists():
        print(f"Missing raw capture: {raw_path}")
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}

    norm_dir = cfg.normalized_dir(date, symbol)
    norm_path = norm_dir / "events.ndjson"
    events, map_warnings = normalize_file(raw_path, cfg, norm_path)
    quality = validate_events(events)
    book = reconstruct_book(events)

    replay_dir = cfg.replay_dir(date, symbol)
    npz_path = replay_dir / f"{symbol}_{date}_trial.npz"
    conversion = convert_to_npz(events, npz_path)

    schema_mapping = {
        "schema_version": SCHEMA_VERSION,
        "raw_file": str(raw_path),
        "normalized_file": str(norm_path),
        "mapping_warnings": map_warnings,
        "field_map": "raw event_type -> normalized_v1 event_type (1:1 with extensions)",
    }
    reports_dir = cfg.reports_dir(date) / symbol
    waterfall_path: Path | None = None
    run_id = os.environ.get("BROKER_LATENCY_RUN_ID")
    if run_id:
        candidate = cfg.repo_root / "runtime" / "broker_latency" / "raw" / run_id / "records.ndjson"
        if candidate.is_file():
            waterfall_path = candidate
    report_paths = emit_all_reports(
        reports_dir,
        manifest=manifest,
        normalized_path=norm_path,
        events=events,
        quality=quality,
        book=book,
        conversion=conversion,
        schema_mapping=schema_mapping,
        waterfall_records_path=waterfall_path,
    )
    print(f"Normalized -> {norm_path} ({len(events)} events)")
    print(f"Reports -> {reports_dir}")
    for name, path in report_paths.items():
        print(f"  {name}: {path}")
    quality_ok = quality["status"] == "pass" or (
        getattr(args, "allow_quality_warn", False) and quality["status"] == "warn"
    )
    return 0 if quality_ok and book["status"] == "pass" and conversion["status"] == "pass" else 1


def cmd_replay_sample(args: argparse.Namespace) -> int:
    print(
        "NOTE: replay-sample is trial-NPZ smoke only. "
        "Macro event research: pipeline replay-event --event-id ... "
        "or scripts/run_event_replay.py (see docs/vault/RESEARCH_ENTRYPOINTS.md).",
        file=sys.stderr,
    )
    from backtest_pipeline.src.runner import ReplayRunner

    npz = Path(args.npz)
    if not npz.exists():
        print(f"NPZ not found: {npz}")
        return 1
    runner = ReplayRunner(str(npz), tick_size=args.tick_size)
    use_strategy = not getattr(args, "simple", False)
    max_steps = getattr(args, "max_steps", None)
    if max_steps == 0:
        max_steps = None
    result = runner.run_replay(
        latency_ms=args.latency_ms,
        use_combined_strategy=use_strategy,
        max_steps=max_steps,
    )
    print(json.dumps(result, indent=2, default=str))
    if "error" in result:
        return 1
    return 0


def cmd_replay_event(args: argparse.Namespace) -> int:
    """Event-driven replay via Databento NPZ + CHI404 latency (not trial capture date)."""
    import subprocess

    summary = Path(args.chi404_summary)
    if not summary.is_absolute():
        summary = _REPO / summary
    cmd = [
        sys.executable,
        str(_REPO / "scripts" / "run_event_replay.py"),
        "--event-id",
        args.event_id,
        "--chi404-summary",
        str(summary),
    ]
    if args.npz:
        cmd.extend(["--npz", str(Path(args.npz))])
    if args.out:
        cmd.extend(["--out", str(Path(args.out))])
    if args.latency_ms is not None:
        cmd.extend(["--latency-ms", str(args.latency_ms)])
    if not getattr(args, "full_hftbacktest", False):
        cmd.append("--skip-hftbacktest")
    return subprocess.call(cmd)


def cmd_run_unattended(args: argparse.Namespace) -> int:
    from data_system.rithmic_trial.unattended import run_unattended

    return run_unattended(
        args.config,
        start_rtrader=not args.no_start_rtrader,
        symbol=args.symbol,
    )


def cmd_broker_latency_daemon(args: argparse.Namespace) -> int:
    from data_system.rithmic_trial.latency.broker_latency_daemon import main as daemon_main

    argv = ["--config", args.config, "--poll-interval-sec", str(args.poll_interval_sec)]
    if args.run_id:
        argv.extend(["--run-id", args.run_id])
    return daemon_main(argv)


def main() -> int:
    parser = argparse.ArgumentParser(description="Rithmic trial ingestion lane")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def _add_config(p: argparse.ArgumentParser) -> None:
        p.add_argument("--config", default="packages/data_system/config/rithmic_trial.yaml")

    p_cap = sub.add_parser("capture")
    _add_config(p_cap)
    p_cap.add_argument("--date", default=None)
    p_cap.add_argument("--symbol", default=None)
    p_cap.add_argument("--exchange", default=None)
    p_cap.add_argument("--duration-sec", type=int, default=30)
    p_cap.add_argument("--poll-interval-sec", type=float, default=1.0)
    p_cap.add_argument("--force", action="store_true")
    p_cap.add_argument(
        "--event-id",
        default=None,
        help="Macro event tag for manifest (research key; capture folder still uses --date)",
    )
    p_cap.set_defaults(func=cmd_capture)

    p_proc = sub.add_parser("process")
    _add_config(p_proc)
    p_proc.add_argument("--date", required=True)
    p_proc.add_argument("--symbol", default=None)
    p_proc.add_argument(
        "--allow-quality-warn",
        action="store_true",
        help="Exit 0 when quality status is warn (trial smoke only)",
    )
    p_proc.set_defaults(func=cmd_process)

    p_rep = sub.add_parser("replay-sample")
    _add_config(p_rep)
    p_rep.add_argument("--npz", required=True)
    p_rep.add_argument("--latency-ms", type=float, default=1.0)
    p_rep.add_argument("--tick-size", type=float, default=0.25)
    p_rep.add_argument(
        "--max-steps",
        type=int,
        default=500,
        help="Bounded step cap (smoke only). 0 disables the cap. hftbacktest 2.3.0 "
        "does not always elapse-to-end on a short NPZ, so the cap is the only "
        "guaranteed stop. Trial data is short by design — keep the default low.",
    )
    p_rep.add_argument(
        "--simple",
        action="store_true",
        help="Trade-only replay without combined strategy (trial NPZ default)",
    )
    p_rep.set_defaults(func=cmd_replay_sample)

    p_ev = sub.add_parser(
        "replay-event",
        help="Replay macro event from Databento NPZ at CHI404-measured latency",
    )
    _add_config(p_ev)
    p_ev.add_argument("--event-id", required=True)
    p_ev.add_argument("--npz", default=None)
    p_ev.add_argument(
        "--chi404-summary",
        default="runtime/latency_reports/latency_summary.json",
    )
    p_ev.add_argument("--out", default=None)
    p_ev.add_argument("--latency-ms", type=float, default=None)
    p_ev.add_argument(
        "--full-hftbacktest",
        action="store_true",
        help="Include slow hftbacktest_loop engine (default: event_accurate_mbo only)",
    )
    p_ev.set_defaults(func=cmd_replay_event)

    p_un = sub.add_parser("run-unattended", help="Headless capture daemon (CHI404 R|Trader Wine only)")
    _add_config(p_un)
    p_un.add_argument("--symbol", default=None)
    p_un.add_argument("--no-start-rtrader", action="store_true")
    p_un.set_defaults(func=cmd_run_unattended)

    p_ol = sub.add_parser(
        "order-latency-burst",
        help="Send explicit Rithmic Test limit orders and measure submit-to-ack latency",
    )
    _add_config(p_ol)
    p_ol.add_argument("--symbol", default=None)
    p_ol.add_argument("--exchange", default=None)
    p_ol.add_argument("--side", choices=("BUY", "SELL"), default="BUY")
    p_ol.add_argument("--qty", type=int, default=1)
    p_ol.add_argument("--price", type=float, required=True)
    p_ol.add_argument("--count", type=int, default=1)
    p_ol.add_argument("--ack-timeout-sec", type=float, default=15.0)
    p_ol.add_argument("--interval-ms", type=float, default=250.0)
    p_ol.add_argument("--run-id", default=None)
    p_ol.add_argument("--subscribe-md", action="store_true")
    p_ol.add_argument("--no-cancel-after-ack", dest="cancel_after_ack", action="store_false")
    p_ol.set_defaults(cancel_after_ack=True)
    p_ol.set_defaults(func=cmd_order_latency_burst)

    p_pl = sub.add_parser(
        "broker-latency-daemon",
        help="Monotonic broker order latency waterfall daemon (CHI404 only)",
    )
    _add_config(p_pl)
    p_pl.add_argument("--run-id", default=None)
    p_pl.add_argument("--poll-interval-sec", type=float, default=0.25)
    p_pl.set_defaults(func=cmd_broker_latency_daemon)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
