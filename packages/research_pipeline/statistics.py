"""Small statistical helpers for edge evaluation.

The functions here are pure and dependency-light. They intentionally do not
wire into pipeline promotion gates; callers can consume the returned floats or
dicts and decide their own fail-closed policy.
"""

from __future__ import annotations

import math
from statistics import NormalDist
from typing import Iterable, Literal, Sequence


CorrectionMethod = Literal["bonferroni", "holm", "bh", "benjamini-hochberg"]


def _finite_values(values: Iterable[float]) -> list[float]:
    out = [float(v) for v in values]
    if not all(math.isfinite(v) for v in out):
        raise ValueError("values must be finite")
    return out


def mean(values: Sequence[float]) -> float:
    vals = _finite_values(values)
    if not vals:
        raise ValueError("values must be non-empty")
    return sum(vals) / len(vals)


def sample_std(values: Sequence[float]) -> float:
    vals = _finite_values(values)
    if len(vals) < 2:
        raise ValueError("at least two observations are required")
    mu = mean(vals)
    var = sum((x - mu) ** 2 for x in vals) / (len(vals) - 1)
    return math.sqrt(var)


def sample_skewness(values: Sequence[float]) -> float:
    vals = _finite_values(values)
    if len(vals) < 3:
        raise ValueError("at least three observations are required")
    mu = mean(vals)
    sigma = sample_std(vals)
    if sigma == 0.0:
        return 0.0
    return sum((x - mu) ** 3 for x in vals) / len(vals) / sigma**3


def sample_kurtosis(values: Sequence[float]) -> float:
    """Return total kurtosis, where Gaussian kurtosis is 3."""

    vals = _finite_values(values)
    if len(vals) < 4:
        raise ValueError("at least four observations are required")
    mu = mean(vals)
    sigma = sample_std(vals)
    if sigma == 0.0:
        return 3.0
    return sum((x - mu) ** 4 for x in vals) / len(vals) / sigma**4


def sharpe_ratio(
    returns: Sequence[float],
    *,
    benchmark_return: float = 0.0,
    periods_per_year: float | None = None,
) -> float:
    """Return sample Sharpe ratio, optionally annualized."""

    vals = [x - float(benchmark_return) for x in _finite_values(returns)]
    sigma = sample_std(vals)
    if sigma == 0.0:
        raise ValueError("returns have zero sample variance")
    sr = mean(vals) / sigma
    if periods_per_year is not None:
        if periods_per_year <= 0.0 or not math.isfinite(periods_per_year):
            raise ValueError("periods_per_year must be positive and finite")
        sr *= math.sqrt(periods_per_year)
    return sr


def _normal_cdf(x: float) -> float:
    return NormalDist().cdf(x)


def _normal_inv_cdf(p: float) -> float:
    if not 0.0 < p < 1.0:
        raise ValueError("probability must be in (0, 1)")
    return NormalDist().inv_cdf(p)


