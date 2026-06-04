"""Trade Manager model behavior envelope and state evaluation facade."""

from __future__ import annotations

from model_metrics.schemas import (
    ModelBehaviorEnvelope,
    ModelLiveObservation,
    ModelStateDecision,
)
from model_metrics.state_engine import classify_model_state


class ModelBehaviorRuleEngine:
    """Rule-based GREEN/YELLOW/RED model behavior evaluator."""

    def evaluate(
        self,
        envelope: ModelBehaviorEnvelope | dict,
        observation: ModelLiveObservation | dict,
    ) -> ModelStateDecision:
        return classify_model_state(envelope, observation)


__all__ = [
    "ModelBehaviorEnvelope",
    "ModelBehaviorRuleEngine",
    "ModelLiveObservation",
    "ModelStateDecision",
    "classify_model_state",
]
