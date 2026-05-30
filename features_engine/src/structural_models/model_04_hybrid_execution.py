"""PDF_MODEL_4 — Avellaneda-Stoikov hybrid execution (reads Model 1 + 3)."""

from __future__ import annotations

import math
from typing import Any, Optional

from .base import BaseStructuralModel, ModelOutput
from .types import BookPressureOutput, HybridExecutionOutput, VPINToxicityOutput


def as_reservation_price(
    mid: float,
    inventory: float,
    gamma: float,
    sigma: float,
    time_remaining: float,
) -> float:
    """r(t) = S_t - q_t * gamma * sigma^2 * (T - t)."""
    return mid - inventory * gamma * (sigma ** 2) * time_remaining


def as_optimal_spread(gamma: float, kappa: float) -> float:
    """delta_a + delta_b = (2/gamma) * ln(1 + gamma/kappa)."""
    if gamma <= 0 or kappa <= 0:
        return 0.0
    return (2.0 / gamma) * math.log(1.0 + gamma / kappa)


def hybrid_reservation(
    reservation: float,
    ofi_smooth: float,
    vpin_value: float,
    lambda_scale: float,
) -> tuple[float, float, float]:
    """r*(t) = r(t) + lambda_t * OFI_smooth; lambda_t scales with VPIN."""
    vpin_mult = 1.0 + vpin_value
    lam = lambda_scale * vpin_mult
    drift = lam * ofi_smooth
    return reservation + drift, drift, vpin_mult


class HybridExecutionModel(BaseStructuralModel[HybridExecutionOutput]):
    model_id = "PDF_MODEL_4"

    def __init__(self, params: Optional[dict] = None):
        cfg = (params or {}).get("avellaneda_stoikov", {})
        self.gamma = float(cfg.get("gamma", 0.1))
        self.kappa = float(cfg.get("kappa", 1.5))
        self.default_sigma = float(cfg.get("default_sigma", 0.02))
        self.time_horizon_sec = float(cfg.get("time_horizon_sec", 3600.0))
        self.vpin_lambda_scale = float(cfg.get("vpin_lambda_scale", 0.001))
        self.vpin_cancel_threshold = 0.5

    def evaluate(self, **kwargs: Any) -> ModelOutput[HybridExecutionOutput]:
        mid = float(kwargs["mid"])
        inventory = float(kwargs.get("inventory", 0.0))
        sigma = float(kwargs.get("sigma", self.default_sigma))
        time_remaining = float(kwargs.get("time_remaining", self.time_horizon_sec))

        book_out: Optional[BookPressureOutput] = kwargs.get("book_pressure")
        vpin_out: Optional[VPINToxicityOutput] = kwargs.get("vpin")
        ofi_smooth = float(kwargs.get("ofi_smooth", book_out.OFI_smooth if book_out else 0.0))
        vpin_value = float(kwargs.get("vpin_value", vpin_out.VPIN_value if vpin_out else 0.0))

        reservation = as_reservation_price(mid, inventory, self.gamma, sigma, time_remaining)
        spread = as_optimal_spread(self.gamma, self.kappa)
        hybrid, drift, vpin_mult = hybrid_reservation(
            reservation, ofi_smooth, vpin_value, self.vpin_lambda_scale
        )
        half = spread / 2.0
        inv_penalty = inventory * self.gamma * (sigma ** 2) * time_remaining
        cancel = vpin_value >= self.vpin_cancel_threshold and abs(inventory) > 1.0
        passive_to_agg = vpin_value >= self.vpin_cancel_threshold

        payload = HybridExecutionOutput(
            reservation_price=reservation,
            hybrid_reservation_price=hybrid,
            optimal_bid=hybrid - half,
            optimal_ask=hybrid + half,
            spread_width=spread,
            inventory_penalty=inv_penalty,
            OFI_drift_component=drift,
            VPIN_multiplier=vpin_mult,
            cancel_quote_flag=cancel,
            passive_to_aggressive_flag=passive_to_agg,
        )
        return ModelOutput(
            model_id=self.model_id,
            timestamp_ns=kwargs.get("timestamp_ns", 0),
            payload=payload,
        )
