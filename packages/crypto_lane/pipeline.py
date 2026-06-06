"""CLI for quarantined crypto-alpha research lane."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from dataclasses import asdict

_LANE = Path(__file__).resolve().parent
_REPO = _LANE.parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "packages"))

from crypto_lane.src.align.latency_profile import (
    calibrate_ws_rtt,
    measure_live_ws_rtt,
    measure_node_profile_from_btc,
    save_node_profile,
)
from crypto_lane.src.config.env_loader import ensure_crypto_env, redacted_env_report
from crypto_lane.src.config_loader import load_hypotheses, load_manifest
from crypto_lane.src.ingest.bronze_pull import pull_bronze
from crypto_lane.src.ingest.mempool_pull import pull_live_mempool, pull_mempool_backfill
from crypto_lane.src.ingest.normalize import normalize_all
from crypto_lane.src.ml.candidate_registry import discover_candidates, discover_backtest_configs
from crypto_lane.src.ml.walk_forward_runner import run_all_smokes, run_smoke


def _candidate_model_from_yaml(doc: dict) -> "CandidateModel":
    from research_pipeline.types import CandidateModel

    return CandidateModel(
        candidate_id=str(doc.get("candidate_id", "")),
        model_id=str(doc.get("hypothesis_id") or doc.get("candidate_id", "CRYPTO_CANDIDATE")),
        strategy_params={
            "target": doc.get("target"),
            "horizons": doc.get("horizons") or [],
            "features": doc.get("features") or [],
            "validation": doc.get("validation") or {},
            "backtest": doc.get("backtest") or {},
        },
        thesis=str(doc.get("candidate_id") or doc.get("hypothesis_id") or "crypto candidate"),
        metadata={
            "asset_class": "CRYPTO",
            "symbol": str((doc.get("metadata") or {}).get("symbol") or "BTCUSDT"),
            "hypothesis_id": doc.get("hypothesis_id"),
            "target": doc.get("target"),
            "btc_node_required": doc.get("btc_node_required"),
        },
    )


def cmd_discover(_: argparse.Namespace) -> int:
    payload = {
        "hypotheses": [h["hypothesis_id"] for h in load_hypotheses()],
        "candidates": [c["candidate_id"] for c in discover_candidates()],
        "backtests": [b["config_id"] for b in discover_backtest_configs()],
    }
    print(json.dumps(payload, indent=2))
    return 0


def cmd_smoke(args: argparse.Namespace) -> int:
    if args.candidate:
        report = run_smoke(args.candidate)
    else:
        report = run_all_smokes()
    print(json.dumps(report, indent=2, default=str))
    return 0


def cmd_manifest(_: argparse.Namespace) -> int:
    print(json.dumps(load_manifest(), indent=2))
    return 0


def cmd_env_check(_: argparse.Namespace) -> int:
    ensure_crypto_env()
    print(json.dumps(redacted_env_report(), indent=2))
    return 0


def cmd_pull_bronze(args: argparse.Namespace) -> int:
    ensure_crypto_env()
    sources = [s.strip() for s in args.sources.split(",")] if args.sources else None
    result = pull_bronze(start=args.start, end=args.end, sources=sources)
    print(json.dumps(result, indent=2))
    return 0


def cmd_pull_mempool(args: argparse.Namespace) -> int:
    ensure_crypto_env()
    if args.samples and args.samples > 1:
        snaps = pull_live_mempool(samples=args.samples, interval_minutes=args.interval_minutes)
        print(json.dumps({"written": len(snaps)}, indent=2))
    else:
        count = pull_mempool_backfill(hours=args.hours, interval_minutes=args.interval_minutes)
        print(json.dumps({"written": count}, indent=2))
    return 0


def cmd_normalize(args: argparse.Namespace) -> int:
    ensure_crypto_env()
    paths = normalize_all(start=args.start, end=args.end)
    print(json.dumps({k: str(v) for k, v in paths.items()}, indent=2))
    return 0


def cmd_calibrate_ws_rtt(args: argparse.Namespace) -> int:
    ensure_crypto_env()
    profile = calibrate_ws_rtt(args.venue, ws_rtt_ms=args.ws_rtt_ms)
    if args.measure_node:
        node = measure_node_profile_from_btc(tunnel_rtt_ms=args.tunnel_rtt_ms)
        save_node_profile(node)
        print(json.dumps({"venue": profile.__dict__, "node": node.__dict__}, indent=2))
    else:
        print(json.dumps(profile.__dict__, indent=2))
    return 0


def cmd_measure_live_ws_rtt(args: argparse.Namespace) -> int:
    import asyncio
    ensure_crypto_env()
    profile = asyncio.run(measure_live_ws_rtt(args.venue, url=args.url, timeout_s=args.timeout))
    print(json.dumps(profile.__dict__, indent=2))
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    ensure_crypto_env()
    sources = [s.strip() for s in args.sources.split(",")] if args.sources else None
    bronze = pull_bronze(start=args.start, end=args.end, sources=sources)
    mempool = {"written": 0}
    if args.with_mempool:
        try:
            mempool = {"written": pull_mempool_backfill(hours=args.mempool_hours, interval_minutes=15)}
        except Exception as exc:
            mempool = {"written": 0, "error": str(exc)}
    paths = normalize_all(start=args.start, end=args.end)
    print(json.dumps({"bronze": bronze, "mempool": mempool, "normalized": {k: str(v) for k, v in paths.items()}}, indent=2))
    return 0


def cmd_record_kraken_l3(args: argparse.Namespace) -> int:
    from crypto_lane.src.data_io.kraken_l3_recorder import cmd_record_kraken_l3
    return cmd_record_kraken_l3(args)


def cmd_convert_kraken_l3(args: argparse.Namespace) -> int:
    from crypto_lane.src.data_io.kraken_l3_converter import cmd_convert_kraken_l3
    return cmd_convert_kraken_l3(args)


def cmd_record_binance_l2(args: argparse.Namespace) -> int:
    from crypto_lane.src.data_io.binance_l2_recorder import cmd_record_binance_l2
    return cmd_record_binance_l2(args)


def cmd_convert_binance_l2(args: argparse.Namespace) -> int:
    from crypto_lane.src.data_io.binance_l2_converter import cmd_convert_binance_l2
    return cmd_convert_binance_l2(args)


def cmd_validate_crypto(args: argparse.Namespace) -> int:
    ensure_crypto_env()
    from crypto_lane.src.validation.crypto_validation_workflow import validate_crypto_candidate
    from crypto_lane.src.ml.candidate_registry import discover_candidates
    from backtest_pipeline.src.promotion_gate import set_execution_classification
    candidates = discover_candidates()
    target = [c for c in candidates if c["candidate_id"] == args.candidate_id]
    if not target:
        print(f"Candidate not found: {args.candidate_id}", file=sys.stderr)
        return 1
    from crypto_lane.src.types import repo_root_from_lane
    catalog = repo_root_from_lane()
    candidate = _candidate_model_from_yaml(target[0])
    report = validate_crypto_candidate(candidate, catalog)
    classification = report.result.execution_classification if report.result.error == "" else "NO_EXECUTION"
    report.result.execution_classification = classification
    set_execution_classification(args.candidate_id, classification)
    payload = asdict(report)
    out_dir = repo_root_from_lane() / "research_cards" / "crypto" / args.candidate_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "validation_report.json").write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps(payload, indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="crypto_lane.pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("discover").set_defaults(func=cmd_discover)
    sub.add_parser("manifest").set_defaults(func=cmd_manifest)
    sub.add_parser("env-check").set_defaults(func=cmd_env_check)

    p_smoke = sub.add_parser("smoke")
    p_smoke.add_argument("--candidate", default=None)
    p_smoke.set_defaults(func=cmd_smoke)

    p_bronze = sub.add_parser("pull-bronze")
    p_bronze.add_argument("--start", required=True)
    p_bronze.add_argument("--end", required=True)
    p_bronze.add_argument("--sources", default=None, help="binance,deribit,mempool")
    p_bronze.set_defaults(func=cmd_pull_bronze)

    p_mp = sub.add_parser("pull-mempool")
    p_mp.add_argument("--hours", type=int, default=24)
    p_mp.add_argument("--interval-minutes", type=int, default=15)
    p_mp.add_argument("--samples", type=int, default=0)
    p_mp.set_defaults(func=cmd_pull_mempool)

    p_norm = sub.add_parser("normalize")
    p_norm.add_argument("--start", required=True)
    p_norm.add_argument("--end", required=True)
    p_norm.set_defaults(func=cmd_normalize)

    p_ingest = sub.add_parser("ingest")
    p_ingest.add_argument("--start", required=True)
    p_ingest.add_argument("--end", required=True)
    p_ingest.add_argument("--sources", default=None)
    p_ingest.add_argument("--with-mempool", action="store_true")
    p_ingest.add_argument("--mempool-hours", type=int, default=24)
    p_ingest.set_defaults(func=cmd_ingest)

    p_cal = sub.add_parser(
        "calibrate-ws-rtt",
        help="Synthetic WS RTT calibration from ws_rtt_ms (not a live probe)",
    )
    p_cal.add_argument("--venue", default="binance_perp")
    p_cal.add_argument("--ws-rtt-ms", type=float, default=None)
    p_cal.add_argument("--measure-node", action="store_true")
    p_cal.add_argument("--tunnel-rtt-ms", type=float, default=None)
    p_cal.set_defaults(func=cmd_calibrate_ws_rtt)

    p_live = sub.add_parser(
        "measure-live-ws-rtt",
        help="Live WebSocket ping/pong RTT measurement for a venue",
    )
    p_live.add_argument("--venue", default="binance_perp")
    p_live.add_argument("--url", default=None, help="Override WebSocket URL")
    p_live.add_argument("--timeout", type=float, default=10.0, help="Connection timeout in seconds")
    p_live.set_defaults(func=cmd_measure_live_ws_rtt)

    p_record = sub.add_parser(
        "record-l3",
        help="Record Kraken WS book-depth via WebSocket (L2_DEPTH raw — not order-level MBO)",
    )
    p_record.add_argument("--symbols", default=None, help="Comma-separated symbols (default: BTC/USD,ETH/USD,SOL/USD)")
    p_record.add_argument("--output-dir", default=None, help="Output directory")
    p_record.add_argument("--depth", type=int, default=1000)
    p_record.add_argument("--duration", type=float, default=None, help="Recording duration in seconds")
    p_record.set_defaults(func=cmd_record_kraken_l3)

    p_bn_record = sub.add_parser("record-l2", help="Record Binance L2 order book depth via WebSocket")
    p_bn_record.add_argument("--symbols", default=None)
    p_bn_record.add_argument("--output-dir", default=None)
    p_bn_record.add_argument("--duration", type=float, default=None)
    p_bn_record.set_defaults(func=cmd_record_binance_l2)

    p_bn_conv = sub.add_parser("convert-l2", help="Convert Binance L2 NDJSON to NPZ")
    p_bn_conv.add_argument("ndjson", type=str)
    p_bn_conv.add_argument("--output", default=None)
    p_bn_conv.add_argument("--routing-symbol", default=None, help="Write to routing-expected data dir for this symbol")
    p_bn_conv.add_argument("--snapshot", default=None, help="Path to REST snapshot JSON for initial book state")
    p_bn_conv.add_argument("--no-fetch", action="store_true", help="Skip REST snapshot fetch")
    p_bn_conv.add_argument("--start-time-ns", type=int, default=1_000_000_000)
    p_bn_conv.add_argument("--step-ns", type=int, default=1_000_000)
    p_bn_conv.set_defaults(func=cmd_convert_binance_l2)

    p_conv = sub.add_parser(
        "convert-l3",
        help="Convert Kraken WS book-depth NDJSON to NPZ (L2_DEPTH — not true order-level MBO)",
    )
    p_conv.add_argument("ndjson", type=str, help="Path to NDJSON file")
    p_conv.add_argument("--output", default=None, help="Explicit NPZ output path")
    p_conv.add_argument("--routing-symbol", default=None, help="Write to routing-expected data dir for this symbol")
    p_conv.add_argument("--start-time-ns", type=int, default=1_000_000_000)
    p_conv.add_argument("--step-ns", type=int, default=1_000_000)
    p_conv.set_defaults(func=cmd_convert_kraken_l3)

    p_val = sub.add_parser("validate", help="Run crypto execution validation for a candidate")
    p_val.add_argument("candidate_id", type=str, help="Candidate model ID")
    p_val.set_defaults(func=cmd_validate_crypto)

    p_probe = sub.add_parser("probe-ws-rtt", help="Deprecated alias for calibrate-ws-rtt")
    p_probe.add_argument("--venue", default="binance_perp")
    p_probe.add_argument("--ws-rtt-ms", type=float, default=None)
    p_probe.add_argument("--measure-node", action="store_true")
    p_probe.add_argument("--tunnel-rtt-ms", type=float, default=None)
    p_probe.set_defaults(func=cmd_calibrate_ws_rtt)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
