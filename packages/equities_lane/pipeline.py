"""CLI for low-float equities research lane."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_LANE = Path(__file__).resolve().parent
_REPO = _LANE.parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "packages"))

from equities_lane.src.backtest.low_float_backtester import LowFloatBacktester
from equities_lane.src.backtest.walk_forward import assert_no_float_lookahead, generate_folds
from equities_lane.src.config_loader import load_universe
from equities_lane.src.ingest.databento_equities import collect_download_specs, download_session
from equities_lane.src.ingest.decadal_pull import estimate_catalog_cost, pull_catalog
from equities_lane.src.ingest.normalize import normalize_dbn, normalize_fixture
from equities_lane.src.report.experiment_report import run_experiment
from equities_lane.src.screen.universe_screener import screen_session

_DEFAULT_CONFIG = _LANE / "config" / "universe.yaml"
_DECADAL_CONFIG = _LANE / "config" / "decadal_runners.yaml"
_FIXTURE = _LANE / "fixtures" / "low_float_session_v1.ndjson"


def cmd_discover(args: argparse.Namespace) -> int:
    jobs = collect_download_specs(args.config)
    _, universe, paths = load_universe(args.config)
    payload = {
        "sessions": [s.id for s in universe.sessions],
        "download_jobs": jobs,
        "paths": {k: str(v) for k, v in paths.items()},
    }
    print(json.dumps(payload, indent=2))
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    dest = download_session(args.config, args.symbol, args.date, use_mbo=args.mbo)
    print(json.dumps({"raw_path": str(dest)}, indent=2))
    return 0


def cmd_normalize(args: argparse.Namespace) -> int:
    _, universe, paths = load_universe(args.config)
    raw = Path(args.raw)
    symbol = args.symbol or "UNKNOWN"
    session_date = args.date or "1970-01-01"
    out = paths["normalized_root"] / f"{symbol}_{session_date}.ndjson"
    schema = args.schema
    if universe.l3_only and schema != "mbo":
        raise SystemExit("L3-only lane: normalize requires schema=mbo")
    if args.fixture:
        normalize_fixture(Path(args.fixture), out, degraded=True)
    else:
        normalize_dbn(raw, out, symbol, session_date, schema=schema)
    print(json.dumps({"normalized_path": str(out)}, indent=2))
    return 0


def cmd_screen(args: argparse.Namespace) -> int:
    _, universe, paths = load_universe(args.config)
    session = Path(args.session)
    result = screen_session(
        session,
        universe.filters,
        paths["float_metadata"],
        paths["daily_bars"],
        daily_bars_fallback=paths.get("daily_bars_fixture"),
        l3_only=universe.l3_only,
        allow_degraded=args.allow_degraded,
    )
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.passed else 1


def cmd_backtest(args: argparse.Namespace) -> int:
    _, universe, _ = load_universe(args.config)
    session = Path(args.session)
    bt = LowFloatBacktester(universe)
    result = bt.run(str(session), ablation=args.ablation, allow_degraded=args.allow_degraded)
    print(json.dumps(result.to_dict(), indent=2))
    return 0


def cmd_fixture_backtest(args: argparse.Namespace) -> int:
    args.config = args.config or str(_DEFAULT_CONFIG)
    args.session = str(_FIXTURE)
    args.allow_degraded = True
    return cmd_backtest(args)


def cmd_experiment(args: argparse.Namespace) -> int:
    _, universe, paths = load_universe(args.config)
    session = Path(args.session or _FIXTURE)
    out = run_experiment(
        str(session),
        universe,
        paths["reports_root"],
        ablation=args.ablation,
    )
    print(json.dumps({"report_dir": str(out)}, indent=2))
    return 0


def cmd_walk_forward(args: argparse.Namespace) -> int:
    _, universe, paths = load_universe(args.config)
    folds = generate_folds(args.start, args.end, universe)
    lookahead = assert_no_float_lookahead(
        str(paths["float_metadata"]),
        args.symbol,
        args.session_date,
    )
    print(
        json.dumps(
            {"folds": [f.__dict__ for f in folds], "lookahead_check": lookahead},
            indent=2,
        )
    )
    return 0


def cmd_estimate_decadal(args: argparse.Namespace) -> int:
    config = args.decadal_config or str(_DECADAL_CONFIG)
    rows = estimate_catalog_cost(config, session_id=args.session_id)
    total = sum(r.get("total_cost_usd") or 0 for r in rows)
    print(json.dumps({"estimates": rows, "total_cost_usd": total}, indent=2))
    return 0


def cmd_pull_decadal(args: argparse.Namespace) -> int:
    config = args.decadal_config or str(_DECADAL_CONFIG)
    result = pull_catalog(
        config,
        session_id=args.session_id,
        dry_run=args.dry_run,
        override_hard_limit=args.override_hard_limit,
        override_operating_cap=args.override_operating_cap,
        resume=args.resume,
        refresh_daily=args.refresh_daily,
        daily_only=args.daily_only,
        pull_options=args.pull_options,
        options_only=args.options_only,
        refresh_options=args.refresh_options,
    )
    print(json.dumps(result, indent=2, default=str))
    failed = [s for s in result.get("sessions", []) if s.get("status") == "failed"]
    if args.options_only and not args.dry_run:
        failed.extend(
            s for s in result.get("sessions", [])
            if s.get("status") == "options_pulled" and (s.get("options") or {}).get("pull_error")
        )
    return 1 if failed and not args.dry_run else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="equities_lane.pipeline")
    parser.add_argument("--config", default=str(_DEFAULT_CONFIG))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("discover").set_defaults(func=cmd_discover)

    p_dl = sub.add_parser("download")
    p_dl.add_argument("--symbol", required=True)
    p_dl.add_argument("--date", required=True)
    p_dl.add_argument("--mbo", action="store_true")
    p_dl.set_defaults(func=cmd_download)

    p_norm = sub.add_parser("normalize")
    p_norm.add_argument("--raw")
    p_norm.add_argument("--symbol")
    p_norm.add_argument("--date")
    p_norm.add_argument("--schema", default="mbo")
    p_norm.add_argument("--fixture")
    p_norm.set_defaults(func=cmd_normalize)

    p_scr = sub.add_parser("screen")
    p_scr.add_argument("--session", required=True)
    p_scr.add_argument(
        "--allow-degraded",
        action="store_true",
        help="CI fixture only; real research paths require MBO L3",
    )
    p_scr.set_defaults(func=cmd_screen)

    p_bt = sub.add_parser("backtest")
    p_bt.add_argument("--session", required=True)
    p_bt.add_argument("--ablation")
    p_bt.add_argument(
        "--allow-degraded",
        action="store_true",
        help="CI fixture only; real research paths require MBO L3",
    )
    p_bt.set_defaults(func=cmd_backtest)

    p_fix = sub.add_parser("fixture-backtest")
    p_fix.add_argument("--ablation")
    p_fix.set_defaults(func=cmd_fixture_backtest)

    p_exp = sub.add_parser("experiment")
    p_exp.add_argument("--session")
    p_exp.add_argument("--ablation", default="all")
    p_exp.set_defaults(func=cmd_experiment)

    p_wf = sub.add_parser("walk-forward")
    p_wf.add_argument("--start", required=True)
    p_wf.add_argument("--end", required=True)
    p_wf.add_argument("--symbol", default="RUNNER")
    p_wf.add_argument("--session-date", default="2024-03-15")
    p_wf.set_defaults(func=cmd_walk_forward)

    p_est = sub.add_parser("estimate-decadal")
    p_est.add_argument("--decadal-config", default=str(_DECADAL_CONFIG))
    p_est.add_argument("--session-id")
    p_est.set_defaults(func=cmd_estimate_decadal)

    p_pull = sub.add_parser("pull-decadal")
    p_pull.add_argument("--decadal-config", default=str(_DECADAL_CONFIG))
    p_pull.add_argument("--session-id")
    p_pull.add_argument("--dry-run", action="store_true")
    p_pull.add_argument("--override-hard-limit", action="store_true")
    p_pull.add_argument("--override-operating-cap", action="store_true")
    p_pull.add_argument("--resume", action="store_true")
    p_pull.add_argument(
        "--refresh-daily",
        action="store_true",
        help="Re-pull daily OHLCV when coverage < daily_lookback_days (756)",
    )
    p_pull.add_argument(
        "--daily-only",
        action="store_true",
        help="Refresh daily OHLCV only; skip MBO download/normalize",
    )
    p_pull.add_argument(
        "--pull-options",
        action="store_true",
        help="Also pull OPRA options chain for same session window",
    )
    p_pull.add_argument(
        "--options-only",
        action="store_true",
        help="Pull options chain only (equity must already be on disk)",
    )
    p_pull.add_argument(
        "--refresh-options",
        action="store_true",
        help="Re-download options even if normalized file exists",
    )
    p_pull.set_defaults(func=cmd_pull_decadal)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
