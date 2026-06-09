"""Crypto execution validator — runs HftBacktest replay with crypto-specific builders and metrics."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import math
from execution.adapter_factory import create_adapter
from execution.interfaces import (
    CancelIntent,
    ExecutionAdapter,
    OrderEvent,
    OrderEventType,
    OrderIntent,
    ReplaceIntent,
    new_intent_id,
)
from execution import safety
from hftbacktest import HashMapMarketDepthBacktest
from replay.replay_clock import ReplayClock, deterministic_run_id
from replay.replay_session import ReplayStepContext, ReplayStrategy
import numpy as np
from hft3.validation.research_stamp import build_certification_stamp, format_stamp_footer
from crypto_lane.src.validation.metrics import slippage_bps, jump_metric


@dataclass
class CryptoExecutionResult:
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
    slippage_bps: float = 0.0
    mean_jump_bps: float = 0.0
    mean_qqe: float = 0.0
    execution_classification: str = "L2_PROXY_ONLY"
    error: str = ""
    trade_pnls: List[float] = field(default_factory=list)


class CryptoReplayStrategy:
    """Converts crypto hypothesis signals to OrderIntents for HftBacktest replay.

    Signal in [-1, 1] -> BUY (signal > threshold) or SELL (signal < -threshold).
    Defaults to a simple threshold-based strategy; override `compute_signal` for
    hypothesis-specific logic.
    """

    def __init__(
        self,
        signal_threshold: float = 0.1,
        base_quantity: float = 1.0,
        tick_size: float = 0.1,
        max_position: float = 10.0,
        latency_ms: float = 50.0,
        model_id: str = "crypto_model",
        strategy_id: str = "crypto_replay",
        is_perp: bool = True,
        marketable_limit: bool = False,
    ):
        self.signal_threshold = signal_threshold
        self.base_quantity = base_quantity
        self.tick_size = tick_size
        self.max_position = max_position
        self.latency_ms = latency_ms
        self.model_id = model_id
        self.strategy_id = strategy_id
        self.is_perp = is_perp
        self.marketable_limit = marketable_limit
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
        if self.is_perp and ctx.position != 0:
            current_pos = ctx.position
            new_pos = current_pos + (qty if side == "BUY" else -qty)
            if abs(new_pos) > self.max_position:
                return []
        if self.marketable_limit:
            price = ctx.best_ask if side == "BUY" else ctx.best_bid
        else:
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
            reduce_only=self.is_perp,
            latency_budget_ms=self.latency_ms,
        )
        return [intent]


def _order_event_row(
    ev: OrderEvent,
    *,
    replay_time_ns: int,
    best_bid: float,
    best_ask: float,
) -> Dict[str, Any]:
    return {
        "run_id": ev.run_id,
        "timestamp_ns": ev.timestamp_ns,
        "replay_time_ns": replay_time_ns,
        "event_type": ev.event_type.value,
        "intent_id": ev.intent_id,
        "order_id": ev.order_id,
        "symbol": ev.symbol,
        "side": ev.side,
        "price": ev.price,
        "quantity": ev.quantity,
        "filled_quantity": ev.filled_quantity,
        "remaining_quantity": ev.remaining_quantity,
        "avg_fill_price": ev.avg_fill_price,
        "reference_price": best_ask if ev.side == "BUY" else best_bid,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "fees": ev.fees,
        "latency_ms": ev.latency_ms,
        "queue_position_estimate": ev.queue_position_estimate,
    }


def run_crypto_replay(
    hbt: HashMapMarketDepthBacktest,
    strategy: CryptoReplayStrategy,
    npz_path: str,
    symbol: str,
    tick_size: float,
    latency_ms: float,
    queue_model: str,
    max_steps: Optional[int] = None,
    step_ns: int = 100_000,
) -> Dict[str, Any]:
    run_id = deterministic_run_id(npz_path, latency_ms, queue_model)
    adapter = create_adapter(
        "REPLAY",
        hbt=hbt,
        run_id=run_id,
        latency_ms=latency_ms,
        queue_model=queue_model,
    )
    safety.assert_replay_safe(adapter, declared_mode="REPLAY")

    clock = ReplayClock()
    lifecycle: List[Dict[str, Any]] = []
    intent_count = 0
    steps = 0

    while True:
        result = hbt.elapse(step_ns)
        if result == 1:
            break
        if result != 0:
            return {"error": int(result), "steps": steps, "run_id": run_id}

        ts = int(hbt.current_timestamp)
        clock.advance_to(ts)
        # Adapter observes terminal order states and clears inactive orders
        # itself; clearing here first would hide fills from it.
        adapter.after_elapse(ts)

        depth = hbt.depth(0)
        if (
            not math.isfinite(float(depth.best_bid))
            or not math.isfinite(float(depth.best_ask))
            or depth.best_bid <= 0
            or depth.best_ask <= 0
        ):
            steps += 1
            if max_steps and steps >= max_steps:
                break
            continue

        drained = adapter.drain_order_events()
        ctx = ReplayStepContext(
            run_id=run_id,
            clock=clock,
            market_state=None,
            best_bid=float(depth.best_bid),
            best_ask=float(depth.best_ask),
            position=float(hbt.position(0)),
            order_events=drained,
            execution=adapter,
            symbol=symbol,
        )
        for ev in drained:
            lifecycle.append(
                _order_event_row(
                    ev,
                    replay_time_ns=ts,
                    best_bid=float(depth.best_bid),
                    best_ask=float(depth.best_ask),
                )
            )
        actions = strategy.on_step(ctx)
        for action in actions:
            if isinstance(action, OrderIntent):
                intent_count += 1
                ev = adapter.submit_order(action)
                lifecycle.append(
                    _order_event_row(
                        ev,
                        replay_time_ns=ts,
                        best_bid=float(depth.best_bid),
                        best_ask=float(depth.best_ask),
                    )
                )
            elif isinstance(action, CancelIntent):
                ev = adapter.cancel_order(action.order_id)
            elif isinstance(action, ReplaceIntent):
                ev = adapter.replace_order(action.order_id, action.new_order_intent)

        steps += 1
        if max_steps and steps >= max_steps:
            break

    account = adapter.get_account_state()
    fill_events = [ev for ev in lifecycle if ev["event_type"] in ("ORDER_FILLED", "ORDER_PARTIALLY_FILLED")]
    return {
        "run_id": run_id,
        "steps": steps,
        "balance": account.balance,
        "fee": account.fee,
        "num_trades": account.num_trades,
        "order_intent_count": intent_count,
        "lifecycle": lifecycle,
        "fill_events": fill_events,
    }


def compute_crypto_metrics(
    run_result: Dict[str, Any],
    execution_classification: str,
) -> CryptoExecutionResult:
    error = str(run_result.get("error") or "")
    balance = float(run_result.get("balance", 0.0))
    fee = float(run_result.get("fee", 0.0))
    num_trades = int(run_result.get("num_trades", 0))
    num_intents = int(run_result.get("order_intent_count", 0))
    fill_events = run_result.get("fill_events", [])

    gross_pnl = balance - fee if abs(fee) > 1e-9 else balance
    net_pnl = balance
    fill_rate = num_trades / max(num_intents, 1)

    trade_pnls: List[float] = []
    as_costs: List[float] = []
    position = 0.0
    cost_basis = 0.0
    cum_pnl_from_trades = 0.0
    peak_pnl = 0.0
    max_dd = 0.0
    slippages: List[float] = []
    fill_mids: List[float] = []

    for ev in fill_events:
        qty = float(ev.get("filled_quantity", 0))
        price = float(ev.get("avg_fill_price", 0))
        side = ev.get("side", "BUY")
        if qty <= 0:
            continue

        ref_price = float(ev.get("reference_price", price))
        if ref_price > 0 and price > 0:
            slippages.append(slippage_bps(price, ref_price, side))

        bb = float(ev.get("best_bid", 0))
        ba = float(ev.get("best_ask", 0))
        if bb > 0 and ba > 0:
            fill_mids.append((bb + ba) / 2.0)

        if side == "BUY":
            old_pos = position
            position += qty
            cost_basis = (cost_basis * old_pos + price * qty) / max(position, 1e-12)
        else:
            if position > 0:
                tp = (price - cost_basis) * min(qty, position)
                as_cost = (price * qty) - (cost_basis * qty) / 2.0
                trade_pnls.append(tp)
                as_costs.append(as_cost)
                cum_pnl_from_trades += tp
            position = max(0.0, position - qty)
        peak_pnl = max(peak_pnl, cum_pnl_from_trades)
        max_dd = max(max_dd, peak_pnl - cum_pnl_from_trades)

    realized_trades = [tp for tp in trade_pnls if abs(tp) > 1e-12]
    win_rate = sum(1 for tp in realized_trades if tp > 0) / max(len(realized_trades), 1)
    avg_trade_pnl = float(np.mean(realized_trades)) if realized_trades else 0.0
    sharpe = (
        float(np.mean(realized_trades) / max(np.std(realized_trades), 1e-9) * np.sqrt(252))
        if len(realized_trades) >= 2
        else 0.0
    )
    tail = float(np.percentile(realized_trades, 5)) if len(realized_trades) >= 20 else 0.0
    as_cost_total = sum(as_costs) if as_costs else 0.0
    avg_slippage = float(np.mean(slippages)) if slippages else 0.0
    jmp = jump_metric(fill_events, fill_mids[:len(fill_events) * 5]) if fill_mids else 0.0
    qqe_val = 0.5

    return CryptoExecutionResult(
        net_pnl=net_pnl,
        gross_pnl=gross_pnl,
        total_fees=fee,
        fill_rate=fill_rate,
        adverse_selection_cost=as_cost_total,
        tail_loss=tail,
        sharpe_ratio=sharpe,
        num_trades=num_trades,
        num_intents=num_intents,
        win_rate=win_rate,
        avg_trade_pnl=avg_trade_pnl,
        max_drawdown_pnl=max_dd,
        slippage_bps=avg_slippage,
        mean_jump_bps=jmp,
        mean_qqe=qqe_val,
        execution_classification=execution_classification,
        error=error,
        trade_pnls=realized_trades,
    )
