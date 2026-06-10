"""Benchmark ReplaySession stepping modes on one event NPZ.

Usage: python scripts/bench_replay_session.py [npz_path] [--modes grid,event]
Prints wall-clock, steps, trades, balance per mode so stepping changes can be
compared for both speed and behavioral drift.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

from backtest_pipeline.src.hypothesis_replay_strategy import HypothesisReplayStrategy
from features_engine.src.hypotheses.registry import get_active_hypotheses
from replay.replay_session import ReplaySession, ReplaySessionConfig


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("npz", nargs="?", default="data/npz/MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz")
    p.add_argument("--modes", default="grid,event")
    p.add_argument("--hyp-index", type=int, default=0)
    p.add_argument("--latency-ms", type=float, default=1.0)
    args = p.parse_args()

    hyp = get_active_hypotheses()[args.hyp_index]
    for mode in args.modes.split(","):
        strategy = HypothesisReplayStrategy(hyp)
        cfg = ReplaySessionConfig(
            npz_path=args.npz, latency_ms=args.latency_ms, step_mode=mode
        )
        t0 = time.time()
        res = ReplaySession(cfg, strategy).run()
        dt = time.time() - t0
        print(
            f"{mode}: {dt:.1f}s steps={res['steps']} trades={res['num_trades']} "
            f"balance={res['balance']:.2f} intents={res['order_intent_count']} "
            f"fills={len(res.get('fill_events', []))}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
