from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .features import L3Features
from .instability import InstabilityScore


@dataclass
class L3PredictionHeads:
    p_micro_ignite_next_1s: float
    p_micro_ignite_next_5s: float
    p_micro_ignite_next_30s: float
    p_micro_ignite_next_5m: float
    p_sweep_continuation: float
    p_sweep_failure: float
    p_ask_replenishment_absorption: float
    p_bid_support_collapse: float
    p_depth_vacuum_breakout: float
    p_halt_reopen_continuation: float
    expected_micro_mfe: float
    expected_micro_mae: float
    expected_micro_slippage: float
    expected_queue_fill_probability: float
    expected_adverse_selection: float

    def to_dict(self) -> dict[str, float]:
        return {k: v for k, v in self.__dict__.items()}


class L3PredictionModel:
    def __init__(self):
        self._trained = False
        self._models: dict[str, Any] = {}

    def train(
        self,
        X: np.ndarray,
        labels: dict[str, np.ndarray],
    ) -> dict[str, float]:
        try:
            import lightgbm as lgb
        except ImportError:
            raise ImportError("lightgbm required for L3 prediction model training")

        metrics = {}

        for target_name, y in labels.items():
            if target_name.startswith("p_"):
                model = lgb.LGBMClassifier(
                    n_estimators=200,
                    max_depth=5,
                    learning_rate=0.05,
                    min_child_samples=20,
                    subsample=0.8,
                    colsample_bytree=0.8,
                    objective="binary",
                    metric="binary_logloss",
                    verbose=-1,
                    random_state=42,
                )
                model.fit(X, y)
                self._models[target_name] = model
                preds = model.predict_proba(X)[:, 1]
                metrics[f"train_auc_{target_name}"] = float(np.mean((preds > 0.5) == y))
            else:
                model = lgb.LGBMRegressor(
                    n_estimators=200,
                    max_depth=5,
                    learning_rate=0.05,
                    min_child_samples=20,
                    subsample=0.8,
                    colsample_bytree=0.8,
                    objective="huber",
                    metric="mae",
                    verbose=-1,
                    random_state=42,
                )
                model.fit(X, y)
                self._models[target_name] = model
                preds = model.predict(X)
                metrics[f"train_mae_{target_name}"] = float(np.mean(np.abs(preds - y)))

        self._trained = True
        return metrics

    def predict(self, features: L3Features, instability: InstabilityScore) -> L3PredictionHeads:
        if not self._trained:
            return self._heuristic_predict(features, instability)

        x = self._features_to_array(features, instability)
        x = x.reshape(1, -1)

        predictions = {}
        for target_name, model in self._models.items():
            if target_name.startswith("p_"):
                pred = model.predict_proba(x)[0, 1]
            else:
                pred = model.predict(x)[0]
            predictions[target_name] = float(pred)

        return L3PredictionHeads(**predictions)

    def _heuristic_predict(
        self,
        features: L3Features,
        instability: InstabilityScore,
    ) -> L3PredictionHeads:
        base_prob = instability.score * 0.5

        p_1s = min(base_prob * 0.3, 0.5)
        p_5s = min(base_prob * 0.5, 0.6)
        p_30s = min(base_prob * 0.7, 0.7)
        p_5m = min(base_prob * 0.9, 0.8)

        p_sweep_cont = min(features.aggressive_buy_ratio * instability.score, 0.7)
        p_sweep_fail = max(0.1, 1.0 - p_sweep_cont)

        p_ask_absorb = min(features.ask_replenishment_failure * 0.8, 0.6)
        p_bid_collapse = max(0.1, 1.0 - features.bid_support_pressure) * 0.5

        p_vacuum_break = min(features.depth_vacuum_score * instability.score, 0.7)
        p_halt_reopen = 0.1

        e_mfe = instability.score * 0.05
        e_mae = -instability.score * 0.02
        e_slippage = 0.005 + (1.0 - features.total_ask_depth_1 / 1000.0) * 0.01
        e_queue_fill = min(features.total_ask_depth_1 / 500.0, 0.8)
        e_adverse = max(0.01, features.aggressive_buy_ratio * 0.05)

        return L3PredictionHeads(
            p_micro_ignite_next_1s=p_1s,
            p_micro_ignite_next_5s=p_5s,
            p_micro_ignite_next_30s=p_30s,
            p_micro_ignite_next_5m=p_5m,
            p_sweep_continuation=p_sweep_cont,
            p_sweep_failure=p_sweep_fail,
            p_ask_replenishment_absorption=p_ask_absorb,
            p_bid_support_collapse=p_bid_collapse,
            p_depth_vacuum_breakout=p_vacuum_break,
            p_halt_reopen_continuation=p_halt_reopen,
            expected_micro_mfe=e_mfe,
            expected_micro_mae=e_mae,
            expected_micro_slippage=e_slippage,
            expected_queue_fill_probability=e_queue_fill,
            expected_adverse_selection=e_adverse,
        )

    def _features_to_array(
        self,
        features: L3Features,
        instability: InstabilityScore,
    ) -> np.ndarray:
        feat_dict = features.to_dict()
        feat_dict["instability_score"] = instability.score
        feat_dict["instability_confidence"] = instability.confidence
        for k, v in instability.components.items():
            feat_dict[f"instability_{k}"] = v

        return np.array(list(feat_dict.values()), dtype=np.float64)
