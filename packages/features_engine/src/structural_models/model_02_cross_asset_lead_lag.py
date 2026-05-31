"""PDF_MODEL_2 — cross-asset lead-lag (consumes Model 1 OFI)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

from .base import BaseStructuralModel, ModelOutput
from .types import BookPressureOutput, CrossAssetLeadLagOutput


def fit_ridge_cross_impact(
    target_returns: np.ndarray,
    own_ofi: np.ndarray,
    leader_ofi: np.ndarray,
    alpha: float,
) -> tuple[float, float, float]:
    """
    Ridge fit: r_{t+1} = alpha + beta * OFI_own + gamma * OFI_leader.
    Returns (beta, gamma, stability=R^2).
    """
    n = len(target_returns)
    if n < 3:
        return 0.0, 0.0, 0.0
    X = np.column_stack([np.ones(n), own_ofi[:n], leader_ofi[:n]])
    y = target_returns[:n]
    # (X'X + alpha I)^{-1} X'y
    xt_x = X.T @ X + alpha * np.eye(X.shape[1])
    coef = np.linalg.solve(xt_x, X.T @ y)
    y_hat = X @ coef
    ss_res = float(np.sum((y - y_hat) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0
    return float(coef[1]), float(coef[2]), max(r2, 0.0)


def predict_target_return(
    alpha: float,
    beta: float,
    gamma: float,
    own_ofi: float,
    leader_ofi: float,
) -> float:
    return alpha + beta * own_ofi + gamma * leader_ofi


def signal_decay_curve(gamma: float, horizon: int = 5) -> List[float]:
    """Exponential decay proxy from cross-impact coefficient."""
    return [gamma * (0.5 ** k) for k in range(horizon)]


class CrossAssetLeadLagModel(BaseStructuralModel[CrossAssetLeadLagOutput]):
    model_id = "CROSS_ASSET_LEAD_LAG"

    def __init__(self, params: Optional[dict] = None):
        cfg = (params or {}).get("lead_lag", {})
        self.ridge_alpha = float(cfg.get("ridge_alpha", 1.0))
        self.min_samples = int(cfg.get("min_samples", 20))
        self._alpha = 0.0
        self._beta = 0.0
        self._gamma = 0.0

    def calibrate(
        self,
        target_returns: List[float],
        own_ofi_series: List[float],
        leader_ofi_series: List[float],
    ) -> None:
        n = min(len(target_returns), len(own_ofi_series), len(leader_ofi_series))
        if n < self.min_samples:
            return
        y = np.array(target_returns[:n], dtype=float)
        own = np.array(own_ofi_series[:n], dtype=float)
        leader = np.array(leader_ofi_series[:n], dtype=float)
        beta, gamma, _ = fit_ridge_cross_impact(y, own, leader, self.ridge_alpha)
        self._beta = beta
        self._gamma = gamma
        self._alpha = float(y.mean() - beta * own.mean() - gamma * leader.mean())

    def evaluate(self, **kwargs: Any) -> ModelOutput[CrossAssetLeadLagOutput]:
        leader = str(kwargs.get("leader_asset", "ES"))
        target = str(kwargs.get("target_asset", "MES"))
        own_ofi = float(kwargs.get("own_ofi", 0.0))
        leader_ofi = float(kwargs.get("leader_ofi", 0.0))

        book_by_asset: Dict[str, BookPressureOutput] = kwargs.get("book_pressure_by_asset", {})
        if book_by_asset:
            if target in book_by_asset:
                own_ofi = book_by_asset[target].OFI_smooth
            if leader in book_by_asset:
                leader_ofi = book_by_asset[leader].OFI_smooth

        if "target_returns" in kwargs:
            self.calibrate(
                kwargs["target_returns"],
                kwargs.get("own_ofi_series", []),
                kwargs.get("leader_ofi_series", []),
            )

        pred = predict_target_return(self._alpha, self._beta, self._gamma, own_ofi, leader_ofi)
        stability = abs(self._gamma) / (abs(self._beta) + 1e-9)
        decay = signal_decay_curve(self._gamma)

        payload = CrossAssetLeadLagOutput(
            leader_asset=leader,
            target_asset=target,
            cross_impact_score=self._gamma,
            predicted_target_return=pred,
            lead_lag_stability=stability,
            signal_decay_curve=decay,
        )
        return ModelOutput(
            model_id=self.model_id,
            timestamp_ns=kwargs.get("timestamp_ns", 0),
            payload=payload,
        )
