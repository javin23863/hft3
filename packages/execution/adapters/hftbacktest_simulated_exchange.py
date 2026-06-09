"""HftBacktest-backed simulated exchange adapter."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from hftbacktest.order import FILLED, GTC, LIMIT, PARTIALLY_FILLED

from execution.interfaces import (
    AccountState,
    OrderEvent,
    OrderEventType,
    OrderIntent,
)


@dataclass
class _TrackedOrder:
    order_id: str
    intent_id: str
    hbt_oid: int
    side: str
    price: float
    quantity: float
    filled_quantity: float = 0.0
    accepted: bool = False


class HftBacktestSimulatedExchangeAdapter:
    """Wraps HftBacktest order API; synthesizes lifecycle events from position/state deltas."""

    source_adapter = "HftBacktestSimulatedExchangeAdapter"

    def __init__(
        self,
        hbt,
        *,
        run_id: str,
        latency_ms: float = 1.0,
        queue_model: str = "LogProbQueueModel2",
        asset_no: int = 0,
    ) -> None:
        self._hbt = hbt
        self._run_id = run_id
        self._latency_ms = latency_ms
        self._queue_model = queue_model
        self._asset_no = asset_no
        self._events: List[OrderEvent] = []
        self._pending_drain: List[OrderEvent] = []
        self._orders: Dict[str, _TrackedOrder] = {}
        self._hbt_oid_to_order_id: Dict[int, str] = {}
        self._next_hbt_oid = 1000
        self._last_position = 0.0
        self._last_fee = 0.0

    def _emit(self, ev: OrderEvent) -> OrderEvent:
        self._events.append(ev)
        self._pending_drain.append(ev)
        return ev

    def submit_order(self, order_intent: OrderIntent) -> OrderEvent:
        hbt_oid = self._next_hbt_oid
        self._next_hbt_oid += 1
        order_id = f"SIM-{hbt_oid}"
        side = order_intent.side.upper()
        qty = float(order_intent.quantity)
        price = float(order_intent.price)

        if side == "BUY":
            self._hbt.submit_buy_order(
                self._asset_no, hbt_oid, price, qty, GTC, LIMIT, False
            )
        else:
            self._hbt.submit_sell_order(
                self._asset_no, hbt_oid, price, qty, GTC, LIMIT, False
            )

        tracked = _TrackedOrder(
            order_id=order_id,
            intent_id=order_intent.intent_id,
            hbt_oid=hbt_oid,
            side=side,
            price=price,
            quantity=qty,
        )
        self._orders[order_id] = tracked
        self._hbt_oid_to_order_id[hbt_oid] = order_id

        self._emit(
            OrderEvent(
                order_id=order_id,
                intent_id=order_intent.intent_id,
                run_id=self._run_id,
                timestamp_ns=order_intent.timestamp_ns,
                receive_timestamp_ns=order_intent.timestamp_ns,
                event_type=OrderEventType.ORDER_SUBMITTED,
                symbol=order_intent.symbol,
                side=side,
                price=price,
                quantity=qty,
                remaining_quantity=qty,
                latency_ms=self._latency_ms,
                source_adapter=self.source_adapter,
            )
        )
        return self._emit(
            OrderEvent(
                order_id=order_id,
                intent_id=order_intent.intent_id,
                run_id=self._run_id,
                timestamp_ns=order_intent.timestamp_ns,
                receive_timestamp_ns=order_intent.timestamp_ns,
                event_type=OrderEventType.ORDER_ACCEPTED,
                symbol=order_intent.symbol,
                side=side,
                price=price,
                quantity=qty,
                remaining_quantity=qty,
                latency_ms=self._latency_ms,
                source_adapter=self.source_adapter,
            )
        )

    def cancel_order(self, order_id: str) -> OrderEvent:
        tracked = self._orders.get(order_id)
        if tracked is None:
            return self._emit(
                OrderEvent(
                    order_id=order_id,
                    intent_id="",
                    run_id=self._run_id,
                    timestamp_ns=int(self._hbt.current_timestamp),
                    receive_timestamp_ns=int(self._hbt.current_timestamp),
                    event_type=OrderEventType.ORDER_REJECTED,
                    symbol="",
                    side="",
                    price=0.0,
                    quantity=0.0,
                    rejection_reason="unknown_order",
                    source_adapter=self.source_adapter,
                )
            )
        self._hbt.cancel(self._asset_no, tracked.hbt_oid, False)
        ts = int(self._hbt.current_timestamp)
        self._emit(
            OrderEvent(
                order_id=order_id,
                intent_id=tracked.intent_id,
                run_id=self._run_id,
                timestamp_ns=ts,
                receive_timestamp_ns=ts,
                event_type=OrderEventType.ORDER_CANCEL_REQUESTED,
                symbol="",
                side=tracked.side,
                price=tracked.price,
                quantity=tracked.quantity,
                source_adapter=self.source_adapter,
            )
        )
        return self._emit(
            OrderEvent(
                order_id=order_id,
                intent_id=tracked.intent_id,
                run_id=self._run_id,
                timestamp_ns=ts,
                receive_timestamp_ns=ts,
                event_type=OrderEventType.ORDER_CANCELLED,
                symbol="",
                side=tracked.side,
                price=tracked.price,
                quantity=tracked.quantity,
                source_adapter=self.source_adapter,
            )
        )

    def replace_order(self, order_id: str, new_order_intent: OrderIntent) -> OrderEvent:
        self.cancel_order(order_id)
        ev = self.submit_order(new_order_intent)
        ev.event_type = OrderEventType.ORDER_REPLACED
        return ev

    def get_order_status(self, order_id: str) -> Optional[OrderEvent]:
        for ev in reversed(self._events):
            if ev.order_id == order_id:
                return ev
        return None

    def get_position(self, symbol: str) -> float:
        del symbol
        return float(self._hbt.position(self._asset_no))

    def get_account_state(self) -> AccountState:
        state = self._hbt.state_values(self._asset_no)
        return AccountState(
            balance=float(state.balance),
            fee=float(state.fee),
            num_trades=int(state.num_trades),
            trading_volume=float(state.trading_volume),
            position=float(state.position),
            realized_pnl=float(state.balance),
        )

    def drain_order_events(self) -> List[OrderEvent]:
        out = list(self._pending_drain)
        self._pending_drain.clear()
        return out

    def after_elapse(self, replay_time_ns: int) -> None:
        state = self._hbt.state_values(self._asset_no)
        orders = self._hbt.orders(self._asset_no)
        fee_now = float(state.fee)
        fee_delta = fee_now - self._last_fee

        # Diff per-order execution state against hbt's order book rather than
        # inferring fills from the trade counter: only orders hbt reports as
        # (partially) filled get fill events, with their actual exec price.
        new_fills: List[tuple] = []
        for tracked in self._orders.values():
            if tracked.filled_quantity >= tracked.quantity:
                continue
            order = orders.get(tracked.hbt_oid)
            if order is None:
                continue
            status = int(order.status)
            if status == FILLED:
                cum_filled = float(order.qty)
            elif status == PARTIALLY_FILLED:
                cum_filled = float(order.qty) - float(order.leaves_qty)
            else:
                continue
            fill_qty = cum_filled - tracked.filled_quantity
            if fill_qty <= 0.0:
                continue
            new_fills.append(
                (tracked, fill_qty, cum_filled, float(order.exec_price), status == FILLED)
            )

        total_fill_qty = sum(f[1] for f in new_fills)
        for tracked, fill_qty, cum_filled, exec_price, terminal in new_fills:
            tracked.filled_quantity = cum_filled
            self._emit(
                OrderEvent(
                    order_id=tracked.order_id,
                    intent_id=tracked.intent_id,
                    run_id=self._run_id,
                    timestamp_ns=replay_time_ns,
                    receive_timestamp_ns=replay_time_ns,
                    event_type=(
                        OrderEventType.ORDER_FILLED
                        if terminal
                        else OrderEventType.ORDER_PARTIALLY_FILLED
                    ),
                    symbol="",
                    side=tracked.side,
                    price=tracked.price,
                    quantity=tracked.quantity,
                    filled_quantity=cum_filled,
                    remaining_quantity=tracked.quantity - cum_filled,
                    avg_fill_price=exec_price,
                    fees=fee_delta * (fill_qty / total_fill_qty),
                    latency_ms=self._latency_ms,
                    source_adapter=self.source_adapter,
                )
            )

        self._last_fee = fee_now
        self._last_position = float(self._hbt.position(self._asset_no))
        # Clear only after terminal states were observed above; clearing first
        # makes filled orders invisible to the diff.
        self._hbt.clear_inactive_orders(self._asset_no)

    @property
    def all_events(self) -> List[OrderEvent]:
        return list(self._events)

    @property
    def order_intent_count(self) -> int:
        return sum(
            1
            for ev in self._events
            if ev.event_type == OrderEventType.ORDER_SUBMITTED
        )
