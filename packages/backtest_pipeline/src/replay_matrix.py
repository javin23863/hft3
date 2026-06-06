"""ReplaySession-backed per-hypothesis matrix (replaces SignalBacktester fills)."""
from __future__ import annotations

import warnings
from typing import Any, Dict, List, Optional

import numpy as np

from backtest_pipeline.src.hypothesis_replay_strategy import HypothesisReplayStrategy
from backtest_pipeline.src.signal_backtester import BacktestResult, FillRecord
from features_engine.src.hypotheses.modules import BaseHypothesis
from replay.replay_session import ReplaySession, ReplaySessionConfig


_FILL_EVENT_TYPES = {"ORDER_FILLED", "ORDER_PARTIALLY_FILLED"}


def _fill_records_from_lifecycle(
    fill_events: List[Dict[str, Any]],
    *,
    hypothesis_id: int,
) -> List[FillRecord]:
    fills: List[FillRecord] = []
    for row in fill_events:
        if row.get("event_type") not in _FILL_EVENT_TYPES:
            continue
        filled_quantity = float(row.get("filled_quantity") or row.get("quantity") or 0.0)
        if filled_quantity <= 0:
            continue
        price = row.get("avg_fill_price") or row.get("price")
        fills.append(
            FillRecord(
                timestamp_ns=int(row.get("timestamp_ns") or row.get("replay_time_ns") or 0),
                side=str(row.get("side") or "").upper(),
                exec_price=float(price or 0.0),
                qty=max(1, int(round(filled_quantity))),
                hypothesis_id=hypothesis_id,
                signal=0.0,
                mid_at_exec=float(price or 0.0),
            )
        )
    return fills


def run_hypothesis_replay(
    hypothesis: BaseHypothesis,
    npz_path: str,
    *,
    latency_ms: float = 1.0,
    signal_threshold: float = 0.15,
    max_steps: int | None = None,
    events: Optional[np.ndarray] = None,
) -> BacktestResult:
    strategy = HypothesisReplayStrategy(hypothesis, signal_threshold=signal_threshold)
    cfg = ReplaySessionConfig(
        npz_path=npz_path,
        events=events,
        latency_ms=latency_ms,
        max_steps=max_steps,
    )
    result = ReplaySession(cfg, strategy).run()
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

    replay_num_trades = int(result.get("num_trades", 0))
    pnl = float(result.get("balance", 0.0))
    lifecycle_fills = result.get("fill_events")
    fills = _fill_records_from_lifecycle(
        lifecycle_fills if isinstance(lifecycle_fills, list) else [],
        hypothesis_id=hypothesis.hyp_id,
    )
    if replay_num_trades > 0 and not fills:
        raise RuntimeError("ReplaySession reported trades but emitted no lifecycle fill events")
    if replay_num_trades != len(fills):
        raise RuntimeError(
            "ReplaySession trade count does not match lifecycle fill events: "
            f"num_trades={replay_num_trades}, lifecycle_fills={len(fills)}"
        )
    num_trades = len(fills)

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
    events: Optional[np.ndarray] = None,
) -> Dict[int, BacktestResult]:
    return {
        h.hyp_id: run_hypothesis_replay(
            h, npz_path, latency_ms=latency_ms, signal_threshold=signal_threshold, events=events
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
