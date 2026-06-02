"""ReplaySession-backed per-hypothesis matrix (replaces SignalBacktester fills)."""
from __future__ import annotations

import warnings
from typing import Dict, List

import numpy as np

from backtest_pipeline.src.hypothesis_replay_strategy import HypothesisReplayStrategy
from backtest_pipeline.src.signal_backtester import BacktestResult, FillRecord
from features_engine.src.hypotheses.modules import BaseHypothesis
from replay.replay_session import ReplaySession, ReplaySessionConfig


def run_hypothesis_replay(
    hypothesis: BaseHypothesis,
    npz_path: str,
    *,
    latency_ms: float = 1.0,
    signal_threshold: float = 0.15,
    max_steps: int | None = None,
    imbalance_ablation_mode_id: str = "",
    auction_events: list | None = None,
    event_window_id: str = "",
    meta_out: dict | None = None,
) -> BacktestResult:
    strategy = HypothesisReplayStrategy(hypothesis, signal_threshold=signal_threshold)
    cfg = ReplaySessionConfig(
        npz_path=npz_path,
        latency_ms=latency_ms,
        max_steps=max_steps,
        imbalance_ablation_mode_id=imbalance_ablation_mode_id,
        auction_events=list(auction_events or []),
        event_window_id=event_window_id,
    )
    session = ReplaySession(cfg, strategy)
    raw = session.run()
    if meta_out is not None:
        meta_out["imbalance_snapshot_summary"] = raw.get("imbalance_snapshot_summary")
        meta_out["imbalance_samples"] = raw.get("imbalance_samples", [])
    result = raw
    if result.get("error"):
        return BacktestResult(
            hypothesis_id=hypothesis.hyp_id,
            net_pnl=0.0,
            num_trades=0,
            win_rate=0.0,
            expectancy=0.0,
            adverse_selection_ticks=0.0,
            tail_loss=0.0,
        )

    num_trades = int(result.get("num_trades", 0))
    pnl = float(result.get("balance", 0.0))
    fills: List[FillRecord] = []
    if num_trades > 0:
        fills.append(
            FillRecord(
                timestamp_ns=int(result.get("order_lifecycle_summary", {}).get("replay_end_time_ns", 0)),
                side="BUY",
                exec_price=0.0,
                qty=1,
                hypothesis_id=hypothesis.hyp_id,
                signal=0.0,
                mid_at_exec=0.0,
            )
        )

    return BacktestResult(
        hypothesis_id=hypothesis.hyp_id,
        net_pnl=pnl,
        num_trades=num_trades,
        win_rate=1.0 if pnl > 0 and num_trades > 0 else 0.0,
        expectancy=pnl / max(num_trades, 1),
        adverse_selection_ticks=0.0,
        tail_loss=min(0.0, pnl),
        fills=fills,
    )


def run_all_hypotheses_replay(
    hypotheses: List[BaseHypothesis],
    npz_path: str,
    latency_ms: float = 1.0,
    signal_threshold: float = 0.15,
) -> Dict[int, BacktestResult]:
    return {
        h.hyp_id: run_hypothesis_replay(
            h, npz_path, latency_ms=latency_ms, signal_threshold=signal_threshold
        )
        for h in hypotheses
    }


def run_latency_matrix_replay(
    hypotheses: List[BaseHypothesis],
    npz_path: str,
    latency_bands: List[float],
    signal_threshold: float = 0.15,
) -> Dict[float, Dict[int, BacktestResult]]:
    return {
        lat: run_all_hypotheses_replay(
            hypotheses, npz_path, latency_ms=lat, signal_threshold=signal_threshold
        )
        for lat in latency_bands
    }


def deprecate_signal_backtester_fill_path() -> None:
    warnings.warn(
        "SignalBacktester internal fill simulation is deprecated; "
        "use replay_matrix.run_hypothesis_replay / ReplaySession instead.",
        DeprecationWarning,
        stacklevel=2,
    )
