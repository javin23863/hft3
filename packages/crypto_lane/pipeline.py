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
from crypto_lane.src.ingest.binance_vision_pull import pull_bookticker_from_vision
from crypto_lane.src.ingest.l3_gap_fill import audit_l3_gaps, fill_l3_gaps
from crypto_lane.src.ingest.mempool_preflight import preflight_mempool_gaps
from crypto_lane.src.ingest.node_remote_sync import sync_chi404_btc_node_artifacts
from crypto_lane.src.ingest.gold_pull import (
    pull_bookticker_from_b2,
    pull_gold,
    supplement_dvol_from_deribit,
    supplement_perp_from_binance,
)
from crypto_lane.src.ingest.mempool_pull import (
    backfill_blockspace_from_node,
    pull_live_mempool,
    pull_mempool_backfill,
)
from crypto_lane.src.ingest.normalize import normalize_all
from crypto_lane.src.config_loader import list_backtest_config_paths, load_yaml
from crypto_lane.src.ml.candidate_registry import discover_candidates, discover_backtest_configs
from crypto_lane.src.ingest.fill_test_gaps import run_fill_test_gaps, write_fill_report
from crypto_lane.src.ml.walk_forward_runner import run_all_smokes, run_smoke


def cmd_discover(_: argparse.Namespace) -> int:
    payload = {
        "hypotheses": [h["hypothesis_id"] for h in load_hypotheses()],
        "candidates": [c["candidate_id"] for c in discover_candidates()],
        "backtests": [b["config_id"] for b in discover_backtest_configs()],
        "backtests_production": [
            load_yaml(p)["config_id"] for p in list_backtest_config_paths(include_production=True)
        ],
    }
    print(json.dumps(payload, indent=2))
    return 0


def cmd_smoke(args: argparse.Namespace) -> int:
    if args.candidate:
        reports = [run_smoke(args.candidate, production=args.production)]
    else:
        reports = run_all_smokes(production=args.production)
    payload = reports[0] if args.candidate else reports
    print(json.dumps(payload, indent=2, default=str))
    return 0 if all(r.get("pass_fail") == "pass" for r in reports) else 1


def cmd_fill_test_gaps(args: argparse.Namespace) -> int:
    ensure_crypto_env()
    report = run_fill_test_gaps(
        dry_run=args.dry_run,
        sync_chi404_node=args.sync_chi404_node,
        skip_chi404=args.skip_chi404,
        ws_rtt_ms=args.ws_rtt_ms,
        force_replace_synthetic=args.force_replace_synthetic,
        allow_degraded=args.allow_degraded,
        continue_on_error=args.continue_on_error,
    )
    out = write_fill_report(report)
    report["report_path"] = str(out)
    print(json.dumps(report, indent=2, default=str))
    return 0 if report.get("ready") else 1


def cmd_manifest(_: argparse.Namespace) -> int:
    print(json.dumps(load_manifest(), indent=2))
    return 0


def cmd_sync_node_host(args: argparse.Namespace) -> int:
    ensure_crypto_env()
    host = (args.host or "chi404").strip().lower()
    if host != "chi404":
        print(json.dumps({"error": f"unsupported host: {host}"}, indent=2))
        return 1
    print(json.dumps(sync_chi404_btc_node_artifacts(), indent=2))
    return 0


def cmd_env_check(args: argparse.Namespace) -> int:
    ensure_crypto_env()
    report = redacted_env_report()
    if args.sync_chi404_node:
        report["chi404_node_sync"] = sync_chi404_btc_node_artifacts()
    if args.mempool_start and args.mempool_end:
        report["mempool_preflight"] = preflight_mempool_gaps(
            start=args.mempool_start,
            end=args.mempool_end,
        )
    print(json.dumps(report, indent=2))
    return 0


def cmd_mempool_preflight(args: argparse.Namespace) -> int:
    ensure_crypto_env()
    print(json.dumps(preflight_mempool_gaps(start=args.start, end=args.end), indent=2))
    return 0


