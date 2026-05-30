"""PDF_MODEL_9 — Quantum spread defense (hft_framework_developer_prompt.pdf Module 2)."""

from __future__ import annotations

import math
from typing import Any, Optional

import numpy as np

from .base import BaseStructuralModel, ModelOutput
from .types import QuantumSpreadOutput


def bessel_i0(x: float, n_steps: int = 64) -> float:
    """I_0(x) = (1/pi) integral_0^pi exp(x cos phi) dphi."""
    if x < 0:
        x = abs(x)
    if x > 50.0:
        return math.exp(x) / math.sqrt(2.0 * math.pi * x)
    phis = np.linspace(0.0, math.pi, n_steps)
    integrand = np.exp(x * np.cos(phis))
    return float(np.trapz(integrand, phis) / math.pi)


def spread_params(xi1: float, kappa1: float) -> tuple[float, float]:
    """a, b from liquidity components xi1, kappa1."""
    xi2 = xi1 * xi1
    ka2 = kappa1 * kappa1
    a = 0.25 * (1.0 / xi2 + 1.0 / ka2)
    b = 0.25 * (1.0 / xi2 - 1.0 / ka2)
    return a, b


def spread_probability(delta: float, xi1: float, kappa1: float) -> float:
    """
    P(Delta) = (1/(xi1*kappa1)) * Delta * exp(-a Delta^2) * I_0(b Delta^2).
    Returns 0 for non-positive delta.
    """
    if delta <= 0 or xi1 <= 0 or kappa1 <= 0:
        return 0.0
    a, b = spread_params(xi1, kappa1)
    d2 = delta * delta
    raw = (delta / (xi1 * kappa1)) * math.exp(-a * d2) * bessel_i0(b * d2)
    return max(raw, 0.0)


def collapse_risk(prob_small: float, prob_wide: float) -> float:
    """Skew toward spread collapse when small-spread mass dominates."""
    denom = prob_small + prob_wide + 1e-12
    return prob_small / denom


class QuantumSpreadDefenseModel(BaseStructuralModel[QuantumSpreadOutput]):
    model_id = "PDF_MODEL_9"

    def __init__(self, params: Optional[dict] = None):
        cfg = (params or {}).get("quantum_spread", {})
        self.xi1 = float(cfg.get("xi1", 1.0))
        self.kappa1 = float(cfg.get("kappa1", 1.0))
        self.small_delta = float(cfg.get("small_delta", 0.25))
        self.wide_delta = float(cfg.get("wide_delta", 2.0))
        self.collapse_threshold = float(cfg.get("collapse_threshold", 0.65))

    def evaluate(self, **kwargs: Any) -> ModelOutput[QuantumSpreadOutput]:
        xi1 = float(kwargs.get("xi1", self.xi1))
        kappa1 = float(kwargs.get("kappa1", self.kappa1))
        delta = float(kwargs.get("spread_ticks", self.small_delta))
        p_delta = spread_probability(delta, xi1, kappa1)
        p_small = spread_probability(self.small_delta, xi1, kappa1)
        p_wide = spread_probability(self.wide_delta, xi1, kappa1)
        risk = collapse_risk(p_small, p_wide)
        cancel = risk >= self.collapse_threshold

        payload = QuantumSpreadOutput(
            spread_probability=p_delta,
            collapse_risk=risk,
            cancel_all_quotes=cancel,
            xi1=xi1,
            kappa1=kappa1,
        )
        return ModelOutput(
            model_id=self.model_id,
            timestamp_ns=int(kwargs.get("timestamp_ns", 0)),
            payload=payload,
        )
