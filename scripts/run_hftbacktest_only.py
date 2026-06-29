#!/usr/bin/env python
"""Run the active HftBacktest-only pipeline slice."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "packages")]

from backtest_pipeline.src.hftbacktest_only_pipeline import (
    HftBacktestOnlyPrepareConfig,
    HftBacktestOnlyRunConfig,
    prepare_hftbacktest_only_l3_from_lake,
    run_hftbacktest_only,
)
from backtest_pipeline.src.hftbacktest_only_campaign_manifest import DEFAULT_AUTHORITY_REFS
from features_engine.src.model_registry import legacy_to_slug, resolve_model_id, slug_to_legacy


def _default_run_id() -> str:
    return f"hbt_only_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def _load_strategy_params(value: str | None, path: Path | None) -> dict:
    if path is not None:
        return json.loads(path.read_text(encoding="utf-8"))
    if value:
        return json.loads(value)
    return {}


def _trade_date_from_event_id(event_id: str) -> str:
    match = re.search(r"(20\d{2})_(\d{2})_(\d{2})", event_id)
    if not match:
        raise SystemExit("--trade-date is required when --event-id has no YYYY_MM_DD segment")
    return "-".join(match.groups())


def _canonical_model_identity(model_id: str | None) -> tuple[str, tuple[str, ...]]:
    if model_id is None or str(model_id).strip() == "":
        raise SystemExit("--model-id is required for active HftBacktest-only runs")
    candidate = str(model_id).strip()
    if candidate in legacy_to_slug():
        raise SystemExit("--model-id must be a canonical descriptive slug; legacy IDs are provenance only")
    try:
        canonical = resolve_model_id(candidate)
    except KeyError as exc:
        raise SystemExit(f"--model-id unknown canonical slug: {candidate}") from exc
    if canonical != candidate:
        raise SystemExit("--model-id must be a canonical descriptive slug; legacy IDs are provenance only")
    legacy_id = slug_to_legacy().get(canonical)
    return canonical, (legacy_id,) if legacy_id else ()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run HftBacktest-only active pipeline")
    parser.add_argument("--data-npz", type=Path, default=None)
    parser.add_argument("--initial-snapshot", type=Path, default=None)
    parser.add_argument("--source-npz", type=Path, default=None)
    parser.add_argument("--trade-date", default=None)
    parser.add_argument("--warmup-seconds", type=int, default=30)
    parser.add_argument("--prepare-out-root", type=Path, default=REPO / "data" / "hbt")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--force-prepare", action="store_true")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--model-id", default=None, help="Canonical descriptive slug from model_registry.yaml.")
    parser.add_argument("--strategy-id", default=None)
    parser.add_argument("--strategy-params-json", default=None)
    parser.add_argument("--strategy-params-file", type=Path, default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--out-root", type=Path, default=REPO / "artifacts" / "hbt_runs")
    parser.add_argument("--tick-size", type=float, default=0.25)
    parser.add_argument("--lot-size", type=float, default=1.0)
    parser.add_argument("--contract-size", type=float, default=1.0)
    parser.add_argument("--maker-fee", type=float, default=0.0)
    parser.add_argument("--taker-fee", type=float, default=0.0)
    parser.add_argument("--entry-latency-ns", type=int, default=100_000)
    parser.add_argument("--response-latency-ns", type=int, default=100_000)
    parser.add_argument("--exchange-fill-model", default="NoPartialFillExchange")
    parser.add_argument("--queue-model", default="L3FIFOQueueModel")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    data_npz = args.data_npz
    initial_snapshot = args.initial_snapshot
    if args.source_npz is not None:
        prepared = prepare_hftbacktest_only_l3_from_lake(
            HftBacktestOnlyPrepareConfig(
                source_npz=args.source_npz.resolve(),
                symbol=args.symbol,
                contract=args.contract,
                event_id=args.event_id,
                trade_date=args.trade_date or _trade_date_from_event_id(args.event_id),
                out_root=args.prepare_out_root.resolve(),
                warmup_seconds=args.warmup_seconds,
                tick_size=args.tick_size,
                lot_size=args.lot_size,
                contract_size=args.contract_size,
                force_rebuild=args.force_prepare,
            )
        )
        if args.prepare_only:
            print(json.dumps(prepared, indent=2, default=str))
            return 0
        data_npz = Path(str(prepared["normalized_npz"]))
        initial_snapshot = Path(str(prepared["initial_snapshot"]))

    if data_npz is None or initial_snapshot is None:
        parser.error("provide either --source-npz or both --data-npz and --initial-snapshot")
    if args.strategy_id is None:
        parser.error("--strategy-id is required unless --prepare-only is used with --source-npz")

    run_id = args.run_id or _default_run_id()
    strategy_params = _load_strategy_params(args.strategy_params_json, args.strategy_params_file)
    param_model_id = strategy_params.get("model_id")
    if args.model_id is not None and param_model_id is not None and str(param_model_id) != str(args.model_id):
        parser.error("--model-id must match strategy_params.model_id when both are provided")
    canonical_model_id, legacy_aliases = _canonical_model_identity(args.model_id or param_model_id)
    if canonical_model_id:
        strategy_params = {**strategy_params, "model_id": canonical_model_id}
    config = HftBacktestOnlyRunConfig(
        run_id=run_id,
        symbol=args.symbol,
        contract=args.contract,
        event_id=args.event_id,
        normalized_npz=data_npz.resolve(),
        initial_snapshot=initial_snapshot.resolve(),
        strategy_id=args.strategy_id,
        strategy_params=strategy_params,
        canonical_model_id=canonical_model_id,
        legacy_aliases=legacy_aliases,
        authority_refs=tuple(DEFAULT_AUTHORITY_REFS) if canonical_model_id else (),
        tick_size=args.tick_size,
        lot_size=args.lot_size,
        contract_size=args.contract_size,
        maker_fee=args.maker_fee,
        taker_fee=args.taker_fee,
        entry_latency_ns=args.entry_latency_ns,
        response_latency_ns=args.response_latency_ns,
        exchange_fill_model=args.exchange_fill_model,
        queue_model=args.queue_model,
    )
    out_dir = (args.out_root / run_id).resolve()
    result = run_hftbacktest_only(config, out_dir=out_dir, dry_run=args.dry_run)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result["status"] in {"completed", "dry_run"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
