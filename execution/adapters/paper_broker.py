"""Paper broker adapter (R|Trader trial lane protocol; no live routing)."""
from __future__ import annotations

from typing import Dict, List, Optional

from execution import safety
from execution.interfaces import AccountState, OrderEvent, OrderEventType, OrderIntent


class PaperBrokerAdapter:
    source_adapter = "PaperBrokerAdapter"

    def __init__(self, run_id: str = "paper") -> None:
        self._run_id = run_id
        self._events: List[OrderEvent] = []
        self._pending: List[OrderEvent] = []
        self._position = 0.0
        self._next_id = 1

    def submit_order(self, order_intent: OrderIntent) -> OrderEvent:
        safety.record_rithmic_order_call()
        oid = f"PAPER-{self._next_id}"
        self._next_id += 1
        ev = OrderEvent(
            order_id=oid,
            intent_id=order_intent.intent_id,
            run_id=self._run_id,
            timestamp_ns=order_intent.timestamp_ns,
            receive_timestamp_ns=order_intent.timestamp_ns,
            event_type=OrderEventType.ORDER_ACCEPTED,
            symbol=order_intent.symbol,
            side=order_intent.side,
            price=order_intent.price,
            quantity=order_intent.quantity,
            remaining_quantity=order_intent.quantity,
            source_adapter=self.source_adapter,
        )
        self._events.append(ev)
        self._pending.append(ev)
        return ev

    def cancel_order(self, order_id: str) -> OrderEvent:
        safety.record_rithmic_order_call()
        ts = 0
        ev = OrderEvent(
            order_id=order_id,
            intent_id="",
            run_id=self._run_id,
            timestamp_ns=ts,
            receive_timestamp_ns=ts,
            event_type=OrderEventType.ORDER_CANCELLED,
            symbol="",
            side="",
            price=0.0,
            quantity=0.0,
            source_adapter=self.source_adapter,
        )
        self._events.append(ev)
        self._pending.append(ev)
        return ev

    def replace_order(self, order_id: str, new_order_intent: OrderIntent) -> OrderEvent:
        self.cancel_order(order_id)
        return self.submit_order(new_order_intent)

    def get_order_status(self, order_id: str) -> Optional[OrderEvent]:
        for ev in reversed(self._events):
            if ev.order_id == order_id:
                return ev
        return None

    def get_position(self, symbol: str) -> float:
        del symbol
        return self._position

    def get_account_state(self) -> AccountState:
        return AccountState(position=self._position)

    def drain_order_events(self) -> List[OrderEvent]:
        out = list(self._pending)
        self._pending.clear()
        return out

    def after_elapse(self, replay_time_ns: int) -> None:
        del replay_time_ns
