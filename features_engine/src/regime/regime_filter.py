"""
Latent regime posterior P(Z_t = z | F_t) per math model Section 5.

v0: log-linear emission scores + softmax (not full HMM forward-backward).
Uses only F_t at time t; prior posterior is blended for temporal smoothing.
"""
from __future__ import annotations

import math
from typing import Dict

import numpy as np

REGIME_LABELS = (
    "normal",
    "event_shock",
    "liquidity_vacuum",
    "stop_cascade",
    "prop_flatten",
    "book_rebuild",
    "chop",
    "trend_continuation",
    "spread_stress",
)


def _softmax(logits: Dict[str, float]) -> Dict[str, float]:
    keys = list(logits.keys())
    vals = np.array([logits[k] for k in keys], dtype=np.float64)
    vals -= vals.max()
    ex = np.exp(vals)
    ex /= ex.sum()
    return {k: float(v) for k, v in zip(keys, ex)}


class RegimeFilter:
    """Estimates P(Z_t | F_t) from observable features at time t only."""

    def __init__(self, temperature: float = 1.0):
        self.temperature = temperature
        self._prev_posterior: Dict[str, float] = {
            z: 1.0 / len(REGIME_LABELS) for z in REGIME_LABELS
        }

    def update(
        self, features: Dict[str, float], event_context: str = "NORMAL"
    ) -> Dict[str, float]:
        spread_stress = features.get("spread_stress", 1.0)
        cancel_ratio = features.get("cancel_to_add_ratio", 1.0)
        agg_imb = features.get("aggressor_volume_imbalance", 0.0)
        slope_change = features.get("book_slope_change", 0.0)
        vacuum = features.get("liquidity_vacuum_score", 0.0)
        near_cancel = features.get("near_touch_cancel_pressure", 0.0)

        logits: Dict[str, float] = {z: 0.0 for z in REGIME_LABELS}

        logits["normal"] = -abs(agg_imb) - max(0.0, spread_stress - 1.0)
        logits["spread_stress"] = max(0.0, spread_stress - 1.2) * 2.0
        logits["liquidity_vacuum"] = vacuum * 3.0 + max(0.0, spread_stress - 1.0)
        logits["stop_cascade"] = abs(agg_imb) * 2.0 + max(0.0, cancel_ratio - 1.5)
        logits["event_shock"] = max(0.0, spread_stress - 1.5) + abs(slope_change) * 2.0
        logits["book_rebuild"] = (
            max(0.0, -slope_change * agg_imb) if slope_change != 0 else 0.0
        )
        logits["trend_continuation"] = abs(agg_imb) * 1.5 - abs(slope_change)
        logits["chop"] = -abs(agg_imb) + abs(slope_change) * 0.5
        logits["prop_flatten"] = 0.0

        if event_context in (
            "CPI_TIGHT",
            "NFP_TIGHT",
            "FOMC_STATEMENT_TIGHT",
            "FOMC_PRESS_TIGHT",
            "CASH_EQUITY_OPEN",
        ):
            logits["event_shock"] += 2.0
            logits["spread_stress"] += 1.0
        if event_context in (
            "PROP_FLATTEN_TOPSTEP",
            "TPT_FLATTEN",
            "APEX_FLATTEN",
            "FRIDAY_CLOSE",
        ):
            logits["prop_flatten"] += 3.0
            logits["normal"] -= 1.0
        if event_context == "NEWS_RESTRICTION":
            logits["prop_flatten"] += 1.5
        if near_cancel > 0.5:
            logits["event_shock"] += 0.5
            logits["liquidity_vacuum"] += 0.5

        for z in REGIME_LABELS:
            logits[z] += 0.15 * math.log(self._prev_posterior.get(z, 1e-9) + 1e-12)

        posterior = _softmax({z: logits[z] / self.temperature for z in REGIME_LABELS})
        self._prev_posterior = posterior
        return posterior

    @staticmethod
    def argmax(posterior: Dict[str, float]) -> str:
        return max(posterior, key=posterior.get)

    def volatility_state(self, features: Dict[str, float]) -> str:
        return "HIGH" if features.get("spread_stress", 1.0) > 1.5 else "NORMAL"

    def liquidity_state(self, features: Dict[str, float]) -> str:
        vacuum = features.get("liquidity_vacuum_score", 0.0)
        depth10 = features.get("top_10_depth_bid", 0.0) + features.get(
            "top_10_depth_ask", 0.0
        )
        if vacuum > 0.01 or depth10 < 50:
            return "THIN"
        return "NORMAL"
