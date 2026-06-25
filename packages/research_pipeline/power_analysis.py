"""Normal-approximation power helpers for edge evaluation."""

from __future__ import annotations

import math
from statistics import NormalDist


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
]
