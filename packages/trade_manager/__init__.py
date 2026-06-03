"""Phase 14 Trade Manager handoff package."""

from trade_manager.manager import (
    ActiveModel,
    TradeManager,
    TradeManagerActivationError,
    TradeManagerError,
    TradeManagerSignalError,
)
from trade_manager.order_intent import (
    OrderIntentValidationError,
    TradeManagerOrderIntent,
    order_intent_from_signal,
)
from trade_manager.order_state import (
    ORDER_STATE_VALUES,
    TERMINAL_ORDER_STATES,
    OrderStateTransitionError,
    TradeManagerOrderState,
    TradeManagerOrderTransition,
)
from trade_manager.risk_layer import (
    TradeManagerRiskConfig,
    TradeManagerRiskContext,
    TradeManagerRiskDecision,
    TradeManagerRiskError,
    TradeManagerRiskLayer,
    load_risk_config,
)
from trade_manager.signals import ModelSignal, SignalSource, StaticSignalSource

__all__ = [
    "ActiveModel",
    "TradeManager",
    "TradeManagerActivationError",
    "TradeManagerError",
    "TradeManagerSignalError",
    "OrderIntentValidationError",
    "TradeManagerOrderIntent",
    "order_intent_from_signal",
    "ORDER_STATE_VALUES",
    "TERMINAL_ORDER_STATES",
    "OrderStateTransitionError",
    "TradeManagerOrderState",
    "TradeManagerOrderTransition",
    "TradeManagerRiskConfig",
    "TradeManagerRiskContext",
    "TradeManagerRiskDecision",
    "TradeManagerRiskError",
    "TradeManagerRiskLayer",
    "load_risk_config",
    "ModelSignal",
    "SignalSource",
    "StaticSignalSource",
]
