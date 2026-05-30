"""PDF_MODEL_10 — Stochastic thermodynamics / free energy (hft_framework_developer_prompt.pdf Module 3)."""

from __future__ import annotations

import math
from typing import Any, List, Optional, Sequence

import numpy as np

from .base import BaseStructuralModel, ModelOutput
from .types import StochasticThermoOutput


def gibbs_probabilities(work: Sequence[float], beta: float) -> np.ndarray:
    """p_i(beta) = exp(-beta W_i) / Z(beta)."""
    w = np.asarray(work, dtype=float)
    if w.size == 0 or beta <= 0:
        return np.array([])
    logits = -beta * w
    logits -= np.max(logits)
    expw = np.exp(logits)
    z = expw.sum()
    if z <= 0:
        return np.ones_like(w) / len(w)
    return expw / z


def partition_function(work: Sequence[float], beta: float) -> float:
    w = np.asarray(work, dtype=float)
    if w.size == 0 or beta <= 0:
        return 1.0
    logits = -beta * w
    logits -= np.max(logits)
    return float(np.exp(logits).sum())


def shannon_entropy_probs(probs: Sequence[float]) -> float:
    p = np.asarray(probs, dtype=float)
    p = p[p > 0]
    if p.size == 0:
        return 0.0
    return float(-np.sum(p * np.log(p)))


def free_energy(expected_work: float, entropy: float, beta: float) -> float:
    """F(beta) = W_beta - S(beta)/beta."""
    if beta <= 0:
        return expected_work
    return expected_work - entropy / beta


class StochasticThermoModel(BaseStructuralModel[StochasticThermoOutput]):
    model_id = "PDF_MODEL_10"

    def __init__(self, params: Optional[dict] = None):
        cfg = (params or {}).get("stochastic_thermo", {})
        self.beta = float(cfg.get("beta", 1.0))
        self.exhaustion_threshold = float(cfg.get("exhaustion_threshold", 0.0))

    def evaluate(self, **kwargs: Any) -> ModelOutput[StochasticThermoOutput]:
        work: List[float] = list(kwargs.get("strategy_work", [0.0, 0.5, 1.0, 1.5]))
        beta = float(kwargs.get("beta", self.beta))
        probs = gibbs_probabilities(work, beta)
        z = partition_function(work, beta)
        w_arr = np.asarray(work, dtype=float)
        expected_work = float(np.dot(probs, w_arr)) if probs.size else 0.0
        entropy = shannon_entropy_probs(probs)
        f_beta = free_energy(expected_work, entropy, beta)
        observed_raw = kwargs.get("observed_dissipative_work")
        if observed_raw is None:
            mean_revert = False
            dissipative = expected_work
        else:
            dissipative = float(observed_raw)
            mean_revert = dissipative >= f_beta + self.exhaustion_threshold

        payload = StochasticThermoOutput(
            partition_function=z,
            expected_work=expected_work,
            entropy=entropy,
            free_energy=f_beta,
            mean_reversion_signal=mean_revert,
        )
        return ModelOutput(
            model_id=self.model_id,
            timestamp_ns=int(kwargs.get("timestamp_ns", 0)),
            payload=payload,
        )
