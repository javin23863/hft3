"""ReplaySession-backed per-hypothesis hypothesis matrix."""
from __future__ import annotations

from collections import deque
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from backtest_pipeline.src.hypothesis_replay_strategy import HypothesisReplayStrategy
from backtest_pipeline.src.backtest_result import BacktestResult, FillRecord
from features_engine.src.hypotheses.modules import BaseHypothesis
from replay.replay_session import ReplaySession, ReplaySessionConfig


def _fill_metrics(
    fill_events: List[Dict[str, Any]],
    hyp_id: int,
    tick_size: float,
) -> Tuple[List[FillRecord], List[float], List[float]]:
    """Build FillRecords, FIFO round-trip PnLs, and per-fill adverse selection.

    Round-trip PnL is in price-point * qty units (matching hbt's balance
    accounting). Adverse selection per fill is measured in ticks against the
    mid observed at drain time: positive means the market moved against the
    fill immediately after execution.
    """
    fills: List[FillRecord] = []
    trade_pnls: List[float] = []
    as_ticks: List[float] = []

    open_side = 0  # +1 long lots, -1 short lots, 0 flat
    open_lots: deque = deque()  # [price, qty]

    for f in fill_events:
        side = str(f["side"]).upper()
        px = float(f["exec_price"])
        qty = float(f["qty"])
        if qty <= 0.0 or side not in ("BUY", "SELL"):
            continue

        best_bid = f.get("best_bid")
        best_ask = f.get("best_ask")
        mid = 0.0
        if best_bid and best_ask:
            mid = (float(best_bid) + float(best_ask)) / 2.0
            adverse = (px - mid) if side == "BUY" else (mid - px)
            as_ticks.append(adverse / tick_size)

        fills.append(
            FillRecord(
                timestamp_ns=int(f["timestamp_ns"]),
                side=side,
                exec_price=px,
                qty=qty,
                hypothesis_id=hyp_id,
                signal=0.0,
                mid_at_exec=mid,
            )
        )

        sign = 1 if side == "BUY" else -1
        if open_side in (0, sign):
            open_lots.append([px, qty])
            open_side = sign
            continue
        remaining = qty
        while remaining > 1e-12 and open_lots:
            lot = open_lots[0]
            matched = min(remaining, lot[1])
            trade_pnls.append((px - lot[0]) * matched * open_side)
            lot[1] -= matched
            remaining -= matched
            if lot[1] <= 1e-12:
                open_lots.popleft()
        if remaining > 1e-12:
            open_lots.append([px, remaining])
            open_side = sign
        elif not open_lots:
            open_side = 0

    return fills, trade_pnls, as_ticks