def probabilistic_sharpe_ratio(
    observed_sharpe: float,
    benchmark_sharpe: float,
    n_obs: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Bailey/Lopez de Prado Probabilistic Sharpe Ratio CDF."""

    if n_obs <= 1:
        raise ValueError("n_obs must be greater than 1")
    for name, value in (
        ("observed_sharpe", observed_sharpe),
        ("benchmark_sharpe", benchmark_sharpe),
        ("skewness", skewness),
        ("kurtosis", kurtosis),
    ):
        if not math.isfinite(float(value)):
            raise ValueError(f"{name} must be finite")
    variance_term = 1.0 - skewness * observed_sharpe + ((kurtosis - 1.0) / 4.0) * observed_sharpe**2
    if variance_term <= 0.0:
        raise ValueError("Sharpe variance term must be positive")
    z = (observed_sharpe - benchmark_sharpe) * math.sqrt(n_obs - 1.0) / math.sqrt(variance_term)
    return _normal_cdf(z)


def expected_maximum_sharpe(n_trials: int) -> float:
    """Approximate expected maximum Sharpe from ``n_trials`` independent tests."""

    if n_trials < 1:
        raise ValueError("n_trials must be at least 1")
    if n_trials == 1:
        return 0.0
    euler_gamma = 0.5772156649015329
    return (
        (1.0 - euler_gamma) * _normal_inv_cdf(1.0 - 1.0 / n_trials)
        + euler_gamma * _normal_inv_cdf(1.0 - 1.0 / (math.e * n_trials))
    )


def deflated_sharpe_ratio(
    observed_sharpe: float,
    *,
    benchmark_sharpe: float = 0.0,
    n_obs: int,
    n_trials: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
    trial_sr_variance: float = 0.0,
) -> float:
    """Return DSR as a CDF adjusted for the expected best trial."""

    benchmark = float(benchmark_sharpe)
    trials = int(n_trials)
    if trial_sr_variance > 0.0:
        benchmark += math.sqrt(trial_sr_variance) * expected_maximum_sharpe(trials)
    elif trials > 1:
        benchmark += expected_maximum_sharpe(trials)
    return probabilistic_sharpe_ratio(
        observed_sharpe,
        benchmark,
        int(n_obs),
        skewness=skewness,
        kurtosis=kurtosis,
    )


def minimum_track_record_length(
    observed_sharpe: float,
    benchmark_sharpe: float = 0.0,
    alpha: float = 0.05,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
    power: float | None = None,
) -> int | None:
    """Return MinTRL observations needed to reject ``benchmark_sharpe``.

    ``None`` means the observed Sharpe is not above the benchmark, so no finite
    positive track record length can establish superiority under this formula.
    """

    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    if observed_sharpe <= benchmark_sharpe:
        return None
    variance_term = 1.0 - skewness * observed_sharpe + ((kurtosis - 1.0) / 4.0) * observed_sharpe**2
    if variance_term <= 0.0:
        raise ValueError("Sharpe variance term must be positive")
    z = _normal_inv_cdf(1.0 - alpha)
    if power is not None:
        if not 0.0 < power < 1.0:
            raise ValueError("power must be in (0, 1)")
        z += _normal_inv_cdf(power)
    required = 1.0 + variance_term * (z / (observed_sharpe - benchmark_sharpe)) ** 2
    return max(2, int(math.ceil(required)))


def p_value_correction(p_values: Sequence[float], method: CorrectionMethod = "holm") -> list[float]:
    """Return adjusted p-values for Bonferroni, Holm, or Benjamini-Hochberg."""

    vals = _finite_values(p_values)
    if any(p < 0.0 or p > 1.0 for p in vals):
        raise ValueError("p-values must be in [0, 1]")
    n = len(vals)
    if n == 0:
        return []
    method_norm = method.lower()
    if method_norm == "bonferroni":
        return [min(1.0, p * n) for p in vals]
    order = sorted(range(n), key=vals.__getitem__)
    adjusted = [0.0] * n
    if method_norm == "holm":
        running = 0.0
        for rank, idx in enumerate(order):
            running = max(running, (n - rank) * vals[idx])
            adjusted[idx] = min(1.0, running)
        return adjusted
    if method_norm in {"bh", "benjamini-hochberg"}:
        running = 1.0
        for reverse_rank, idx in enumerate(reversed(order), start=1):
            rank = n - reverse_rank + 1
            running = min(running, vals[idx] * n / rank)
            adjusted[idx] = min(1.0, running)
        return adjusted
    raise ValueError(f"unsupported correction method: {method}")


def adjusted_p_value(p_value: float, num_tests: int, method: CorrectionMethod = "holm") -> float:
    """Return the adjusted p-value for one selected test among ``num_tests``."""

    if num_tests < 1:
        raise ValueError("num_tests must be at least 1")
    p = float(p_value)
    if p < 0.0 or p > 1.0 or not math.isfinite(p):
        raise ValueError("p_value must be in [0, 1]")
    method_norm = method.lower()
    if method_norm in {"bonferroni", "holm"}:
        return min(1.0, p * num_tests)
    if method_norm in {"bh", "benjamini-hochberg"}:
        return p
    raise ValueError(f"unsupported correction method: {method}")


__all__ = [
    "deflated_sharpe_ratio",
    "adjusted_p_value",
    "expected_maximum_sharpe",
    "mean",
    "minimum_track_record_length",
    "p_value_correction",
    "probabilistic_sharpe_ratio",
    "sample_kurtosis",
    "sample_skewness",
    "sample_std",
    "sharpe_ratio",
]
