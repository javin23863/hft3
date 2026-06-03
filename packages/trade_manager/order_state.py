"""Phase 18 inert Trade Manager order-state machine."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from trade_manager.risk_layer import TradeManagerRiskDecision


class TradeManagerOrderState(str, Enum):
    CREATED = "CREATED"
    SENT_TO_RISK = "SENT_TO_RISK"
    RISK_REJECTED = "RISK_REJECTED"
    RISK_APPROVED = "RISK_APPROVED"
    SENT_TO_EXECUTION = "SENT_TO_EXECUTION"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    REPLACE_REQUESTED = "REPLACE_REQUESTED"
    REPLACED = "REPLACED"
    BROKER_REJECTED = "BROKER_REJECTED"
    EXPIRED = "EXPIRED"
    TIMED_OUT = "TIMED_OUT"
    ERROR = "ERROR"
    KILLED = "KILLED"


ORDER_STATE_VALUES = tuple(state.value for state in TradeManagerOrderState)

TERMINAL_ORDER_STATES = frozenset(
    {
        TradeManagerOrderState.RISK_REJECTED,
        TradeManagerOrderState.FILLED,
        TradeManagerOrderState.CANCELLED,
        TradeManagerOrderState.BROKER_REJECTED,
        TradeManagerOrderState.EXPIRED,
        TradeManagerOrderState.TIMED_OUT,
        TradeManagerOrderState.ERROR,
        TradeManagerOrderState.KILLED,
    }
)

ALLOWED_ORDER_STATE_TRANSITIONS: dict[TradeManagerOrderState, tuple[TradeManagerOrderState, ...]] = {
    TradeManagerOrderState.CREATED: (
        TradeManagerOrderState.SENT_TO_RISK,
        TradeManagerOrderState.ERROR,
        TradeManagerOrderState.KILLED,
    ),
    TradeManagerOrderState.SENT_TO_RISK: (
        TradeManagerOrderState.RISK_REJECTED,
        TradeManagerOrderState.RISK_APPROVED,
        TradeManagerOrderState.ERROR,
        TradeManagerOrderState.KILLED,
    ),
    TradeManagerOrderState.RISK_APPROVED: (
        TradeManagerOrderState.SENT_TO_RISK,
        TradeManagerOrderState.SENT_TO_EXECUTION,
        TradeManagerOrderState.ERROR,
        TradeManagerOrderState.KILLED,
    ),
    TradeManagerOrderState.SENT_TO_EXECUTION: (
        TradeManagerOrderState.ACKNOWLEDGED,
        TradeManagerOrderState.BROKER_REJECTED,
        TradeManagerOrderState.TIMED_OUT,
        TradeManagerOrderState.ERROR,
        TradeManagerOrderState.KILLED,
    ),
    TradeManagerOrderState.ACKNOWLEDGED: (
        TradeManagerOrderState.PARTIALLY_FILLED,
        TradeManagerOrderState.FILLED,
        TradeManagerOrderState.CANCEL_REQUESTED,
        TradeManagerOrderState.REPLACE_REQUESTED,
        TradeManagerOrderState.BROKER_REJECTED,
        TradeManagerOrderState.EXPIRED,
        TradeManagerOrderState.TIMED_OUT,
        TradeManagerOrderState.ERROR,
        TradeManagerOrderState.KILLED,
    ),
    TradeManagerOrderState.PARTIALLY_FILLED: (
        TradeManagerOrderState.PARTIALLY_FILLED,
        TradeManagerOrderState.FILLED,
        TradeManagerOrderState.CANCEL_REQUESTED,
        TradeManagerOrderState.REPLACE_REQUESTED,
        TradeManagerOrderState.EXPIRED,
        TradeManagerOrderState.TIMED_OUT,
        TradeManagerOrderState.ERROR,
        TradeManagerOrderState.KILLED,
    ),
    TradeManagerOrderState.CANCEL_REQUESTED: (
        TradeManagerOrderState.CANCELLED,
        TradeManagerOrderState.ERROR,
        TradeManagerOrderState.KILLED,
    ),
    TradeManagerOrderState.REPLACE_REQUESTED: (
        TradeManagerOrderState.REPLACED,
        TradeManagerOrderState.BROKER_REJECTED,
        TradeManagerOrderState.ERROR,
        TradeManagerOrderState.KILLED,
    ),
    TradeManagerOrderState.REPLACED: (
        TradeManagerOrderState.ACKNOWLEDGED,
        TradeManagerOrderState.PARTIALLY_FILLED,
        TradeManagerOrderState.FILLED,
        TradeManagerOrderState.CANCEL_REQUESTED,
        TradeManagerOrderState.REPLACE_REQUESTED,
        TradeManagerOrderState.EXPIRED,
        TradeManagerOrderState.TIMED_OUT,
        TradeManagerOrderState.ERROR,
        TradeManagerOrderState.KILLED,
    ),
}


@dataclass(frozen=True)
class TradeManagerOrderTransition:
    order_intent_id: str
    model_id: str
    previous_state: TradeManagerOrderState | None
    state: TradeManagerOrderState
    timestamp_ns: int
    reason: str
    source: str
    risk_allowed: bool | None = None
    risk_reason: str = ""
    risk_action: str = ""
    monitor_name: str = ""
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_intent_id": self.order_intent_id,
            "model_id": self.model_id,
            "previous_state": self.previous_state.value if self.previous_state is not None else None,
            "state": self.state.value,
            "timestamp_ns": self.timestamp_ns,
            "reason": self.reason,
            "source": self.source,
            "risk_allowed": self.risk_allowed,
            "risk_reason": self.risk_reason,
            "risk_action": self.risk_action,
            "monitor_name": self.monitor_name,
            "details": dict(self.details or {}),
        }


class OrderStateTransitionError(ValueError):
    """Raised when an order-state transition is invalid."""

    def __init__(
        self,
        model_id: str,
        order_intent_id: str,
        reason: str,
        *,
        previous_state: TradeManagerOrderState | None = None,
        next_state: TradeManagerOrderState | None = None,
    ) -> None:
        self.model_id = model_id
        self.order_intent_id = order_intent_id
        self.reason = reason
        self.previous_state = previous_state
        self.next_state = next_state
        super().__init__(
            f"Trade Manager order state failed for {model_id!r}/{order_intent_id!r}: {reason}"
        )


def transition_from_risk_decision(decision: TradeManagerRiskDecision) -> TradeManagerOrderState:
    return TradeManagerOrderState.RISK_APPROVED if decision.allowed else TradeManagerOrderState.RISK_REJECTED


def validate_order_state_transition(
    previous_state: TradeManagerOrderState | None,
    next_state: TradeManagerOrderState,
) -> None:
    if previous_state is None:
        if next_state != TradeManagerOrderState.CREATED:
            raise OrderStateTransitionError("", "", "INITIAL_STATE_MUST_BE_CREATED", next_state=next_state)
        return
    if previous_state in TERMINAL_ORDER_STATES:
        raise OrderStateTransitionError("", "", "TERMINAL_STATE", previous_state=previous_state, next_state=next_state)
    allowed = ALLOWED_ORDER_STATE_TRANSITIONS.get(previous_state, ())
    if next_state not in allowed:
        raise OrderStateTransitionError(
            "",
            "",
            "INVALID_STATE_TRANSITION",
            previous_state=previous_state,
            next_state=next_state,
        )


def make_order_transition(
    *,
    order_intent_id: str,
    model_id: str,
    previous_state: TradeManagerOrderState | None,
    state: TradeManagerOrderState,
    reason: str,
    source: str,
    risk_decision: TradeManagerRiskDecision | None = None,
    details: dict[str, Any] | None = None,
    timestamp_ns: int | None = None,
) -> TradeManagerOrderTransition:
    validate_order_state_transition(previous_state, state)
    if timestamp_ns is not None and timestamp_ns <= 0:
        raise OrderStateTransitionError("", "", "INVALID_TRANSITION_TIMESTAMP", previous_state=previous_state, next_state=state)
    return TradeManagerOrderTransition(
        order_intent_id=order_intent_id,
        model_id=model_id,
        previous_state=previous_state,
        state=state,
        timestamp_ns=timestamp_ns if timestamp_ns is not None else time.monotonic_ns(),
        reason=reason,
        source=source,
        risk_allowed=risk_decision.allowed if risk_decision is not None else None,
        risk_reason=risk_decision.reason if risk_decision is not None else "",
        risk_action=risk_decision.action if risk_decision is not None else "",
        monitor_name=risk_decision.monitor_name if risk_decision is not None else "",
        details=details if details is not None else (risk_decision.to_dict() if risk_decision is not None else {}),
    )
