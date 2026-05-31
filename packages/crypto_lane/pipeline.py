"""CLI for quarantined crypto-alpha research lane."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_LANE = Path(__file__).resolve().parent
_REPO = _LANE.parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "packages"))

from crypto_lane.src.align.latency_profile import (
    calibrate_ws_rtt,
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
