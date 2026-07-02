#!/usr/bin/env python
"""Run the Gate-4 robustness evaluation over per-event HBT run stats.

Feeds chronological per-event stats_summary.json files (plus an optional
parameter-surface performance matrix) into
backtest_pipeline.src.hbt_only_gates.run_robustness_gate, writes
robustness_report.json into the target run directory, and refreshes that
run's promotion_decision.json. Fail-closed: missing events or a missing
surface matrix produce explicit blocker reasons, never a silent pass.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "packages")]

from backtest_pipeline.src.hbt_only_gates import (
    DEFAULT_MAX_PBO,
    DEFAULT_MIN_DSR,
    DEFAULT_MIN_EVENTS,
    DEFAULT_MIN_PSR,
    run_robustness_gate,
)
from backtest_pipeline.src.hftbacktest_only_pipeline import write_promotion_decision


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run HBT-only Gate-4 robustness evaluation")
    parser.add_argument(
        "--stats",
        type=Path,
        nargs="+",
        required=True,
        help="Per-event stats_summary.json paths in chronological event order.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="Target run directory (receives robustness_report.json + refreshed promotion_decision.json).",
    )
    parser.add_argument(
        "--performance-matrix",
        type=Path,
        default=None,
        help="JSON file with the parameter-surface matrix (rows=chronological events, cols=variants) for CSCV/PBO.",
    )
    parser.add_argument("--min-events", type=int, default=DEFAULT_MIN_EVENTS)
    parser.add_argument("--min-psr", type=float, default=DEFAULT_MIN_PSR)
    parser.add_argument("--min-dsr", type=float, default=DEFAULT_MIN_DSR)
    parser.add_argument("--max-pbo", type=float, default=DEFAULT_MAX_PBO)
    parser.add_argument("--n-trials", type=int, default=1)
    args = parser.parse_args(argv)

    event_stats = []
    for stats_path in args.stats:
        stats = json.loads(stats_path.read_text(encoding="utf-8"))
        event_stats.append(
            {
                "event_id": stats.get("event_id"),
                "realized_closed_trade_pnl": stats.get("realized_closed_trade_pnl"),
                "fills_count": stats.get("fills_count"),
                "stats_path": str(stats_path),
            }
        )
    matrix = None
    if args.performance_matrix is not None:
        matrix = json.loads(args.performance_matrix.read_text(encoding="utf-8"))

    report = run_robustness_gate(
        event_stats,
        out_dir=args.out_dir,
        min_events=args.min_events,
        min_psr=args.min_psr,
        min_dsr=args.min_dsr,
        max_pbo=args.max_pbo,
        n_trials=args.n_trials,
        performance_matrix=matrix,
    )
    decision = write_promotion_decision(args.out_dir)
    print(json.dumps({"robustness_report": report, "promotion_decision": decision}, indent=2, default=str))
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
