from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from data_system.rithmic_trial.capture.live_capture import LiveCapture
from data_system.rithmic_trial.config import load_config
from data_system.rithmic_trial.connector import build_connector
from data_system.rithmic_trial.convert.hftbacktest_converter import convert_to_npz
from data_system.rithmic_trial.normalize.mapper import normalize_file
from data_system.rithmic_trial.reports.emit_reports import emit_all_reports
from data_system.rithmic_trial.schema.normalized_v1 import SCHEMA_VERSION
from data_system.rithmic_trial.validate.book_reconstruction import reconstruct_book
from data_system.rithmic_trial.validate.quality_checks import validate_events
from data_system.rithmic_trial.platform import is_windows


def cmd_capture(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    if not cfg.enabled and not args.force:
        print("Rithmic trial lane disabled. Set enabled: true or RITHMIC_TRIAL_ENABLED=1")
        return 1
    if is_windows() and cfg.connector.lower() != "fixture":
        print(
            "ERROR: Live Rithmic capture runs on CHI404 only (not this Windows workstation). "
            "Use connector: fixture for local tests, or SSH to CHI404 for live capture.",
            file=sys.stderr,
        )
        return 1
    connector = build_connector(cfg)
    connector.connect()
    capture = LiveCapture(cfg, date=args.date, symbol=args.symbol)
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
    reports_dir = cfg.reports_dir(date)
    report_paths = emit_all_reports(
        reports_dir,
        manifest=manifest,
        normalized_path=norm_path,
        events=events,
        quality=quality,
        book=book,
        conversion=conversion,
        schema_mapping=schema_mapping,
    )
    print(f"Normalized -> {norm_path} ({len(events)} events)")
    print(f"Reports -> {reports_dir}")
    for name, path in report_paths.items():
        print(f"  {name}: {path}")
    return 0 if quality["status"] == "pass" and book["status"] == "pass" and conversion["status"] == "pass" else 1


def cmd_replay_sample(args: argparse.Namespace) -> int:
    from backtest_pipeline.src.runner import ReplayRunner

    npz = Path(args.npz)
    if not npz.exists():
        print(f"NPZ not found: {npz}")
        return 1
    runner = ReplayRunner(str(npz), tick_size=args.tick_size)
    result = runner.run_replay(latency_ms=args.latency_ms, use_combined_strategy=True)
    print(json.dumps(result, indent=2, default=str))
    if "error" in result:
        return 1
    return 0


def cmd_run_unattended(args: argparse.Namespace) -> int:
    from data_system.rithmic_trial.unattended import run_unattended

    return run_unattended(
        args.config,
        start_rtrader=not args.no_start_rtrader,
        symbol=args.symbol,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Rithmic trial ingestion lane")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def _add_config(p: argparse.ArgumentParser) -> None:
        p.add_argument("--config", default="data_system/config/rithmic_trial.yaml")

    p_cap = sub.add_parser("capture")
    _add_config(p_cap)
    p_cap.add_argument("--date", default=None)
    p_cap.add_argument("--symbol", default=None)
    p_cap.add_argument("--duration-sec", type=int, default=30)
    p_cap.add_argument("--poll-interval-sec", type=float, default=1.0)
    p_cap.add_argument("--force", action="store_true")
    p_cap.set_defaults(func=cmd_capture)

    p_proc = sub.add_parser("process")
    _add_config(p_proc)
    p_proc.add_argument("--date", required=True)
    p_proc.add_argument("--symbol", default=None)
    p_proc.set_defaults(func=cmd_process)

    p_rep = sub.add_parser("replay-sample")
    _add_config(p_rep)
    p_rep.add_argument("--npz", required=True)
    p_rep.add_argument("--latency-ms", type=float, default=1.0)
    p_rep.add_argument("--tick-size", type=float, default=0.25)
    p_rep.set_defaults(func=cmd_replay_sample)

    p_un = sub.add_parser("run-unattended", help="Headless capture daemon (CHI404 R|Trader Wine only)")
    _add_config(p_un)
    p_un.add_argument("--symbol", default=None)
    p_un.add_argument("--no-start-rtrader", action="store_true")
    p_un.set_defaults(func=cmd_run_unattended)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
