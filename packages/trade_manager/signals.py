"""Phase 15 signal ingress contracts for the Trade Manager.

Signals are not orders. This module normalizes model output into a validated
envelope that Phase 16 can later convert into an order-intent schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

SIGNAL_SIDES = frozenset({"BUY", "SELL", "FLAT"})


@dataclass(frozen=True)
class ModelSignal:
    signal_id: str
    registry_id: str
    model_id: str
    candidate_id: str
    run_id: str
    timestamp_ns: int
    symbol: str
    side: str
    strength: float
    confidence: float
    expected_edge: float
    reason_code: str
    source_features_reference: str = ""
    market_context: dict[str, Any] = field(default_factory=dict)
    latency_profile: dict[str, Any] = field(default_factory=dict)
    signal_source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "registry_id": self.registry_id,
            "model_id": self.model_id,
            "candidate_id": self.candidate_id,
            "run_id": self.run_id,
            "timestamp_ns": self.timestamp_ns,
            "symbol": self.symbol,
            "side": self.side,
            "strength": self.strength,
            "confidence": self.confidence,
            "expected_edge": self.expected_edge,
            "reason_code": self.reason_code,
            "source_features_reference": self.source_features_reference,
            "market_context": dict(self.market_context),
            "latency_profile": dict(self.latency_profile),
            "signal_source": self.signal_source,
        }


class SignalSource(Protocol):
    source_name: str

    def evaluate(
        self,
        active_model: Any,
        *,
        symbol: str,
        timestamp_ns: int,
        context: Any = None,
    ) -> ModelSignal: ...


@dataclass(frozen=True)
class StaticSignalSource:
    """Deterministic signal source for tests and offline smoke checks."""

    source_name: str = "static_signal_source"
    side: str = "BUY"
    strength: float = 0.5
    confidence: float = 0.5
    expected_edge: float = 0.0
    reason_code: str = "STATIC_SIGNAL"

    def evaluate(
        self,
        active_model: Any,
        *,
        symbol: str,
        timestamp_ns: int,
        context: Any = None,
    ) -> ModelSignal:
        return ModelSignal(
            signal_id=f"{active_model.model_id}:{timestamp_ns}",
            registry_id=active_model.registry_id,
            model_id=active_model.model_id,
            candidate_id=active_model.candidate_id,
            run_id=active_model.run_id,
            timestamp_ns=timestamp_ns,
            symbol=symbol,
            side=self.side,
            strength=self.strength,
            confidence=self.confidence,
            expected_edge=self.expected_edge,
            reason_code=self.reason_code,
            source_features_reference=getattr(context, "source_features_reference", ""),
            market_context={"context": context} if isinstance(context, str) else {},
            latency_profile=dict(active_model.latency_profile),
            signal_source=self.source_name,
        )
