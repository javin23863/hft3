"""Multi-horizon event hazard model with LightGBM baseline.

Estimates P(Runner_Event within horizon h | X_i,t) using focal-loss-weighted
gradient boosting. Separate models per horizon with shared feature input.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

from .features import FEATURE_NAMES
from .types import HazardEstimate, ModelConfig

logger = logging.getLogger(__name__)


def _softplus(x: float) -> float:
    return float(np.log1p(np.exp(np.clip(x, -20, 20))))


def _focal_weight(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    gamma: float,
    alpha: float,
) -> np.ndarray:
    p = np.clip(y_pred, 1e-7, 1 - 1e-7)
    pos = alpha * (1 - p) ** gamma
    neg = (1 - alpha) * p ** gamma
    return np.where(y_true > 0.5, pos, neg)


class HazardModel:
    def __init__(self, config: ModelConfig) -> None:
        self.config = config
        self.models: dict[int, Any] = {}
        self.feature_names = list(FEATURE_NAMES)
        self._trained = False

    def train(
        self,
        X: np.ndarray,
        labels_by_horizon: dict[int, np.ndarray],
        sample_weights: np.ndarray | None = None,
    ) -> dict[str, float]:
        try:
            import lightgbm as lgb
        except ImportError:
            raise ImportError(
                "lightgbm required for hazard model training. "
                "Install with: pip install lightgbm"
            )

        metrics: dict[str, float] = {}
        base_weights = sample_weights if sample_weights is not None else np.ones(len(X))

        for h, y in labels_by_horizon.items():
            pos_rate = float(np.mean(y))
            scale_pos = (1 - pos_rate) / max(pos_rate, 1e-6)

            model = lgb.LGBMClassifier(
                n_estimators=self.config.lgb_n_estimators,
                max_depth=self.config.lgb_max_depth,
                learning_rate=self.config.lgb_learning_rate,
                min_child_samples=self.config.lgb_min_child_samples,
                subsample=self.config.lgb_subsample,
                colsample_bytree=self.config.lgb_colsample_bytree,
                reg_alpha=self.config.lgb_reg_alpha,
                reg_lambda=self.config.lgb_reg_lambda,
                scale_pos_weight=scale_pos,
                objective="binary",
                metric=["binary_logloss", "auc"],
                verbose=-1,
                random_state=42,
            )

            preds_init = np.full(len(y), pos_rate)
            fw = _focal_weight(
                y, preds_init,
                self.config.focal_loss_gamma,
                self.config.focal_loss_alpha,
            )
            combined_weights = base_weights * fw

            model.fit(
                X, y,
                sample_weight=combined_weights,
                eval_set=[(X, y)],
            )

            self.models[h] = model

            preds = model.predict_proba(X)[:, 1]
            auc = _pr_auc(y, preds)
            metrics[f"train_auc_h{h}"] = auc
            metrics[f"train_pos_rate_h{h}"] = pos_rate

        self._trained = True
        return metrics

    def predict(self, X: np.ndarray) -> list[HazardEstimate]:
        if not self._trained:
            raise RuntimeError("Model not trained")

        raw: dict[int, np.ndarray] = {}
        for h, model in self.models.items():
            raw[h] = model.predict_proba(X)[:, 1]

        estimates: list[HazardEstimate] = []
        for i in range(len(X)):
            p1 = float(raw.get(1, np.zeros(len(X)))[i])
            p2 = float(raw.get(2, np.zeros(len(X)))[i])
            p5 = float(raw.get(5, np.zeros(len(X)))[i])

            p_ah = p1 * 0.3
            p_pm = p1 * 0.5
            p_day = p1 * 0.7

            estimates.append(HazardEstimate(
                p_run_5d=p5,
                p_run_2d=p2,
                p_run_1d=p1,
                p_afterhours_ignite=p_ah,
                p_premarket_ignite=p_pm,
                p_intraday_continuation=p_day,
            ))
        return estimates

    def predict_single(self, x: np.ndarray) -> HazardEstimate:
        return self.predict(x.reshape(1, -1))[0]

    def feature_importance(self, horizon: int = 5) -> list[tuple[str, float]]:
        if horizon not in self.models:
            return []
        model = self.models[horizon]
        imp = model.feature_importances_
        pairs = list(zip(self.feature_names, imp.astype(float)))
        pairs.sort(key=lambda p: p[1], reverse=True)
        return pairs

    def save(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        meta = {
            "feature_names": self.feature_names,
            "horizons": list(self.models.keys()),
            "config": self.config.to_dict(),
        }
        (path / "meta.json").write_text(json.dumps(meta, indent=2))
        for h, model in self.models.items():
            model.booster_.save_model(str(path / f"model_h{h}.txt"))

    def load(self, path: Path) -> None:
        try:
            import lightgbm as lgb
        except ImportError:
            raise ImportError("lightgbm required for model loading")

        meta = json.loads((path / "meta.json").read_text())
        self.feature_names = meta["feature_names"]
        for h in meta["horizons"]:
            booster = lgb.Booster(model_file=str(path / f"model_h{h}.txt"))
            self.models[h] = _BoosterWrapper(booster)
        self._trained = True


class _BoosterWrapper:
    def __init__(self, booster: Any) -> None:
        self.booster_ = booster

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        p = self.booster_.predict(X)
        return np.column_stack([1 - p, p])


def _pr_auc(y_true: np.ndarray, y_score: np.ndarray) -> float:
    try:
        from sklearn.metrics import average_precision_score
        return float(average_precision_score(y_true, y_score))
    except ImportError:
        desc_idx = np.argsort(-y_score)
        y_sorted = y_true[desc_idx]
        tp = np.cumsum(y_sorted)
        fp = np.cumsum(1 - y_sorted)
        precision = tp / (tp + fp)
        recall = tp / max(tp[-1], 1)
        return float(np.trapz(precision, recall))
