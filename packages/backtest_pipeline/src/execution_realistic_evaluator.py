"""Execution-realistic backtest evaluator — HftBacktest-simulated fills replace synthetic metrics.

Per BLUEPRINT §3: fills account for queue position, latency, transaction cost, adverse selection.
Traces to: BLUEPRINT.md §3 (Action-value objective), chicago_cme_microstructure_a_plus_developer_handoff.pdf §6.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from itertools import count
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from execution.interfaces import OrderEventType, OrderIntent, new_intent_id
from replay.replay_session import (
    ReplaySession,
    ReplaySessionConfig,
    ReplayStepContext,
    ReplayStrategy,
)


@dataclass
class ExecutionRealisticResult:
    net_pnl: float = 0.0
    gross_pnl: float = 0.0
    total_fees: float = 0.0
    fill_rate: float = 0.0
    adverse_selection_cost: float = 0.0
    tail_loss: float = 0.0
    sharpe_ratio: float = 0.0
    num_trades: int = 0
    num_intents: int = 0
    win_rate: float = 0.0
    avg_trade_pnl: float = 0.0
    max_drawdown_pnl: float = 0.0
    latency_band_ms: float = 1.0
    queue_model: str = ""
    execution_mode: str = "REPLAY"
    error: str = ""


@dataclass
class StrategyMetrics:
    net_expectancy: float = 0.0
    tail_loss: float = 0.0
    loss: float = 0.0
    net_pnl: float = 0.0
    num_trades: int = 0
    fill_rate: float = 0.0
    adverse_selection: float = 0.0


class SignalToIntentStrategy(ReplayStrategy):
    """Converts model predictions to OrderIntents for HftBacktest replay.

    Signal in [-1, 1] -> BUY (signal > threshold) or SELL (signal < -threshold).
    Magnitude scales quantity. No signal -> no order.
    """

    def __init__(
        self,
        signal_threshold: float = 0.1,
        base_quantity: float = 1.0,
        tick_size: float = 0.25,
        max_position: float = 10.0,
        latency_ms: float = 1.0,
        model_id: str = "eval_model",
        strategy_id: str = "execution_realistic_eval",
    ):
        self.signal_threshold = signal_threshold
        self.base_quantity = base_quantity
        self.tick_size = tick_size
        self.max_position = max_position
        self.latency_ms = latency_ms
        self.model_id = model_id
        self.strategy_id = strategy_id
        self._position = 0.0
        self._order_count = 0
        self._signal_getter: Optional[Callable[[], float]] = None

    def set_signal_getter(self, getter: Callable[[], float]) -> None:
        self._signal_getter = getter

    def on_step(self, ctx: ReplayStepContext) -> list:
        if self._signal_getter is None:
            return []
        signal = self._signal_getter()
        if abs(signal) < self.signal_threshold:
            return []
        side = "BUY" if signal > 0 else "SELL"
        qty = max(1, int(self.base_quantity * abs(signal)))
        price = ctx.best_bid if side == "BUY" else ctx.best_ask
        intent = OrderIntent(
            intent_id=new_intent_id(),
            run_id=ctx.run_id,
            timestamp_ns=ctx.clock.now_ns,
            strategy_id=self.strategy_id,
            model_id=self.model_id,
            symbol=ctx.symbol,
            side=side,
            order_type="LIMIT",
            price=price,
            quantity=float(qty),
            time_in_force="GTC",
            latency_budget_ms=self.latency_ms,
        )
        return [intent]


class ExecutionRealisticEvaluator:
    """Runs a strategy through HftBacktest replay with real queue position,
    latency bands, and fee model to produce execution-realistic PnL."""

    def __init__(
        self,
        latency_ms: float = 1.0,
        queue_model: str = "LogProbQueueModel2",
        tick_size: float = 0.25,
        lot_size: float = 1.0,
        product: str = "MES",
    ):
        self.latency_ms = latency_ms
        self.queue_model = queue_model
        self.tick_size = tick_size
        self.lot_size = lot_size
        self.product = product

    def evaluate(
        self,
        npz_path: str,
        model_weights: List[float],
        feature_vectors: List[np.ndarray],
        signal_threshold: float = 0.1,
        max_steps: Optional[int] = None,
    ) -> ExecutionRealisticResult:
        strategy = SignalToIntentStrategy(
            signal_threshold=signal_threshold,
            base_quantity=1.0,
            tick_size=self.tick_size,
            max_position=10.0,
            latency_ms=self.latency_ms,
        )
        signal_iter = self._signal_iterator(model_weights, feature_vectors)
        strategy.set_signal_getter(lambda: next(signal_iter))

        cfg = ReplaySessionConfig(
            npz_path=npz_path,
            latency_ms=self.latency_ms,
            queue_model=self.queue_model,
            tick_size=self.tick_size,
            lot_size=self.lot_size,
            product=self.product,
            max_steps=max_steps,
        )
        session = ReplaySession(cfg, strategy)
        result = session.run()

        if "error" in result and result["error"]:
            return ExecutionRealisticResult(
                error=str(result.get("error", "")),
                net_pnl=-99999.0,
                latency_band_ms=self.latency_ms,
                queue_model=self.queue_model,
            )

        lifecycle = self._load_lifecycle(
            str(cfg.audit_dir / f"{session.run_id}_order_lifecycle.jsonl")
        )
        return self._compute_metrics(result, lifecycle)

    def _signal_iterator(self, weights: List[float], features: List[np.ndarray]):
        idx = 0
        while True:
            if idx >= len(features):
                yield 0.0
            else:
                fv = features[idx].ravel() if hasattr(features[idx], "ravel") else features[idx]
                w = np.array(weights[: len(fv)], dtype=np.float64)
                yield float(np.dot(w, fv.astype(np.float64)))
            idx += 1

    def _load_lifecycle(self, path: str) -> List[Dict[str, Any]]:
        import json
        from pathlib import Path
        p = Path(path)
        if not p.exists():
            return []
        events: List[Dict[str, Any]] = []
        for line in p.read_text(encoding="utf-8").strip().split("\n"):
            if line.strip():
                events.append(json.loads(line))
        return events

    def _compute_metrics(
        self,
        run_result: Dict[str, Any],
        lifecycle: List[Dict[str, Any]],
    ) -> ExecutionRealisticResult:
        balance = float(run_result.get("balance", 0.0))
        fee = float(run_result.get("fee", 0.0))
        num_trades = int(run_result.get("num_trades", 0))
        num_intents = int(run_result.get("order_intent_count", 0))

        gross_pnl = balance - fee if abs(fee) > 1e-9 else balance
        net_pnl = balance
        fill_rate = num_trades / max(num_intents, 1)

        fill_events = [
            ev for ev in lifecycle
            if ev.get("event_type") in ("ORDER_FILLED", "ORDER_PARTIALLY_FILLED")
        ]
        trade_pnls: List[float] = []
        mid_prices: List[float] = []
        as_costs: List[float] = []
        cum_pnl = 0.0
        peak = 0.0
        max_dd = 0.0

        trade_prices: List[float] = []
        position = 0.0
        cost_basis = 0.0
        cum_pnl_from_trades = 0.0
        peak_pnl = 0.0

        for ev in fill_events:
            qty = float(ev.get("filled_quantity", 0))
            price = float(ev.get("avg_fill_price", 0))
            side = ev.get("side", "BUY")
            if qty <= 0:
                continue
            mid = price
            mid_prices.append(mid)
            if side == "BUY":
                old_pos = position
                position += qty
                cost_basis = (cost_basis * old_pos + price * qty) / max(position, 1e-12)
                trade_prices.append(price)
                trade_pnls.append(0.0)
                as_costs.append(0.0)
            else:
                if position > 0:
                    tp = (price - cost_basis) * min(qty, position)
                    as_cost = (price * qty) - (cost_basis * qty) / 2.0
                    trade_pnls.append(tp)
                    as_costs.append(as_cost)
                    trade_prices.append(price)
                    cum_pnl_from_trades += tp
                position = max(0.0, position - qty)

            peak_pnl = max(peak_pnl, cum_pnl_from_trades)
            max_dd = max(max_dd, peak_pnl - cum_pnl_from_trades)

        realized_trades = [tp for tp in trade_pnls if abs(tp) > 1e-12]
        win_rate = sum(1 for tp in realized_trades if tp > 0) / max(len(realized_trades), 1)
        avg_trade_pnl = np.mean(realized_trades) if realized_trades else 0.0
        sharpe = (
            np.mean(realized_trades) / max(np.std(realized_trades), 1e-9) * np.sqrt(252)
            if len(realized_trades) >= 2
            else 0.0
        )
        tail = float(np.percentile(realized_trades, 5)) if len(realized_trades) >= 20 else 0.0
        as_cost_total = sum(as_costs) if as_costs else 0.0

        return ExecutionRealisticResult(
            net_pnl=net_pnl,
            gross_pnl=gross_pnl,
            total_fees=fee,
            fill_rate=fill_rate,
            adverse_selection_cost=as_cost_total,
            tail_loss=tail,
            sharpe_ratio=float(sharpe),
            num_trades=num_trades,
            num_intents=num_intents,
            win_rate=win_rate,
            avg_trade_pnl=float(avg_trade_pnl),
            max_drawdown_pnl=max_dd,
            latency_band_ms=self.latency_ms,
            queue_model=self.queue_model,
        )

    @staticmethod
    def to_strategy_metrics(result: ExecutionRealisticResult) -> StrategyMetrics:
        return StrategyMetrics(
            net_expectancy=result.net_pnl / max(result.num_trades, 1),
            tail_loss=result.tail_loss,
            loss=0.0,
            net_pnl=result.net_pnl,
            num_trades=result.num_trades,
            fill_rate=result.fill_rate,
            adverse_selection=result.adverse_selection_cost,
        )


def evaluate_with_execution_realism(
    npz_path: str,
    model_weights: List[float],
    feature_vectors: List[np.ndarray],
    latency_ms: float = 1.0,
    queue_model: str = "LogProbQueueModel2",
) -> StrategyMetrics:
    evaluator = ExecutionRealisticEvaluator(
        latency_ms=latency_ms, queue_model=queue_model,
    )
    result = evaluator.evaluate(npz_path, model_weights, feature_vectors)
    return ExecutionRealisticEvaluator.to_strategy_metrics(result)
