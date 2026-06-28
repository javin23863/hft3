#!/usr/bin/env python
"""Run the active HftBacktest-only pipeline slice."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "packages")]

from backtest_pipeline.src.hftbacktest_only_pipeline import (
    HftBacktestOnlyRunConfig,
    run_hftbacktest_only,
)


def _default_run_id() -> str:
    return f"hbt_only_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def _load_strategy_params(value: str | None, path: Path | None) -> dict:
    if path is not None:
        return json.loads(path.read_text(encoding="utf-8"))
    if value:
        return json.loads(value)
    return {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run HftBacktest-only active pipeline")
    parser.add_argument("--data-npz", type=Path, required=True)
    parser.add_argument("--initial-snapshot", type=Path, required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--strategy-id", required=True)
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

    run_id = args.run_id or _default_run_id()
    strategy_params = _load_strategy_params(args.strategy_params_json, args.strategy_params_file)
    config = HftBacktestOnlyRunConfig(
        run_id=run_id,
        symbol=args.symbol,
        contract=args.contract,
        event_id=args.event_id,
        normalized_npz=args.data_npz.resolve(),
        initial_snapshot=args.initial_snapshot.resolve(),
        strategy_id=args.strategy_id,
        strategy_params=strategy_params,
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
