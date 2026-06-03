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
from equities_lane.src.prediction.trainer import (
    build_training_dataset,
    generate_predictions,
    load_models,
    run_timing_policy_analysis,
    run_walk_forward_validation,
    save_models,
    train_full_model,
    write_report,
)
from equities_lane.src.prediction.types import ModelConfig

_DEFAULT_CONFIG = _LANE / "config" / "universe.yaml"
_DECADAL_CONFIG = _LANE / "config" / "decadal_runners.yaml"
_RUNNER_BENCHMARK_CONFIG = _LANE / "config" / "historical_runner_benchmark.yaml"
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


def cmd_predict_train(args: argparse.Namespace) -> int:
    from equities_lane.src.ingest.daily_bars_io import load_daily_bars
    from equities_lane.src.ingest.float_metadata import load_float_csv

    _, universe, paths = load_universe(args.config)
    config = ModelConfig()

    daily_root = paths.get("daily_bars", paths.get("daily_root", _REPO / "data" / "equities" / "daily"))
    symbol_bars: dict[str, list] = {}
    for sym_file in Path(daily_root).glob("*.parquet"):
        sym = sym_file.stem
        symbol_bars[sym] = load_daily_bars(str(sym_file), sym)
    for sym_file in Path(daily_root).glob("*.csv"):
        sym = sym_file.stem
        symbol_bars[sym] = load_daily_bars(str(sym_file), sym)

    float_csv = paths.get("float_metadata", _REPO / "data" / "equities" / "metadata" / "float_pit.csv")
    float_records: dict[str, list] = {}
    if Path(float_csv).exists():
        recs = load_float_csv(str(float_csv))
        for rec in recs:
            float_records.setdefault(rec.symbol, []).append(rec)

    X, dates, label_dict, labels = build_training_dataset(symbol_bars, float_records, config)
    if len(X) == 0:
        print(json.dumps({"error": "no training data available"}, indent=2))
        return 1

    hazard, payoff, risk, metrics = train_full_model(X, label_dict, config)

    output_dir = Path(args.output or str(_REPO / "research_cards" / "equities" / "prediction_model"))
    save_models(hazard, payoff, risk, output_dir)

    fi = hazard.feature_importance(horizon=5)
    report_path = write_report(metrics, [], [], fi, output_dir)

    print(json.dumps({
        "model_dir": str(output_dir),
        "n_samples": len(X),
        "n_runners": int(label_dict["runner_labels"].sum()),
        "base_rate": float(label_dict["runner_labels"].mean()),
        "train_metrics": metrics,
        "report": str(report_path),
    }, indent=2))
    return 0


def cmd_predict_validate(args: argparse.Namespace) -> int:
    from equities_lane.src.ingest.daily_bars_io import load_daily_bars
    from equities_lane.src.ingest.float_metadata import load_float_csv

    _, universe, paths = load_universe(args.config)
    config = ModelConfig(
        walk_forward_n_folds=args.folds or 5,
        walk_forward_embargo_days=args.embargo or 5,
    )

    daily_root = paths.get("daily_bars", paths.get("daily_root", _REPO / "data" / "equities" / "daily"))
    symbol_bars: dict[str, list] = {}
    for sym_file in Path(daily_root).glob("*.parquet"):
        sym = sym_file.stem
        symbol_bars[sym] = load_daily_bars(str(sym_file), sym)
    for sym_file in Path(daily_root).glob("*.csv"):
        sym = sym_file.stem
        symbol_bars[sym] = load_daily_bars(str(sym_file), sym)

    float_csv = paths.get("float_metadata", _REPO / "data" / "equities" / "metadata" / "float_pit.csv")
    float_records: dict[str, list] = {}
    if Path(float_csv).exists():
        recs = load_float_csv(str(float_csv))
        for rec in recs:
            float_records.setdefault(rec.symbol, []).append(rec)

    X, dates, label_dict, labels = build_training_dataset(symbol_bars, float_records, config)
    if len(X) == 0:
        print(json.dumps({"error": "no data available for validation"}, indent=2))
        return 1

    wf_metrics = run_walk_forward_validation(X, dates, label_dict, config)

    timing_reports = run_timing_policy_analysis(X, dates, label_dict, config)

    output_dir = Path(args.output or str(_REPO / "research_cards" / "equities" / "prediction_validation"))
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "walk_forward.json").write_text(
        json.dumps([m.to_dict() for m in wf_metrics], indent=2)
    )
    (output_dir / "timing_policies.json").write_text(
        json.dumps([t.to_dict() for t in timing_reports], indent=2)
    )

    print(json.dumps({
        "walk_forward": [m.to_dict() for m in wf_metrics],
        "timing_policies": [t.to_dict() for t in timing_reports],
        "output_dir": str(output_dir),
    }, indent=2))
    return 0


