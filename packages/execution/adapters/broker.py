"""External broker adapter stub (not wired to gateway hot path)."""
from __future__ import annotations

from typing import List, Optional

from execution import safety
from execution.interfaces import AccountState, OrderEvent, OrderEventType, OrderIntent


class BrokerAdapter:
    source_adapter = "BrokerAdapter"

    def __init__(self, run_id: str = "external") -> None:
        self._run_id = run_id
        self._events: List[OrderEvent] = []
        self._pending: List[OrderEvent] = []

    def submit_order(self, order_intent: OrderIntent) -> OrderEvent:
        safety.record_broker_call()
        safety.record_rithmic_order_call()
        ev = OrderEvent(
            order_id="BROKER-1",
            intent_id=order_intent.intent_id,
            run_id=self._run_id,
            timestamp_ns=order_intent.timestamp_ns,
            receive_timestamp_ns=order_intent.timestamp_ns,
            event_type=OrderEventType.ORDER_REJECTED,
            symbol=order_intent.symbol,
            side=order_intent.side,
            price=order_intent.price,
            quantity=order_intent.quantity,
            rejection_reason="broker_adapter_stub_not_wired",
            source_adapter=self.source_adapter,
        )
        self._events.append(ev)
        self._pending.append(ev)
        return ev

    def cancel_order(self, order_id: str) -> OrderEvent:
        safety.record_broker_call()
        safety.record_rithmic_order_call()
        ev = OrderEvent(
            order_id=order_id,
            intent_id="",
            run_id=self._run_id,
            timestamp_ns=0,
            receive_timestamp_ns=0,
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
        del order_id
        return self.submit_order(new_order_intent)

    def get_order_status(self, order_id: str) -> Optional[OrderEvent]:
        for ev in reversed(self._events):
            if ev.order_id == order_id:
                return ev
        return None

    def get_position(self, symbol: str) -> float:
        del symbol
        return 0.0

    def get_account_state(self) -> AccountState:
        return AccountState()

    def drain_order_events(self) -> List[OrderEvent]:
        out = list(self._pending)
        self._pending.clear()
        return out

    def after_elapse(self, replay_time_ns: int) -> None:
        del replay_time_ns
