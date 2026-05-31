"""PDF_MODEL_11 — Multivariate Hawkes toxic flow (hft_framework_developer_prompt.pdf Module 4)."""

from __future__ import annotations

from typing import Any, List, Optional, Sequence

import numpy as np

from .base import BaseStructuralModel, ModelOutput
from .types import HawkesToxicOutput


def hawkes_intensity(
    t: float,
    event_times: Sequence[float],
    mu: float,
    alpha: float,
    beta: float,
) -> float:
    """Univariate Hawkes intensity lambda(t) = mu + sum alpha exp(-beta (t - t_i))."""
    if t <= 0:
        return mu
    times = np.asarray(event_times, dtype=float)
    times = times[times < t]
    if times.size == 0:
        return mu
    contrib = alpha * np.exp(-beta * (t - times))
    return float(mu + contrib.sum())


def multivariate_hawkes_intensity(
    t: float,
    events_by_type: dict[str, Sequence[float]],
    mu: dict[str, float],
    alpha: dict[tuple[str, str], float],
    beta: float,
) -> dict[str, float]:
    """Cross-excitation: lambda_j(t) = mu_j + sum_i sum_{t_i < t} alpha_{i->j} exp(-beta (t-t_i))."""
    out: dict[str, float] = {}
    for target, mu_j in mu.items():
        lam = float(mu_j)
        for source, times in events_by_type.items():
            key = (source, target)
            a = alpha.get(key, 0.0)
            if a <= 0:
                continue
            arr = np.asarray(times, dtype=float)
            arr = arr[arr < t]
            if arr.size:
                lam += float(a * np.exp(-beta * (t - arr)).sum())
        out[target] = lam
    return out


def toxic_cascade_score(intensities: dict[str, float], baseline: dict[str, float]) -> float:
    """Self-reinforcing cascade when intensity exceeds baseline multiplicatively."""
    if not intensities:
        return 0.0
    ratios = []
    for k, lam in intensities.items():
        base = baseline.get(k, 1e-9)
        ratios.append(lam / max(base, 1e-9))
    return float(max(ratios) - 1.0)


class HawkesToxicFlowModel(BaseStructuralModel[HawkesToxicOutput]):
    model_id = "HAWKES_TOXIC_FLOW"

    def __init__(self, params: Optional[dict] = None):
        cfg = (params or {}).get("hawkes", {})
        self.mu = float(cfg.get("mu", 0.1))
        self.alpha = float(cfg.get("alpha", 0.5))
        self.beta = float(cfg.get("beta", 1.0))
        self.gamma_base = float(cfg.get("gamma_base", 0.1))
        self.gamma_scale = float(cfg.get("gamma_scale", 2.0))
        self.toxic_threshold = float(cfg.get("toxic_threshold", 1.0))

    def evaluate(self, **kwargs: Any) -> ModelOutput[HawkesToxicOutput]:
        t = float(kwargs.get("t", 1.0))
        event_times: List[float] = list(kwargs.get("market_order_times", []))
        events_by_type = kwargs.get("events_by_type")
        if events_by_type:
            mu = {k: self.mu for k in events_by_type}
            alpha = {
                (src, tgt): self.alpha
                for src in events_by_type
                for tgt in events_by_type
            }
            intensities = multivariate_hawkes_intensity(
                t, events_by_type, mu, alpha, self.beta
            )
        else:
            lam = hawkes_intensity(t, event_times, self.mu, self.alpha, self.beta)
            intensities = {"market": lam}

        baseline = {k: self.mu for k in intensities}
        score = toxic_cascade_score(intensities, baseline)
        toxic = score >= self.toxic_threshold
        gamma = self.gamma_base * (self.gamma_scale if toxic else 1.0)
        hybrid_price = float(kwargs.get("hybrid_reservation_price", 0.0))
        if toxic and hybrid_price > 0:
            reservation_skew = -score * hybrid_price * 1e-4
        else:
            reservation_skew = -score if toxic else 0.0

        payload = HawkesToxicOutput(
            intensity_by_class=intensities,
            toxic_cascade_score=score,
            risk_aversion_gamma=gamma,
            reservation_price_skew=reservation_skew,
            toxic_flow_detected=toxic,
        )
        return ModelOutput(
            model_id=self.model_id,
            timestamp_ns=int(kwargs.get("timestamp_ns", 0)),
            payload=payload,
        )
