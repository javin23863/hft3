"""Normal-approximation power helpers for edge evaluation.

Two API layers:
- Scalar API (edge evaluation): ``required_sample_size(effect_size, alpha=, power=)``, ``achieved_power(effect_size, n_obs)``.
- Stream API (continuous lane): ``cohens_d(returns)``, ``power_ttest_stream(returns)``, ``power_summary(returns)`` — accepts a return stream.
"""

from __future__ import annotations

import math
from statistics import NormalDist
from typing import Iterable

import numpy as np


def _z_for_alpha(alpha: float, *, two_sided: bool) -> float:
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    tail = alpha / 2.0 if two_sided else alpha
    return NormalDist().inv_cdf(1.0 - tail)


def _z_for_power(power: float) -> float:
    if not 0.0 < power < 1.0:
        raise ValueError("power must be in (0, 1)")
    return NormalDist().inv_cdf(power)


def required_sample_size(
    effect_size: float,
    *,
    alpha: float = 0.05,
    power: float = 0.8,
    two_sided: bool = True,
) -> int:
    """Return observations required for a one-sample z-style test.

    ``effect_size`` is standardized units: mean edge divided by standard
    deviation at the same observation grain.
    """

    effect = abs(float(effect_size))
    if effect <= 0.0 or not math.isfinite(effect):
        raise ValueError("effect_size must be positive and finite")
    n = ((_z_for_alpha(alpha, two_sided=two_sided) + _z_for_power(power)) / effect) ** 2
    return max(2, int(math.ceil(n)))


def minimum_sample_size(effect_size: float, alpha: float = 0.05, power: float = 0.8) -> int:
    """Compatibility alias for callers using positional alpha/power."""

    return required_sample_size(effect_size, alpha=alpha, power=power)


def compute_effect_size(observed_sharpe: float, benchmark_sharpe: float = 0.0) -> float:
    """Return positive Sharpe distance from benchmark in standardized units."""

    observed = float(observed_sharpe)
    benchmark = float(benchmark_sharpe)
    if not math.isfinite(observed) or not math.isfinite(benchmark):
        raise ValueError("Sharpe values must be finite")
    return max(0.0, observed - benchmark)


def detectable_effect_size(
    n_obs: int,
    *,
    alpha: float = 0.05,
    power: float = 0.8,
    two_sided: bool = True,
) -> float:
    """Return the minimum standardized effect detectable with ``n_obs``."""

    if n_obs < 2:
        raise ValueError("n_obs must be at least 2")
    return (_z_for_alpha(alpha, two_sided=two_sided) + _z_for_power(power)) / math.sqrt(n_obs)


def achieved_power(
    effect_size: float,
    n_obs: int,
    *,
    alpha: float = 0.05,
    two_sided: bool = True,
) -> float:
    """Approximate achieved power for a standardized effect and sample size."""

    effect = abs(float(effect_size))
    if not math.isfinite(effect):
        raise ValueError("effect_size must be finite")
    if n_obs < 2:
        raise ValueError("n_obs must be at least 2")
    z_alpha = _z_for_alpha(alpha, two_sided=two_sided)
    noncentral = effect * math.sqrt(n_obs)
    normal = NormalDist()
    upper_power = 1.0 - normal.cdf(z_alpha - noncentral)
    if two_sided:
        lower_power = normal.cdf(-z_alpha - noncentral)
        return max(0.0, min(1.0, upper_power + lower_power))
    return max(0.0, min(1.0, upper_power))


__all__ = [
    "achieved_power",
    "compute_effect_size",
    "detectable_effect_size",
    "minimum_sample_size",
    "required_sample_size",
    # Continuous-lane stream API
    "cohens_d", "power_ttest_stream", "stream_minimum_sample_size", "power_summary",
]


# ---------------------------------------------------------------------------
# Continuous-lane stream API (PDF section 10). Accepts a return stream;
# computes Cohen's d, t-test power, and minimum sample size.
# ---------------------------------------------------------------------------


def _as_returns(returns: Iterable[float]) -> np.ndarray:
    arr = np.asarray(list(returns), dtype=np.float64)
    if arr.size == 0:
        return arr
    return arr[np.isfinite(arr)]


def cohens_d(returns: Iterable[float], benchmark: float = 0.0) -> float:
    """Cohen's d effect size vs a fixed benchmark mean (default 0)."""
    arr = _as_returns(returns)
    if arr.size < 2:
        return 0.0
    std = float(arr.std(ddof=1))
    if std <= 0.0:
        return 0.0
    return (float(arr.mean()) - benchmark) / std


def power_ttest_stream(
    returns: Iterable[float],
    *,
    alpha: float = 0.05,
    benchmark: float = 0.0,
    alternative: str = "greater",
) -> dict:
    """Power of a one-sample t-test for mean > benchmark on the observed sample."""
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    arr = _as_returns(returns)
    n = int(arr.size)
    if n < 2:
        return {"power": 0.0, "effect_size": 0.0, "n": n, "alpha": alpha, "sufficient": False}
    d = cohens_d(arr, benchmark=benchmark)
    if d == 0.0:
        return {"power": 0.0, "effect_size": 0.0, "n": n, "alpha": alpha, "sufficient": False}
    effect = abs(d)
    ncp = effect * math.sqrt(n)
    z_crit = NormalDist().inv_cdf(1.0 - alpha)
    power = float(NormalDist().cdf(ncp - z_crit))
    return {
        "power": power, "effect_size": float(d), "n": n,
        "alpha": alpha, "sufficient": bool(power >= 0.8),
    }


def stream_minimum_sample_size(
    target_effect: float = 0.2,
    *,
    alpha: float = 0.05,
    target_power: float = 0.8,
) -> int:
    """Minimum sample size to detect ``target_effect`` (one-sided t-test)."""
    if target_effect <= 0.0:
        raise ValueError("target_effect must be positive")
    if not 0.0 < alpha < 1.0 or not 0.0 < target_power < 1.0:
        raise ValueError("alpha and target_power must be in (0, 1)")
    z_alpha = NormalDist().inv_cdf(1.0 - alpha)
    z_power = NormalDist().inv_cdf(target_power)
    n = ((z_alpha + z_power) / target_effect) ** 2
    return max(2, int(math.ceil(n)))


def power_summary(
    returns: Iterable[float],
    *,
    alpha: float = 0.05,
    benchmark: float = 0.0,
) -> dict:
    """One-shot power-analysis bundle for a continuous candidate."""
    arr = _as_returns(returns)
    n = int(arr.size)
    if n < 2:
        return {
            "power": 0.0, "effect_size": 0.0, "n": n, "alpha": alpha,
            "sufficient": False,
            "min_n_for_small_effect": stream_minimum_sample_size(0.2, alpha=alpha),
            "min_n_for_medium_effect": stream_minimum_sample_size(0.5, alpha=alpha),
        }
    t = power_ttest_stream(arr, alpha=alpha, benchmark=benchmark)
    return {
        "power": t["power"], "effect_size": t["effect_size"], "n": n,
        "alpha": alpha, "sufficient": t["sufficient"],
        "min_n_for_small_effect": stream_minimum_sample_size(0.2, alpha=alpha),
        "min_n_for_medium_effect": stream_minimum_sample_size(0.5, alpha=alpha),
    }