def cmd_pull_gold(args: argparse.Namespace) -> int:
    ensure_crypto_env()
    sources = [s.strip() for s in args.sources.split(",")] if args.sources else None
    counts = pull_gold(start=args.start, end=args.end, sources=sources)
    if "binance" in {s.strip().lower() for s in (sources or ["binance", "deribit", "mempool"])}:
        counts["perp_binance_api"] = supplement_perp_from_binance(start=args.start, end=args.end)
    if "deribit" in {s.strip().lower() for s in (sources or ["binance", "deribit", "mempool"])}:
        counts["dvol_deribit_api"] = supplement_dvol_from_deribit(start=args.start, end=args.end)
    print(json.dumps(counts, indent=2))
    return 0


def cmd_pull_bronze(args: argparse.Namespace) -> int:
    """Deprecated: use pull-gold (production lake on crypto-alpha-datasets)."""
    return cmd_pull_gold(args)


def cmd_pull_mempool(args: argparse.Namespace) -> int:
    ensure_crypto_env()
    if args.samples and args.samples > 1:
        snaps = pull_live_mempool(samples=args.samples, interval_minutes=args.interval_minutes)
        print(json.dumps({"written": len(snaps)}, indent=2))
    else:
        count = pull_mempool_backfill(hours=args.hours, interval_minutes=args.interval_minutes)
        print(json.dumps({"written": count}, indent=2))
    return 0


def cmd_backfill_blockspace(args: argparse.Namespace) -> int:
    ensure_crypto_env()
    count = backfill_blockspace_from_node(
        start=args.start,
        end=args.end,
        step_hours=args.step_hours,
    )
    print(json.dumps({"written": count}, indent=2))
    return 0 if count else 1


def cmd_normalize(args: argparse.Namespace) -> int:
    ensure_crypto_env()
    paths = normalize_all(start=args.start, end=args.end)
    print(json.dumps({k: str(v) for k, v in paths.items()}, indent=2))
    return 0


def cmd_calibrate_ws_rtt(args: argparse.Namespace) -> int:
    ensure_crypto_env()
    profile = calibrate_ws_rtt(
        args.venue,
        ws_rtt_ms=args.ws_rtt_ms,
        live_measured=args.live_measured,
    )
    if args.measure_node:
        node = measure_node_profile_from_btc(tunnel_rtt_ms=args.tunnel_rtt_ms)
        save_node_profile(node)
        print(json.dumps({"venue": profile.__dict__, "node": node.__dict__}, indent=2))
    else:
        print(json.dumps(profile.__dict__, indent=2))
    return 0


def cmd_pull_bookticker(args: argparse.Namespace) -> int:
    ensure_crypto_env()
    report = {
        "b2": pull_bookticker_from_b2(
            start=args.start, end=args.end, max_days=args.max_days
        ),
        "binance_vision": pull_bookticker_from_vision(
            start=args.start,
            end=args.end,
            sleep_s=args.sleep_s,
            max_days=args.max_days,
        ),
    }
    print(json.dumps(report, indent=2))
    return 0