def cmd_predict_score(args: argparse.Namespace) -> int:
    from equities_lane.src.ingest.daily_bars_io import load_daily_bars
    from equities_lane.src.ingest.float_metadata import load_float_csv

    _, universe, paths = load_universe(args.config)
    config = ModelConfig()

    model_dir = Path(args.model_dir or str(_REPO / "research_cards" / "equities" / "prediction_model"))
    hazard, payoff, risk = load_models(model_dir, config)

    daily_root = paths.get("daily_bars", paths.get("daily_root", _REPO / "data" / "equities" / "daily"))
    symbol_bars: dict[str, list] = {}
    for sym_file in Path(daily_root).glob("*.parquet"):
        sym = sym_file.stem
        symbol_bars[sym] = load_daily_bars(str(sym_file), sym)
    for sym_file in Path(daily_root).glob("*.csv"):
        sym = sym_file.stem
        symbol_bars[sym] = load_daily_bars(str(sym_file), sym)

    float_csv = paths.get("float_metadata", _REPO / "data" / "equities" / "metadata" / "float_pit.csv")
    float_records: dict[str, list] = {}
    if Path(float_csv).exists():
        recs = load_float_csv(str(float_csv))
        for rec in recs:
            float_records.setdefault(rec.symbol, []).append(rec)

    predictions = generate_predictions(
        symbol_bars, float_records, hazard, payoff, risk, config
    )

    output_dir = Path(args.output or str(_REPO / "research_cards" / "equities" / "predictions"))
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "predictions.json"
    out_path.write_text(json.dumps(predictions, indent=2))

    print(json.dumps({
        "n_predictions": len(predictions),
        "top_5": predictions[:5],
        "output_path": str(out_path),
    }, indent=2))
    return 0


def cmd_l3_extract(args: argparse.Namespace) -> int:
    from equities_lane.src.prediction.l3.event_types import MBOEvent
    from equities_lane.src.prediction.l3.features import L3FeatureExtractor
    from equities_lane.src.prediction.l3.queue_state import OrderBookReconstructor

    session_path = Path(args.session)
    if not session_path.exists():
        print(json.dumps({"error": f"session file not found: {session_path}"}, indent=2))
        return 1

    events = []
    with open(session_path) as f:
        for line in f:
            if line.strip():
                data = json.loads(line)
                if "ts_event_ns" in data:
                    events.append(MBOEvent.from_dict(data))

    if not events:
        print(json.dumps({"error": "no MBO events found in session"}, indent=2))
        return 1

    reconstructor = OrderBookReconstructor()
    snapshots = []
    for event in events:
        snap = reconstructor.process_event(event)
        if snap:
            snapshots.append(snap)

    extractor = L3FeatureExtractor(window_ns=args.window_ns)
    features = extractor.extract(events, snapshots)

    output_dir = Path(args.output or str(_REPO / "research_cards" / "equities" / "l3_features"))
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{args.symbol}_l3_features.json"
    out_path.write_text(json.dumps(features.to_dict(), indent=2))

    print(json.dumps({
        "symbol": args.symbol,
        "n_events": len(events),
        "n_snapshots": len(snapshots),
        "n_features": len(features.to_dict()),
        "output_path": str(out_path),
    }, indent=2))
    return 0


def cmd_l3_snapshot(args: argparse.Namespace) -> int:
    from equities_lane.src.prediction.l3.event_types import MBOEvent
    from equities_lane.src.prediction.l3.snapshots import L3SnapshotBuilder, L3SnapshotType

    session_path = Path(args.session)
    if not session_path.exists():
        print(json.dumps({"error": f"session file not found: {session_path}"}, indent=2))
        return 1

    events = []
    with open(session_path) as f:
        for line in f:
            if line.strip():
                data = json.loads(line)
                if "ts_event_ns" in data:
                    events.append(MBOEvent.from_dict(data))

    if not events:
        print(json.dumps({"error": "no MBO events found in session"}, indent=2))
        return 1

    builder = L3SnapshotBuilder(args.symbol)
    for event in events:
        builder.add_event(event)

    snapshot_type = L3SnapshotType(args.snapshot_type)
    snapshots = builder.build_multi_resolution_snapshots(snapshot_type)

    output_dir = Path(args.output or str(_REPO / "research_cards" / "equities" / "l3_snapshots"))
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{args.symbol}_l3_snapshots.json"
    out_path.write_text(json.dumps([s.to_dict() for s in snapshots], indent=2))

    print(json.dumps({
        "symbol": args.symbol,
        "n_events": len(events),
        "n_snapshots": len(snapshots),
        "snapshot_type": args.snapshot_type,
        "output_path": str(out_path),
    }, indent=2))
    return 0


