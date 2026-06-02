"""Phase 14 Trade Manager handoff package."""

from trade_manager.manager import (
    ActiveModel,
    TradeManager,
    TradeManagerActivationError,
    TradeManagerError,
    TradeManagerSignalError,
)
from trade_manager.signals import ModelSignal, SignalSource, StaticSignalSource

__all__ = [
    "ActiveModel",
    "TradeManager",
    "TradeManagerActivationError",
    "TradeManagerError",
    "TradeManagerSignalError",
    "ModelSignal",
    "SignalSource",
    "StaticSignalSource",
]
