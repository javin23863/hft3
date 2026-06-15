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
    """Build FillRecords, FIFO closed-trade PnLs, and per-fill adverse selection.

    Closed-trade PnL is in the same cash units as hftbacktest balance and is
    net of the matched opening/closing fill fees. Open inventory is deliberately
    excluded: hftbacktest balance is cash accounting, so an unclosed buy can
    make balance look like a large loss even though no round trip exists.
    Adverse selection per fill is measured in ticks against the mid observed at
    drain time: positive means the market moved against the fill immediately
    after execution.
    """
    fills: List[FillRecord] = []
    trade_pnls: List[float] = []
    as_ticks: List[float] = []

    open_side = 0  # +1 long lots, -1 short lots, 0 flat
    open_lots: deque = deque()  # [price, qty, remaining_fee]

    for f in fill_events:
        side = str(f["side"]).upper()
        px = float(f["exec_price"])
        qty = float(f["qty"])
        fee = float(f.get("fees", 0.0) or 0.0)
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
            open_lots.append([px, qty, fee])
            open_side = sign
            continue
        remaining = qty
        remaining_fee = fee
        while remaining > 1e-12 and open_lots:
            lot = open_lots[0]
            lot_qty_before = lot[1]
            remaining_before = remaining
            matched = min(remaining, lot_qty_before)
            open_fee = lot[2] * (matched / lot_qty_before) if lot_qty_before else 0.0
            close_fee = remaining_fee * (matched / remaining_before) if remaining_before else 0.0
            gross = (px - lot[0]) * matched * open_side
            trade_pnls.append(gross - open_fee - close_fee)
            lot[1] -= matched
            lot[2] -= open_fee
            remaining -= matched
            remaining_fee -= close_fee
            if lot[1] <= 1e-12:
                open_lots.popleft()
        if remaining > 1e-12:
            open_lots.append([px, remaining, remaining_fee])
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

    hftbacktest_cash_balance = float(result.get("balance", 0.0))
    ending_position_qty = float(result.get("position", 0.0))
    fill_events = result.get("fill_events", [])
    fills, trade_pnls, as_ticks = _fill_metrics(
        fill_events, hypothesis.hyp_id, tick_size
    )

    # Report realized closed-round-trip PnL. Raw hftbacktest balance is cash
    # accounting and can include open-inventory cost/proceeds, so it is carried
    # separately as hftbacktest_cash_balance.
    num_round_trips = len(trade_pnls)
    net_trade_pnls = trade_pnls

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
        net_pnl=float(sum(net_trade_pnls)),
        num_trades=num_round_trips,
        win_rate=win_rate,
        expectancy=expectancy,
        adverse_selection_ticks=float(np.mean(as_ticks)) if as_ticks else 0.0,
        tail_loss=tail_loss,
        hftbacktest_cash_balance=hftbacktest_cash_balance,
        ending_position_qty=ending_position_qty,
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
