from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .features import L3Features


@dataclass
class InstabilityScore:
    score: float
    components: dict[str, float]
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "components": self.components,
            "confidence": self.confidence,
        }


class MicrostructureInstabilityScorer:
    def __init__(self, weights: dict[str, float] | None = None):
        self._weights = weights or {
            "ask_depletion_ratio": 1.5,
            "ask_replenishment_failure": 1.3,
            "bid_support_pressure": 1.2,
            "aggressive_buy_ratio": 1.4,
            "cancel_asymmetry": 1.1,
            "depth_vacuum_score": 1.6,
            "order_book_imbalance_1": 1.0,
            "microprice_dislocation": 1.2,
            "event_acceleration": 1.3,
            "queue_collapse_score": 1.4,
            "book_resilience_decay": 1.1,
        }

    def compute(self, features: L3Features) -> InstabilityScore:
        components = {}

        components["ask_depletion"] = self._normalize(features.ask_depletion_ratio, 0, 3)
        components["replenishment_failure"] = self._normalize(features.ask_replenishment_failure, 0, 1)
        components["bid_support"] = self._normalize(features.bid_support_pressure, -1, 1)
        components["aggressive_buy"] = self._normalize(features.aggressive_buy_ratio, 0, 1)
        components["cancel_asymmetry"] = self._normalize(features.cancel_asymmetry, -1, 1)
        components["depth_vacuum"] = self._normalize(features.depth_vacuum_score, 0, 1)
        components["order_imbalance"] = self._normalize(features.order_book_imbalance_1, -1, 1)
        components["microprice_lead"] = self._normalize(features.microprice_dislocation, -0.1, 0.1)
        components["event_acceleration"] = self._normalize(features.event_acceleration, 0, 5)
        components["queue_collapse"] = self._normalize(features.queue_collapse_score, 0, 1)
        components["resilience_decay"] = self._normalize(features.book_resilience_decay, 0, 1)

        weighted_sum = 0.0
        total_weight = 0.0

        for key, weight in self._weights.items():
            if key in components:
                weighted_sum += components[key] * weight
                total_weight += weight

        score = weighted_sum / total_weight if total_weight > 0 else 0.0

        confidence = self._compute_confidence(features)

        return InstabilityScore(
            score=score,
            components=components,
            confidence=confidence,
        )

    def _normalize(self, value: float, min_val: float, max_val: float) -> float:
        if max_val <= min_val:
            return 0.0
        normalized = (value - min_val) / (max_val - min_val)
        return np.clip(normalized, 0.0, 1.0)

    def _compute_confidence(self, features: L3Features) -> float:
        confidence = 0.0

        if features.messages_per_second > 10:
            confidence += 0.2
        if features.messages_per_second > 100:
            confidence += 0.2

        if features.total_ask_depth_1 > 0:
            confidence += 0.2

        if features.event_acceleration > 0:
            confidence += 0.2

        if features.aggressive_buy_count > 0:
            confidence += 0.2

        return min(confidence, 1.0)

    def train_weights(self, X: np.ndarray, y: np.ndarray) -> dict[str, float]:
        try:
            from sklearn.linear_model import LogisticRegression

            model = LogisticRegression(C=1.0, max_iter=1000, random_state=42)
            model.fit(X, y)

            feature_names = list(self._weights.keys())
            learned_weights = {}
            for i, name in enumerate(feature_names):
                learned_weights[name] = float(model.coef_[0][i])

            self._weights = learned_weights
            return learned_weights
        except ImportError:
            return self._weights