def cmd_l3_integrate(args: argparse.Namespace) -> int:
    from equities_lane.src.prediction.l3.event_types import MBOEvent
    from equities_lane.src.prediction.l3.integration import L3IntegrationLayer
    from equities_lane.src.prediction.types import HazardEstimate, ModelConfig

    session_path = Path(args.session)
    if not session_path.exists():
        print(json.dumps({"error": f"session file not found: {session_path}"}, indent=2))
        return 1

    events = []
    with open(session_path) as f:
        for line in f:
            if line.strip():
                data = json.loads(line)
                if "ts_event_ns" in data:
                    events.append(MBOEvent.from_dict(data))

    if not events:
        print(json.dumps({"error": "no MBO events found in session"}, indent=2))
        return 1

    config = ModelConfig()
    base_hazard = HazardEstimate(
        p_run_5d=0.15,
        p_run_2d=0.10,
        p_run_1d=0.05,
        p_afterhours_ignite=0.03,
        p_premarket_ignite=0.04,
        p_intraday_continuation=0.06,
    )

    integration = L3IntegrationLayer(config)
    enhanced = integration.process_event_stream(args.symbol, events, base_hazard)

    if not enhanced:
        print(json.dumps({"error": "failed to generate L3 enhanced prediction"}, indent=2))
        return 1

    output_dir = Path(args.output or str(_REPO / "research_cards" / "equities" / "l3_integrated"))
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{args.symbol}_l3_integrated.json"
    out_path.write_text(json.dumps(enhanced.to_dict(), indent=2))

    print(json.dumps({
        "symbol": args.symbol,
        "n_events": len(events),
        "base_p_run_5d": base_hazard.p_run_5d,
        "enhanced_p_run_5d": enhanced.enhanced_hazard.p_run_5d,
        "incremental_alpha": enhanced.incremental_alpha,
        "timing_recommendation": enhanced.l3_timing_recommendation,
        "reject_reasons": enhanced.l3_reject_reasons,
        "output_path": str(out_path),
    }, indent=2))
    return 0


def cmd_resolve_runner_seeds(args: argparse.Namespace) -> int:
    from equities_lane.src.prediction.runner_seed_resolver import resolve_runner_seed_events

    delisted_roots = list(getattr(args, "delisted_daily_roots", None) or [])
    result = resolve_runner_seed_events(
        args.seeds,
        daily_root=args.daily_root,
        output_dir=args.output,
        delisted_daily_roots=delisted_roots,
    )
    print(json.dumps(result, indent=2))
    return 0


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

    p_ptrain = sub.add_parser("predict-train")
    p_ptrain.add_argument("--output")
    p_ptrain.set_defaults(func=cmd_predict_train)

    p_pval = sub.add_parser("predict-validate")
    p_pval.add_argument("--folds", type=int, default=5)
    p_pval.add_argument("--embargo", type=int, default=5)
    p_pval.add_argument("--output")
    p_pval.set_defaults(func=cmd_predict_validate)

    p_pscore = sub.add_parser("predict-score")
    p_pscore.add_argument("--model-dir")
    p_pscore.add_argument("--output")
    p_pscore.set_defaults(func=cmd_predict_score)

    p_l3_extract = sub.add_parser("l3-extract")
    p_l3_extract.add_argument("--session", required=True)
    p_l3_extract.add_argument("--symbol", required=True)
    p_l3_extract.add_argument("--window-ns", type=int, default=1_000_000_000)
    p_l3_extract.add_argument("--output")
    p_l3_extract.set_defaults(func=cmd_l3_extract)

    p_l3_snapshot = sub.add_parser("l3-snapshot")
    p_l3_snapshot.add_argument("--session", required=True)
    p_l3_snapshot.add_argument("--symbol", required=True)
    p_l3_snapshot.add_argument("--snapshot-type", default="event_window")
    p_l3_snapshot.add_argument("--output")
    p_l3_snapshot.set_defaults(func=cmd_l3_snapshot)

    p_l3_integrate = sub.add_parser("l3-integrate")
    p_l3_integrate.add_argument("--session", required=True)
    p_l3_integrate.add_argument("--symbol", required=True)
    p_l3_integrate.add_argument("--model-dir")
    p_l3_integrate.add_argument("--output")
    p_l3_integrate.set_defaults(func=cmd_l3_integrate)

    p_seed = sub.add_parser(
        "resolve-runner-seeds",
        help="Resolve runner seed events from free daily OHLCV (no paid market data downloads).",
    )
    p_seed.add_argument("--seeds", default=str(_RUNNER_BENCHMARK_CONFIG))
    p_seed.add_argument("--daily-root", default=None)
    p_seed.add_argument("--output", default=None)
    p_seed.add_argument(
        "--delisted-daily-roots",
        nargs="*",
        default=None,
        help="Optional fallback roots for delisted tickers (CSV/parquet per-ticker, DailyBar format).",
    )
    p_seed.set_defaults(func=cmd_resolve_runner_seeds)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
