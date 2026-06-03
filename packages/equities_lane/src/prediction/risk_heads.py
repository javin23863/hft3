"""Risk heads — dilution, halt, slippage, capacity, manipulation risk.

Separate risk models that estimate negative-alpha processes independent
of runner probability.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

from .features import FEATURE_NAMES
from .types import ModelConfig, RiskEstimate

logger = logging.getLogger(__name__)


class RiskModel:
    def __init__(self, config: ModelConfig) -> None:
        self.config = config
        self._dilution_model: Any = None
        self._halt_model: Any = None
        self._slippage_model: Any = None
        self._capacity_model: Any = None
        self._manipulation_model: Any = None
        self.feature_names = list(FEATURE_NAMES)
        self._trained = False

    def train(
        self,
        X: np.ndarray,
        dilution_labels: np.ndarray,
        halt_labels: np.ndarray,
        slippage_labels: np.ndarray,
        capacity_labels: np.ndarray | None = None,
        manipulation_labels: np.ndarray | None = None,
        sample_weights: np.ndarray | None = None,
    ) -> dict[str, float]:
        try:
            import lightgbm as lgb
        except ImportError:
            raise ImportError("lightgbm required for risk model training")

        metrics: dict[str, float] = {}
        weights = sample_weights if sample_weights is not None else np.ones(len(X))

        cls_params = dict(
            n_estimators=self.config.lgb_n_estimators // 3,
            max_depth=self.config.lgb_max_depth - 1,
            learning_rate=self.config.lgb_learning_rate,
            min_child_samples=max(self.config.lgb_min_child_samples // 2, 5),
            subsample=self.config.lgb_subsample,
            colsample_bytree=self.config.lgb_colsample_bytree,
            objective="binary",
            metric="binary_logloss",
            verbose=-1,
            random_state=42,
        )

        reg_params = dict(
            n_estimators=self.config.lgb_n_estimators // 3,
            max_depth=self.config.lgb_max_depth - 1,
            learning_rate=self.config.lgb_learning_rate,
            min_child_samples=max(self.config.lgb_min_child_samples // 2, 5),
            subsample=self.config.lgb_subsample,
            colsample_bytree=self.config.lgb_colsample_bytree,
            objective="huber",
            metric="mae",
            verbose=-1,
            random_state=42,
        )

        self._dilution_model = lgb.LGBMClassifier(**cls_params)
        self._dilution_model.fit(X, dilution_labels, sample_weight=weights)
        dil_pred = self._dilution_model.predict(X)
        metrics["train_dilution_acc"] = float(np.mean(dil_pred == dilution_labels))
        metrics["train_dilution_rate"] = float(np.mean(dilution_labels))

        self._halt_model = lgb.LGBMClassifier(**cls_params)
        self._halt_model.fit(X, halt_labels, sample_weight=weights)
        halt_pred = self._halt_model.predict(X)
        metrics["train_halt_acc"] = float(np.mean(halt_pred == halt_labels))
        metrics["train_halt_rate"] = float(np.mean(halt_labels))

        self._slippage_model = lgb.LGBMRegressor(**reg_params)
        self._slippage_model.fit(X, slippage_labels, sample_weight=weights)
        slip_pred = self._slippage_model.predict(X)
        metrics["train_slippage_mae"] = float(np.mean(np.abs(slip_pred - slippage_labels)))

        if capacity_labels is not None:
            self._capacity_model = lgb.LGBMRegressor(**reg_params)
            self._capacity_model.fit(X, capacity_labels, sample_weight=weights)
            cap_pred = self._capacity_model.predict(X)
            metrics["train_capacity_mae"] = float(np.mean(np.abs(cap_pred - capacity_labels)))

        if manipulation_labels is not None:
            self._manipulation_model = lgb.LGBMClassifier(**cls_params)
            self._manipulation_model.fit(X, manipulation_labels, sample_weight=weights)
            man_pred = self._manipulation_model.predict(X)
            metrics["train_manipulation_acc"] = float(np.mean(man_pred == manipulation_labels))

        self._trained = True
        return metrics

    def predict(self, X: np.ndarray) -> list[RiskEstimate]:
        if not self._trained:
            raise RuntimeError("RiskModel not trained")

        dil_p = self._dilution_model.predict_proba(X)[:, 1]
        halt_p = self._halt_model.predict_proba(X)[:, 1]
        slip_e = self._slippage_model.predict(X)

        estimates: list[RiskEstimate] = []
        for i in range(len(X)):
            p_dil = float(dil_p[i])
            e_dil_loss = p_dil * 0.25
            p_halt = float(halt_p[i])
            e_slip = float(max(slip_e[i], 0.001))
            e_cap = 0.0
            if self._capacity_model is not None:
                e_cap = float(self._capacity_model.predict(X[i:i+1])[0])
            p_manip = 0.0
            if self._manipulation_model is not None:
                p_manip = float(self._manipulation_model.predict_proba(X[i:i+1])[0, 1])

            estimates.append(RiskEstimate(
                p_dilution_gap=p_dil,
                expected_dilution_loss=e_dil_loss,
                p_halt_event=p_halt,
                expected_slippage=e_slip,
                expected_capacity=e_cap,
                p_manipulation_risk=p_manip,
            ))
        return estimates

    def predict_single(self, x: np.ndarray) -> RiskEstimate:
        return self.predict(x.reshape(1, -1))[0]

    def save(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        meta = {"feature_names": self.feature_names, "components": []}
        if self._dilution_model is not None:
            self._dilution_model.booster_.save_model(str(path / "dilution.txt"))
            meta["components"].append("dilution")
        if self._halt_model is not None:
            self._halt_model.booster_.save_model(str(path / "halt.txt"))
            meta["components"].append("halt")
        if self._slippage_model is not None:
            self._slippage_model.booster_.save_model(str(path / "slippage.txt"))
            meta["components"].append("slippage")
        if self._capacity_model is not None:
            self._capacity_model.booster_.save_model(str(path / "capacity.txt"))
            meta["components"].append("capacity")
        if self._manipulation_model is not None:
            self._manipulation_model.booster_.save_model(str(path / "manipulation.txt"))
            meta["components"].append("manipulation")
        (path / "risk_meta.json").write_text(json.dumps(meta, indent=2))

    def load(self, path: Path) -> None:
        try:
            import lightgbm as lgb
        except ImportError:
            raise ImportError("lightgbm required for model loading")

        meta = json.loads((path / "risk_meta.json").read_text())
        self.feature_names = meta["feature_names"]
        for comp in meta["components"]:
            booster = lgb.Booster(model_file=str(path / f"{comp}.txt"))
            if comp in ("dilution", "halt", "manipulation"):
                setattr(self, f"_{comp}_model", _ClsWrapper(booster))
            else:
                setattr(self, f"_{comp}_model", _RegWrapper(booster))
        self._trained = True


class _ClsWrapper:
    def __init__(self, booster: Any) -> None:
        self.booster_ = booster

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        p = self.booster_.predict(X)
        return np.column_stack([1 - p, p])

    def predict(self, X: np.ndarray) -> np.ndarray:
        return (self.booster_.predict(X) > 0.5).astype(int)


class _RegWrapper:
    def __init__(self, booster: Any) -> None:
        self.booster_ = booster

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.booster_.predict(X)