def run_hypothesis_replay(
    hypothesis: BaseHypothesis,
    npz_path: str,
    *,
    latency_ms: float = 1.0,
    signal_threshold: float = 0.15,
    max_steps: int | None = None,
    events: Optional[np.ndarray] = None,
    tick_size: float = 0.25,
    cross_asset_npz: Optional[Dict[str, str]] = None,
    sensor_feature_npz: Optional[Dict[str, str]] = None,
) -> BacktestResult:
    strategy = HypothesisReplayStrategy(hypothesis, signal_threshold=signal_threshold)
    cfg = ReplaySessionConfig(
        npz_path=npz_path,
        events=events,
        latency_ms=latency_ms,
        max_steps=max_steps,
        tick_size=tick_size,
        cross_asset_npz=cross_asset_npz or {},
        sensor_feature_npz=sensor_feature_npz or {},
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

    pnl = float(result.get("balance", 0.0))
    total_fees = float(result.get("fee", 0.0))
    fill_events = result.get("fill_events", [])
    fills, trade_pnls, as_ticks = _fill_metrics(
        fill_events, hypothesis.hyp_id, tick_size
    )

    # Fees are spread evenly across round trips; open residual inventory keeps
    # its share inside net_pnl (hbt's balance) but not in the per-trade stats.
    num_round_trips = len(trade_pnls)
    fee_per_trade = total_fees / num_round_trips if num_round_trips else 0.0
    net_trade_pnls = [p - fee_per_trade for p in trade_pnls]

    if net_trade_pnls:
        win_rate = float(np.mean([p > 0 for p in net_trade_pnls]))
        expectancy = float(np.mean(net_trade_pnls))
        tail_loss = float(np.percentile(net_trade_pnls, 5))
    else:
        win_rate = 0.0
        expectancy = 0.0
        tail_loss = 0.0

    return BacktestResult(
        hypothesis_id=hypothesis.hyp_id,
        net_pnl=pnl,
        num_trades=num_round_trips,
        win_rate=win_rate,
        expectancy=expectancy,
        adverse_selection_ticks=float(np.mean(as_ticks)) if as_ticks else 0.0,
        tail_loss=tail_loss,
        fills=fills,
    )


def run_all_hypotheses_replay(
    hypotheses: List[BaseHypothesis],
    npz_path: str,
    latency_ms: float = 1.0,
    signal_threshold: float = 0.15,
    events: Optional[np.ndarray] = None,
    cross_asset_npz: Optional[Dict[str, str]] = None,
    sensor_feature_npz: Optional[Dict[str, str]] = None,
) -> Dict[int, BacktestResult]:
    return {
        h.hyp_id: run_hypothesis_replay(
            h,
            npz_path,
            latency_ms=latency_ms,
            signal_threshold=signal_threshold,
            events=events,
            cross_asset_npz=cross_asset_npz,
            sensor_feature_npz=sensor_feature_npz,
        )
        for h in hypotheses
    }


def run_latency_matrix_replay(
    hypotheses: List[BaseHypothesis],
    npz_path: str,
    latency_bands: List[float],
    signal_threshold: float = 0.15,
    sensor_feature_npz: Optional[Dict[str, str]] = None,
) -> Dict[float, Dict[int, BacktestResult]]:
    return {
        lat: run_all_hypotheses_replay(
            hypotheses, npz_path, latency_ms=lat, signal_threshold=signal_threshold,
            sensor_feature_npz=sensor_feature_npz,
        )
        for lat in latency_bands
    }


# deprecate_signal_backtester_fill_path() has been removed: the fill
# simulation it warned about was deleted in the same commit that removed
# SignalBacktester's run_hypothesis / run_all_hypotheses / run_latency_matrix
# methods.  The canonical entry points are run_hypothesis_replay,
# run_all_hypotheses_replay, and run_latency_matrix_replay in this module.


def run_structural_models_replay(
    npz_path: str,
    *,
    latency_ms: float = 1.0,
    events: Optional[np.ndarray] = None,
    cross_asset_npz: Optional[Dict[str, str]] = None,
) -> None:
    """NOT-WIRED: structural models cannot be evaluated through the replay matrix.

    Interface mismatch:
      - BaseHypothesis.evaluate(state: MarketState) -> float
        (pure stateless signal on a fully built MarketState)
      - BaseStructuralModel.evaluate(**kwargs) -> ModelOutput[T]
        (accepts raw book/BBO/tick kwargs, returns a typed payload object, and
        in several models — CrossAssetLeadLagModel, VPINToxicityModel,
        TransferEntropyModel, HawkesToxicFlowModel — requires accumulated
        internal state built up by prior update calls; a single evaluate() on
        a fresh instance produces meaningless output)

    A meaningful structural-model replay would need:
      1. A per-model adapter that translates MBOEvent/MarketState fields into
         the correct **kwargs (e.g. bid_p, bid_q, ask_p, ask_q for Model 1;
         own_ofi + leader_ofi sequences for Model 2; trade volume buckets for
         Model 3; etc.).
      2. Stateful warm-up: many models need O(50-100) prior events before their
         first output is valid (z-score windows, PCA history, VPIN buckets).
      3. A different result schema: ModelOutput[T].payload is a typed dataclass
         (BookPressureOutput, VPINToxicityOutput, etc.) with no single scalar
         signal to compare against BacktestResult.

    Wire structural models through their own dedicated evaluation harness
    (e.g. apps/workbench/src/registry/pdf_orchestrator.py) rather than the
    hypothesis replay matrix.
    """
    raise NotImplementedError(
        "run_structural_models_replay is NOT-WIRED: structural models use "
        "evaluate(**kwargs) -> ModelOutput[T] and require stateful warm-up; "
        "they are incompatible with the MarketState-based hypothesis replay "
        "matrix.  Use pdf_orchestrator.py for structural model evaluation."
    )
