"""Order intent, lifecycle events, and adapter protocols."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


class OrderEventType(str, Enum):
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    ORDER_ACCEPTED = "ORDER_ACCEPTED"
    ORDER_REJECTED = "ORDER_REJECTED"
    ORDER_PARTIALLY_FILLED = "ORDER_PARTIALLY_FILLED"
    ORDER_FILLED = "ORDER_FILLED"
    ORDER_CANCEL_REQUESTED = "ORDER_CANCEL_REQUESTED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    ORDER_REPLACE_REQUESTED = "ORDER_REPLACE_REQUESTED"
    ORDER_REPLACED = "ORDER_REPLACED"
    ORDER_EXPIRED = "ORDER_EXPIRED"
    ORDER_TIMED_OUT = "ORDER_TIMED_OUT"


def new_intent_id() -> str:
    return str(uuid.uuid4())


@dataclass
class OrderIntent:
    intent_id: str
    run_id: str
    timestamp_ns: int
    strategy_id: str
    model_id: str
    symbol: str
    side: str  # BUY | SELL
    order_type: str  # LIMIT | MARKET
    price: float
    quantity: float
    time_in_force: str = "GTC"
    reduce_only: bool = False
    latency_budget_ms: float = 1.0
    max_slippage_ticks: float = 0.0
    feature_snapshot_id: str = ""
    event_context: str = "NORMAL"
    regime_state: str = "NORMAL"
    risk_metadata: Dict[str, Any] = field(default_factory=dict)
    reason_code: str = ""


@dataclass
class CancelIntent:
    intent_id: str
    run_id: str
    timestamp_ns: int
    order_id: str
    strategy_id: str
    model_id: str
    symbol: str
    reason_code: str = ""


@dataclass
class ReplaceIntent:
    intent_id: str
    run_id: str
    timestamp_ns: int
    order_id: str
    new_order_intent: OrderIntent
    reason_code: str = ""


@dataclass
class OrderEvent:
    order_id: str
    intent_id: str
    run_id: str
    timestamp_ns: int
    receive_timestamp_ns: int
    event_type: OrderEventType
    symbol: str
    side: str
    price: float
    quantity: float
    filled_quantity: float = 0.0
    remaining_quantity: float = 0.0
    avg_fill_price: float = 0.0
    fees: float = 0.0
    queue_position_estimate: Optional[float] = None
    latency_ms: Optional[float] = None
    rejection_reason: str = ""
    source_adapter: str = ""


@dataclass
class AccountState:
    balance: float = 0.0
    fee: float = 0.0
    num_trades: int = 0
    trading_volume: float = 0.0
    position: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0


@runtime_checkable
class ExecutionAdapter(Protocol):
    source_adapter: str

    def submit_order(self, order_intent: OrderIntent) -> OrderEvent: ...

    def cancel_order(self, order_id: str) -> OrderEvent: ...

    def replace_order(self, order_id: str, new_order_intent: OrderIntent) -> OrderEvent: ...

    def get_order_status(self, order_id: str) -> Optional[OrderEvent]: ...

    def get_position(self, symbol: str) -> float: ...

    def get_account_state(self) -> AccountState: ...

    def drain_order_events(self) -> List[OrderEvent]: ...

    def after_elapse(self, replay_time_ns: int) -> None: ...


@runtime_checkable
class MarketDataAdapter(Protocol):
    def next_event(self) -> Any: ...

    def current_time_ns(self) -> int: ...

    def current_market_state(self, symbol: str) -> Any: ...

    def is_finished(self) -> bool: ...
