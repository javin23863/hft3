"""PDF_MODEL_5 — dealer GEX, vanna, charm (Black-Scholes)."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from .base import BaseStructuralModel, ModelOutput
from .types import DealerHedgingOutput


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_d1_d2(spot: float, strike: float, t: float, r: float, sigma: float) -> tuple[float, float]:
    if t <= 0 or sigma <= 0 or spot <= 0 or strike <= 0:
        return 0.0, 0.0
    vol_sqrt_t = sigma * math.sqrt(t)
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * t) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    return d1, d2


def bs_gamma(spot: float, strike: float, t: float, r: float, sigma: float) -> float:
    d1, _ = bs_d1_d2(spot, strike, t, r, sigma)
    if t <= 0 or sigma <= 0:
        return 0.0
    return _norm_pdf(d1) / (spot * sigma * math.sqrt(t))


def bs_vanna(spot: float, strike: float, t: float, r: float, sigma: float) -> float:
    d1, d2 = bs_d1_d2(spot, strike, t, r, sigma)
    if t <= 0 or sigma <= 0:
        return 0.0
    return -_norm_pdf(d1) * d2 / sigma


def bs_charm(spot: float, strike: float, t: float, r: float, sigma: float, is_call: bool = True) -> float:
    d1, d2 = bs_d1_d2(spot, strike, t, r, sigma)
    if t <= 0 or sigma <= 0:
        return 0.0
    charm = -_norm_pdf(d1) * (2 * r * t - d2 * sigma * math.sqrt(t)) / (2 * t * sigma * math.sqrt(t))
    if not is_call:
        charm += r * _norm_cdf(-d1)
    return charm


def dollar_gex(gamma: float, open_interest: float, spot: float, contract_multiplier: float = 100.0) -> float:
    """Dealer GEX convention: gamma * OI * spot^2 * multiplier (sign per dealer short gamma)."""
    return gamma * open_interest * spot * spot * contract_multiplier * 0.01


def find_zero_gamma_level(gex_by_strike: Dict[float, float], spot: float) -> Optional[float]:
    strikes = sorted(gex_by_strike.keys())
    if len(strikes) < 2:
        return None
    cum = 0.0
    for i, k in enumerate(strikes):
        cum += gex_by_strike[k]
        if i > 0 and cum * gex_by_strike[strikes[0]] <= 0:
            return (strikes[i - 1] + k) / 2.0
    return spot


class DealerHedgingModel(BaseStructuralModel[DealerHedgingOutput]):
    model_id = "PDF_MODEL_5"

    def __init__(self, params: Optional[dict] = None):
        cfg = (params or {}).get("dealer_gex", {})
        self.risk_free_rate = float(cfg.get("risk_free_rate", 0.05))

    def evaluate(self, **kwargs: Any) -> ModelOutput[DealerHedgingOutput]:
        spot = float(kwargs["spot"])
        t = float(kwargs.get("time_to_expiry_years", 0.02))
        chain: List[dict] = kwargs.get("chain", [])
        gex_by_strike: Dict[float, float] = {}
        total_gex = 0.0
        total_vanna = 0.0
        total_charm = 0.0

        for leg in chain:
            strike = float(leg["strike"])
            iv = float(leg.get("iv", 0.2))
            oi = float(leg.get("open_interest", 0))
            right = leg.get("right", "C")
            is_call = right.upper().startswith("C")
            gamma = bs_gamma(spot, strike, t, self.risk_free_rate, iv)
            vanna = bs_vanna(spot, strike, t, self.risk_free_rate, iv)
            charm = bs_charm(spot, strike, t, self.risk_free_rate, iv, is_call)
            sign = -1.0  # dealer short options convention
            gex = sign * dollar_gex(gamma, oi, spot)
            gex_by_strike[strike] = gex_by_strike.get(strike, 0.0) + gex
            total_gex += gex
            total_vanna += sign * vanna * oi * spot
            total_charm += sign * charm * oi * spot

        zero_gamma = find_zero_gamma_level(gex_by_strike, spot)
        pressure = -total_gex / (abs(total_gex) + 1e-9) if total_gex != 0 else 0.0

        payload = DealerHedgingOutput(
            total_gex=total_gex,
            gex_by_strike=gex_by_strike,
            vanna_exposure=total_vanna,
            charm_exposure=total_charm,
            zero_gamma_level=zero_gamma,
            dealer_hedging_pressure=pressure,
        )
        return ModelOutput(
            model_id=self.model_id,
            timestamp_ns=kwargs.get("timestamp_ns", 0),
            payload=payload,
        )
