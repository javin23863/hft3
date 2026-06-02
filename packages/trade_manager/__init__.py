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
    "ModelSignal",
    "SignalSource",
    "StaticSignalSource",
]
