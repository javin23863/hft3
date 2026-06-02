"""Phase 16 Trade Manager order-intent schema.

This is deliberately distinct from `execution.interfaces.OrderIntent`. Phase 16
creates inert order-intent data only; risk checks and adapter routing are later
phases.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from trade_manager.signals import ModelSignal

ORDER_INTENT_SIDES = frozenset({"BUY", "SELL"})
ORDER_INTENT_TYPES = frozenset({"LIMIT", "MARKET"})
TIME_IN_FORCE_VALUES = frozenset({"GTC", "IOC", "FOK", "DAY"})


class OrderIntentValidationError(ValueError):
    """Raised when a Phase 16 Trade Manager order intent is invalid."""

    def __init__(
        self,
        model_id: str,
        reason: str,
        *,
        invalid_fields: list[str] | None = None,
    ) -> None:
        self.model_id = model_id
        self.reason = reason
        self.invalid_fields = invalid_fields or []
        detail = f" for {model_id!r}"
        if self.invalid_fields:
            detail += f"; invalid_fields={self.invalid_fields}"
        super().__init__(f"Trade Manager order intent failed{detail}: {reason}")


@dataclass(frozen=True)
class TradeManagerOrderIntent:
    order_intent_id: str
    registry_id: str
    model_id: str
    strategy_id: str
    signal_id: str
    timestamp: int
    symbol: str
    side: str
    quantity: float
    order_type: str
    limit_price: float | None
    time_in_force: str
    expected_edge: float
    risk_budget_id: str
    reason_code: str
    execution_profile: dict[str, Any]
    latency_profile: dict[str, Any]
    source_features_reference: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_intent_id": self.order_intent_id,
            "registry_id": self.registry_id,
            "model_id": self.model_id,
            "strategy_id": self.strategy_id,
            "signal_id": self.signal_id,
            "timestamp": self.timestamp,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "order_type": self.order_type,
            "limit_price": self.limit_price,
            "time_in_force": self.time_in_force,
            "expected_edge": self.expected_edge,
            "risk_budget_id": self.risk_budget_id,
            "reason_code": self.reason_code,
            "execution_profile": dict(self.execution_profile),
            "latency_profile": dict(self.latency_profile),
            "source_features_reference": self.source_features_reference,
        }


def order_intent_from_signal(
    active_model: Any,
    signal: ModelSignal,
    *,
    strategy_id: str,
    quantity: float,
    order_type: str,
    risk_budget_id: str,
    limit_price: float | None = None,
    time_in_force: str = "GTC",
    execution_profile: dict[str, Any] | None = None,
    order_intent_id: str | None = None,
) -> TradeManagerOrderIntent:
    """Build a validated Phase 16 order-intent envelope from a signal."""

    if signal.side == "FLAT":
        raise OrderIntentValidationError(signal.model_id, "SIGNAL_SIDE_NOT_ACTIONABLE")

    normalized_order_type = str(order_type).upper()
    normalized_tif = str(time_in_force).upper()
    profile = dict(execution_profile) if execution_profile is not None else dict(active_model.execution_assumptions)
    intent = TradeManagerOrderIntent(
        order_intent_id=order_intent_id or f"{signal.signal_id}:order-intent",
        registry_id=signal.registry_id,
        model_id=signal.model_id,
        strategy_id=strategy_id,
        signal_id=signal.signal_id,
        timestamp=signal.timestamp_ns,
        symbol=signal.symbol,
        side=signal.side,
        quantity=quantity,
        order_type=normalized_order_type,
        limit_price=limit_price,
        time_in_force=normalized_tif,
        expected_edge=signal.expected_edge,
        risk_budget_id=risk_budget_id,
        reason_code=signal.reason_code,
        execution_profile=profile,
        latency_profile=dict(signal.latency_profile),
        source_features_reference=signal.source_features_reference,
    )
    _validate_order_intent(active_model, intent)
    return intent


def _validate_order_intent(active_model: Any, intent: TradeManagerOrderIntent) -> None:
    invalid: list[str] = []
    for field_name in (
        "order_intent_id",
        "registry_id",
        "model_id",
        "strategy_id",
        "signal_id",
        "symbol",
        "side",
        "order_type",
        "time_in_force",
        "risk_budget_id",
        "reason_code",
    ):
        if not str(getattr(intent, field_name, "")):
            invalid.append(field_name)

    if intent.registry_id != active_model.registry_id:
        invalid.append("registry_id")
    if intent.model_id != active_model.model_id:
        invalid.append("model_id")
    if intent.symbol not in active_model.allowed_symbols:
        invalid.append("symbol")
    if intent.side not in ORDER_INTENT_SIDES:
        invalid.append("side")
    if not _finite(intent.timestamp) or intent.timestamp <= 0:
        invalid.append("timestamp")
    if not _finite(intent.quantity) or intent.quantity <= 0:
        invalid.append("quantity")
    if intent.order_type not in ORDER_INTENT_TYPES:
        invalid.append("order_type")
    allowed_types = {str(value).upper() for value in active_model.allowed_order_types}
    if intent.order_type not in allowed_types:
        invalid.append("order_type")
    if intent.order_type == "LIMIT" and (not _finite(intent.limit_price) or intent.limit_price <= 0):
        invalid.append("limit_price")
    if intent.order_type == "MARKET" and intent.limit_price is not None:
        invalid.append("limit_price")
    if intent.time_in_force not in TIME_IN_FORCE_VALUES:
        invalid.append("time_in_force")
    if not _finite(intent.expected_edge):
        invalid.append("expected_edge")
    if not intent.execution_profile:
        invalid.append("execution_profile")
    if not intent.latency_profile:
        invalid.append("latency_profile")

    if invalid:
        raise OrderIntentValidationError(
            intent.model_id,
            "ORDER_INTENT_INVALID",
            invalid_fields=list(dict.fromkeys(invalid)),
        )


def _finite(value: float | int | None) -> bool:
    return isinstance(value, (int, float)) and value == value and value not in (float("inf"), float("-inf"))
