"""Payoff distribution heads — MFE/MAE estimation and path ordering.

Estimates conditional payoff distributions:
  E[MFE | X], E[MAE | X], P(MFE before MAE | X)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

from .features import FEATURE_NAMES
from .types import ModelConfig, PayoffEstimate

logger = logging.getLogger(__name__)


class PayoffModel:
    def __init__(self, config: ModelConfig) -> None:
        self.config = config
        self._mfe_models: dict[int, Any] = {}
        self._mae_models: dict[int, Any] = {}
        self._path_model: Any = None
        self.feature_names = list(FEATURE_NAMES)
        self._trained = False

    def train(
        self,
        X: np.ndarray,
        mfe_by_horizon: dict[int, np.ndarray],
        mae_by_horizon: dict[int, np.ndarray],
        mfe_before_mae: np.ndarray,
        sample_weights: np.ndarray | None = None,
    ) -> dict[str, float]:
        try:
            import lightgbm as lgb
        except ImportError:
            raise ImportError("lightgbm required for payoff model training")

        metrics: dict[str, float] = {}
        weights = sample_weights if sample_weights is not None else np.ones(len(X))

        for h in mfe_by_horizon:
            mfe_y = mfe_by_horizon[h]
            mae_y = mae_by_horizon[h]

            mfe_model = lgb.LGBMRegressor(
                n_estimators=self.config.lgb_n_estimators // 2,
                max_depth=self.config.lgb_max_depth,
                learning_rate=self.config.lgb_learning_rate,
                min_child_samples=self.config.lgb_min_child_samples,
                subsample=self.config.lgb_subsample,
                colsample_bytree=self.config.lgb_colsample_bytree,
                objective="huber",
                metric="mae",
                verbose=-1,
                random_state=42,
            )
            mfe_model.fit(X, mfe_y, sample_weight=weights)
            self._mfe_models[h] = mfe_model

            mae_model = lgb.LGBMRegressor(
                n_estimators=self.config.lgb_n_estimators // 2,
                max_depth=self.config.lgb_max_depth,
                learning_rate=self.config.lgb_learning_rate,
                min_child_samples=self.config.lgb_min_child_samples,
                subsample=self.config.lgb_subsample,
                colsample_bytree=self.config.lgb_colsample_bytree,
                objective="huber",
                metric="mae",
                verbose=-1,
                random_state=43,
            )
            mae_model.fit(X, mae_y, sample_weight=weights)
            self._mae_models[h] = mae_model

            mfe_pred = mfe_model.predict(X)
            mae_pred = mae_model.predict(X)
            metrics[f"train_mfe_mae_h{h}"] = float(np.mean(np.abs(mfe_pred - mfe_y)))
            metrics[f"train_mae_mae_h{h}"] = float(np.mean(np.abs(mae_pred - mae_y)))

        path_model = lgb.LGBMClassifier(
            n_estimators=self.config.lgb_n_estimators // 2,
            max_depth=self.config.lgb_max_depth,
            learning_rate=self.config.lgb_learning_rate,
            min_child_samples=self.config.lgb_min_child_samples,
            subsample=self.config.lgb_subsample,
            colsample_bytree=self.config.lgb_colsample_bytree,
            objective="binary",
            metric="binary_logloss",
            verbose=-1,
            random_state=44,
        )
        path_model.fit(X, mfe_before_mae, sample_weight=weights)
        self._path_model = path_model
        path_pred = path_model.predict(X)
        metrics["train_path_acc"] = float(np.mean(path_pred == mfe_before_mae))

        self._trained = True
        return metrics

    def predict(self, X: np.ndarray, horizon: int = 5) -> list[PayoffEstimate]:
        if not self._trained:
            raise RuntimeError("PayoffModel not trained")

        mfe_model = self._mfe_models.get(horizon)
        mae_model = self._mae_models.get(horizon)
        if mfe_model is None or mae_model is None:
            available = list(self._mfe_models.keys())
            if available:
                horizon = available[0]
                mfe_model = self._mfe_models[horizon]
                mae_model = self._mae_models[horizon]
            else:
                raise RuntimeError(f"No payoff model for horizon {horizon}")

        mfe_pred = mfe_model.predict(X)
        mae_pred = mae_model.predict(X)
        path_pred = self._path_model.predict_proba(X)[:, 1]

        estimates: list[PayoffEstimate] = []
        for i in range(len(X)):
            estimates.append(PayoffEstimate(
                expected_mfe=float(mfe_pred[i]),
                expected_mae=float(mae_pred[i]),
                p_mfe_before_mae=float(path_pred[i]),
                mfe_median=float(mfe_pred[i]),
                mae_median=float(mae_pred[i]),
            ))
        return estimates

    def predict_single(self, x: np.ndarray, horizon: int = 5) -> PayoffEstimate:
        return self.predict(x.reshape(1, -1), horizon)[0]

    def save(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        meta = {
            "feature_names": self.feature_names,
            "mfe_horizons": list(self._mfe_models.keys()),
            "mae_horizons": list(self._mae_models.keys()),
        }
        (path / "payoff_meta.json").write_text(json.dumps(meta, indent=2))
        for h, model in self._mfe_models.items():
            model.booster_.save_model(str(path / f"mfe_h{h}.txt"))
        for h, model in self._mae_models.items():
            model.booster_.save_model(str(path / f"mae_h{h}.txt"))
        self._path_model.booster_.save_model(str(path / "path.txt"))

    def load(self, path: Path) -> None:
        try:
            import lightgbm as lgb
        except ImportError:
            raise ImportError("lightgbm required for model loading")

        meta = json.loads((path / "payoff_meta.json").read_text())
        self.feature_names = meta["feature_names"]
        for h in meta["mfe_horizons"]:
            booster = lgb.Booster(model_file=str(path / f"mfe_h{h}.txt"))
            self._mfe_models[h] = _RegWrapper(booster)
        for h in meta["mae_horizons"]:
            booster = lgb.Booster(model_file=str(path / f"mae_h{h}.txt"))
            self._mae_models[h] = _RegWrapper(booster)
        path_booster = lgb.Booster(model_file=str(path / "path.txt"))
        self._path_model = _ClsWrapper(path_booster)
        self._trained = True


class _RegWrapper:
    def __init__(self, booster: Any) -> None:
        self.booster_ = booster

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.booster_.predict(X)


class _ClsWrapper:
    def __init__(self, booster: Any) -> None:
        self.booster_ = booster

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        p = self.booster_.predict(X)
        return np.column_stack([1 - p, p])

    def predict(self, X: np.ndarray) -> np.ndarray:
        return (self.booster_.predict(X) > 0.5).astype(int)