def cmd_fill_l3_gaps(args: argparse.Namespace) -> int:
    from crypto_lane.src.ingest.l3_preflight import preflight_l3_gaps

    ensure_crypto_env()
    if args.audit_only:
        print(json.dumps(audit_l3_gaps(start=args.start, end=args.end), indent=2))
        return 0
    if args.dry_run:
        print(
            json.dumps(
                preflight_l3_gaps(start=args.start, end=args.end, vision_probe=False),
                indent=2,
            )
        )
        return 0
    report = fill_l3_gaps(
        start=args.start,
        end=args.end,
        replace_synthetic=args.replace_synthetic,
        allow_degraded=args.allow_degraded,
        force=args.force,
        sleep_s=args.sleep_s,
        max_days=args.max_days,
    )
    print(json.dumps(report, indent=2))
    if report.get("aborted"):
        return 1
    absent_after = report.get("absent_after", 0)
    synthetic_after = report.get("synthetic_after", 0)
    if absent_after:
        return 1
    if synthetic_after and not args.allow_degraded:
        return 1
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    ensure_crypto_env()
    sources = [s.strip() for s in args.sources.split(",")] if args.sources else None
    gold = pull_gold(start=args.start, end=args.end, sources=sources)
    if "binance" in {s.strip().lower() for s in (sources or ["binance", "deribit", "mempool"])}:
        gold["perp_binance_api"] = supplement_perp_from_binance(start=args.start, end=args.end)
    if "deribit" in {s.strip().lower() for s in (sources or ["binance", "deribit", "mempool"])}:
        gold["dvol_deribit_api"] = supplement_dvol_from_deribit(start=args.start, end=args.end)
    mempool = {"written": 0}
    if args.with_mempool:
        try:
            mempool = {"written": pull_mempool_backfill(hours=args.mempool_hours, interval_minutes=15)}
        except Exception as exc:
            mempool = {"written": 0, "error": str(exc)}
    paths = normalize_all(start=args.start, end=args.end)
    print(json.dumps({"gold": gold, "mempool": mempool, "normalized": {k: str(v) for k, v in paths.items()}}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="crypto_lane.pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("discover").set_defaults(func=cmd_discover)
    sub.add_parser("manifest").set_defaults(func=cmd_manifest)
    p_env = sub.add_parser("env-check")
    p_env.add_argument("--mempool-start", default=None, help="Optional date for mempool preflight")
    p_env.add_argument("--mempool-end", default=None, help="Optional date for mempool preflight")
    p_env.add_argument(
        "--sync-chi404-node",
        action="store_true",
        help="SCP btc-node status, env, and mempool jsonl from chi404 before check",
    )
    p_env.set_defaults(func=cmd_env_check)

    p_sync = sub.add_parser(
        "sync-node-host",
        help="Sync btc-node status/env/mempool gold from remote host (default chi404)",
    )
    p_sync.add_argument("--host", default="chi404")
    p_sync.set_defaults(func=cmd_sync_node_host)

    p_mpf = sub.add_parser("mempool-preflight", help="Probe B2 mempool gold and CAE btc-node status")
    p_mpf.add_argument("--start", required=True)
    p_mpf.add_argument("--end", required=True)
    p_mpf.set_defaults(func=cmd_mempool_preflight)

    p_smoke = sub.add_parser("smoke")
    p_smoke.add_argument("--candidate", default=None)
    p_smoke.add_argument(
        "--production",
        action="store_true",
        help="Use *_production.yaml backtest configs (requires normalized data)",
    )
    p_smoke.set_defaults(func=cmd_smoke)

    p_ftg = sub.add_parser(
        "fill-test-gaps",
        help="Orchestrate crypto data gap-fill for full-year production testing",
    )
    p_ftg.add_argument("--dry-run", action="store_true", help="Preflight and audit only")
    p_ftg.add_argument(
        "--sync-chi404-node",
        action="store_true",
        help="SCP btc-node status/env/mempool jsonl from chi404",
    )
    p_ftg.add_argument("--skip-chi404", action="store_true", help="Do not attempt chi404 sync")
    p_ftg.add_argument("--ws-rtt-ms", type=float, default=None, help="Live-measured WS RTT for pit_strict")
    p_ftg.add_argument(
        "--force-replace-synthetic",
        action="store_true",
        help="Purge synthetic bookticker even when preflight says unsafe",
    )
    p_ftg.add_argument(
        "--allow-degraded",
        action="store_true",
        help="Fill remaining bookticker gaps from perp klines",
    )
    p_ftg.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue pipeline after pull_gold/normalize failures (default: fail-fast)",
    )
    p_ftg.set_defaults(func=cmd_fill_test_gaps)

    p_gold = sub.add_parser("pull-gold", help="Download production gold parquet from crypto-alpha-datasets")
    p_gold.add_argument("--start", required=True)
    p_gold.add_argument("--end", required=True)
    p_gold.add_argument("--sources", default=None, help="binance,deribit,mempool")
    p_gold.set_defaults(func=cmd_pull_gold)

    p_bronze = sub.add_parser("pull-bronze", help="Deprecated alias for pull-gold")
    p_bronze.add_argument("--start", required=True)
    p_bronze.add_argument("--end", required=True)
    p_bronze.add_argument("--sources", default=None, help="binance,deribit,mempool")
    p_bronze.set_defaults(func=cmd_pull_bronze)

    p_mp = sub.add_parser("pull-mempool")
    p_mp.add_argument("--hours", type=int, default=24)
    p_mp.add_argument("--interval-minutes", type=int, default=15)
    p_mp.add_argument("--samples", type=int, default=0)
    p_mp.set_defaults(func=cmd_pull_mempool)

    p_bf = sub.add_parser(
        "backfill-blockspace",
        help="Historical block-fee proxy from synced bitcoind (not live mempool)",
    )
    p_bf.add_argument("--start", required=True)
    p_bf.add_argument("--end", required=True)
    p_bf.add_argument("--step-hours", type=int, default=1)
    p_bf.set_defaults(func=cmd_backfill_blockspace)

    p_bt = sub.add_parser(
        "pull-bookticker",
        help="Pull true L3 bookticker from B2 then Binance Vision",
    )
    p_bt.add_argument("--start", required=True)
    p_bt.add_argument("--end", required=True)
    p_bt.add_argument("--sleep-s", type=float, default=0.2)
    p_bt.add_argument("--max-days", type=int, default=None)
    p_bt.set_defaults(func=cmd_pull_bookticker)

    p_l3 = sub.add_parser(
        "fill-l3-gaps",
        help="Backfill missing BTC futures_um_bookticker_tick (B2 → Vision → degraded)",
    )
    p_l3.add_argument("--start", required=True)
    p_l3.add_argument("--end", required=True)
    p_l3.add_argument("--audit-only", action="store_true")
    p_l3.add_argument(
        "--dry-run",
        action="store_true",
        help="Preflight B2/Vision fillability; no downloads or deletes",
    )
    p_l3.add_argument("--replace-synthetic", action="store_true")
    p_l3.add_argument(
        "--force",
        action="store_true",
        help="Allow --replace-synthetic even when preflight says purge is unsafe",
    )
    p_l3.add_argument(
        "--allow-degraded",
        action="store_true",
        help="Fill remaining gaps from perp klines (not production-grade L3)",
    )
    p_l3.add_argument("--sleep-s", type=float, default=0.2)
    p_l3.add_argument("--max-days", type=int, default=None)
    p_l3.set_defaults(func=cmd_fill_l3_gaps)

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
    p_cal.add_argument(
        "--live-measured",
        action="store_true",
        help="Tag venue_profiles.json source as live_measured (requires --ws-rtt-ms)",
    )
    p_cal.add_argument("--measure-node", action="store_true")
    p_cal.add_argument("--tunnel-rtt-ms", type=float, default=None)
    p_cal.set_defaults(func=cmd_calibrate_ws_rtt)

    p_probe = sub.add_parser("probe-ws-rtt", help="Deprecated alias for calibrate-ws-rtt")
    p_probe.add_argument("--venue", default="binance_perp")
    p_probe.add_argument("--ws-rtt-ms", type=float, default=None)
    p_probe.add_argument("--live-measured", action="store_true")
    p_probe.add_argument("--measure-node", action="store_true")
    p_probe.add_argument("--tunnel-rtt-ms", type=float, default=None)
    p_probe.set_defaults(func=cmd_calibrate_ws_rtt)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
