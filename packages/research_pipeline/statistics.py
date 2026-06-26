"""Small statistical helpers for edge evaluation.

The functions here are pure and dependency-light. They intentionally do not
wire into pipeline promotion gates; callers can consume the returned floats or
dicts and decide their own fail-closed policy.

Two API styles are exposed:
- Scalar API (main/edge-evaluation): ``probabilistic_sharpe_ratio(observed_sharpe, benchmark_sharpe, n_obs, ...)`` — caller computes moments.
- Stream API (continuous lane): ``psr(returns)``, ``dsr(returns, num_trials=...)``, ``summary_metrics(returns)`` — accepts a return stream and computes moments internally via NumPy. Returns 0.0 on edge cases (empty/insufficient) instead of raising.
"""

from __future__ import annotations

import math
from statistics import NormalDist
from typing import Iterable, Literal, Sequence

import numpy as np

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


# ---------------------------------------------------------------------------
# Stream-based API (continuous lane). Accepts a return stream; computes
# moments internally via NumPy; returns 0.0 on edge cases instead of raising.
# Bridges to the scalar API above.
# ---------------------------------------------------------------------------


def _as_returns(returns: Iterable[float]) -> np.ndarray:
    arr = np.asarray(list(returns), dtype=np.float64)
    if arr.size == 0:
        return arr
    return arr[np.isfinite(arr)]


def stream_sharpe(returns: Iterable[float], periods: int = 1, risk_free: float = 0.0) -> float:
    """Stream Sharpe ratio (annualised when ``periods`` > 1). Safe on empty/var=0."""
    arr = _as_returns(returns)
    if arr.size < 2:
        return 0.0
    std = float(arr.std(ddof=1))
    if std <= 0.0:
        return 0.0
    sr = (float(arr.mean()) - risk_free) / std
    return sr * math.sqrt(periods) if periods and periods != 1 else sr


def stream_sortino(returns: Iterable[float], periods: int = 1, target: float = 0.0) -> float:
    """Stream Sortino ratio using downside deviation only."""
    arr = _as_returns(returns)
    if arr.size < 2:
        return 0.0
    excess = arr - target
    downside = excess[excess < 0.0]
    if downside.size == 0:
        return 0.0
    dd_std = float(np.sqrt(np.mean(downside * downside)))
    if dd_std <= 0.0:
        return 0.0
    sr = (float(arr.mean()) - target) / dd_std
    return sr * math.sqrt(periods) if periods and periods != 1 else sr


def stream_skewness(returns: Iterable[float]) -> float:
    arr = _as_returns(returns)
    if arr.size < 3:
        return 0.0
    mean_v = float(arr.mean())
    std = float(arr.std(ddof=1))
    if std <= 0.0:
        return 0.0
    return float(np.mean(((arr - mean_v) / std) ** 3))


def stream_kurtosis(returns: Iterable[float], excess: bool = True) -> float:
    """Stream kurtosis (excess by default, matching Pearson)."""
    arr = _as_returns(returns)
    if arr.size < 4:
        return 0.0
    mean_v = float(arr.mean())
    var = float(arr.var(ddof=1))
    if var <= 0.0:
        return 0.0
    k = float(np.mean(((arr - mean_v) / math.sqrt(var)) ** 4))
    return k - 3.0 if excess else k


def max_drawdown(returns: Iterable[float]) -> float:
    """Max peak-to-trough drawdown of the additive equity curve (positive)."""
    arr = _as_returns(returns)
    if arr.size == 0:
        return 0.0
    equity = np.cumsum(arr)
    running_max = np.maximum.accumulate(equity)
    drawdowns = running_max - equity
    return float(np.max(drawdowns)) if drawdowns.size else 0.0


def cvar(returns: Iterable[float], alpha: float = 0.05) -> float:
    """Conditional Value at Risk at tail probability ``alpha`` (mean of worst alpha)."""
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    arr = _as_returns(returns)
    if arr.size == 0:
        return 0.0
    sorted_arr = np.sort(arr)
    cutoff = max(1, int(math.ceil(alpha * arr.size)))
    return float(np.mean(sorted_arr[:cutoff]))


def tail_ratio(returns: Iterable[float], q: float = 0.95) -> float:
    """Ratio of right-tail gain to left-tail loss magnitude."""
    if not 0.5 < q < 1.0:
        raise ValueError("q must be in (0.5, 1)")
    arr = _as_returns(returns)
    if arr.size < 4:
        return 0.0
    gain = float(np.quantile(arr, q))
    loss = float(np.quantile(arr, 1.0 - q))
    if abs(loss) < 1e-18:
        return 0.0
    return gain / abs(loss)


def psr(returns: Iterable[float], sharpe_benchmark: float = 0.0, periods: int = 1) -> float:
    """Stream PSR: probability true Sharpe exceeds ``sharpe_benchmark``.

    ``periods`` is intentionally ignored in the Sharpe computation — PSR is
    defined on the per-period (non-annualised) Sharpe ratio per Bailey et al.
    """
    arr = _as_returns(returns)
    n = arr.size
    if n < 3:
        return 0.0
    sr = stream_sharpe(arr, periods=1)
    if sr == 0.0:
        return 0.0
    skew = stream_skewness(arr)
    kurt = stream_kurtosis(arr, excess=True) + 3.0
    denom = math.sqrt(1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr)
    if denom <= 0.0:
        return 0.0
    z = (sr - sharpe_benchmark) * math.sqrt(n - 1) / denom
    return _normal_cdf(z)


def dsr(returns: Iterable[float], num_trials: int = 1, periods: int = 1) -> float:
    """Stream DSR: PSR with a benchmark inflated by multiple-testing selection.

    Uses the Bailey & Lopez de Prado (2014) expected-maximum-Sharpe formula:
    the benchmark is inflated by ``stdev(SR) * E[max SR over N trials]`` where
    ``E[max] = (1-gamma)*Phi^-1(1 - 1/N) + gamma*Phi^-1(1 - 1/(N*e))``.
    """
    arr = _as_returns(returns)
    n = arr.size
    if n < 3 or num_trials < 1:
        return 0.0
    sr_hat = stream_sharpe(arr, periods=1)
    if sr_hat == 0.0:
        return 0.0
    sharpe_variance = 1.0 / max(n - 1, 1)
    sharpe_stdev = math.sqrt(max(sharpe_variance, 1e-18))
    embias = expected_maximum_sharpe(num_trials)
    sr_benchmark = sharpe_stdev * embias
    return psr(arr, sharpe_benchmark=sr_benchmark, periods=periods)


def min_trl(returns: Iterable[float], confidence: float = 0.95, sharpe_benchmark: float = 0.0) -> float:
    """Stream MinTRL: minimum observations for PSR >= ``confidence``; inf if unreachable."""
    if not 0.5 < confidence < 1.0:
        raise ValueError("confidence must be in (0.5, 1)")
    arr = _as_returns(returns)
    if arr.size < 3:
        return float("inf")
    sr = stream_sharpe(arr, periods=1)
    if sr <= sharpe_benchmark:
        return float("inf")
    skew = stream_skewness(arr)
    kurt = stream_kurtosis(arr, excess=True) + 3.0
    z_target = _normal_inv_cdf(confidence)
    coeff = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr
    if coeff <= 0.0:
        return float("inf")
    delta = sr - sharpe_benchmark
    if delta <= 0.0:
        return float("inf")
    return float(1.0 + (z_target * math.sqrt(coeff) / delta) ** 2)


def summary_metrics(returns: Iterable[float], *, num_trials: int = 1, periods: int = 1) -> dict:
    """One-shot metric bundle (PDF section 10.1) from a return stream."""
    arr = _as_returns(returns)
    n = int(arr.size)
    if n == 0:
        return {
            "n": 0, "sharpe": 0.0, "sortino": 0.0, "psr": 0.0, "dsr": 0.0,
            "min_trl": float("inf"), "max_drawdown": 0.0, "cvar_95": 0.0,
            "cvar_99": 0.0, "tail_ratio": 0.0, "skew": 0.0, "kurtosis": 0.0,
            "mean_return": 0.0, "std_return": 0.0,
        }
    return {
        "n": n,
        "mean_return": float(arr.mean()),
        "std_return": float(arr.std(ddof=1)) if n > 1 else 0.0,
        "sharpe": stream_sharpe(arr, periods=periods),
        "sortino": stream_sortino(arr, periods=periods),
        "psr": psr(arr, periods=periods),
        "dsr": dsr(arr, num_trials=num_trials, periods=periods),
        "min_trl": min_trl(arr),
        "max_drawdown": max_drawdown(arr),
        "cvar_95": cvar(arr, alpha=0.05),
        "cvar_99": cvar(arr, alpha=0.01),
        "tail_ratio": tail_ratio(arr),
        "skew": stream_skewness(arr),
        "kurtosis": stream_kurtosis(arr, excess=True),
    }


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
    # Stream API (continuous lane)
    "stream_sharpe", "stream_sortino", "stream_skewness", "stream_kurtosis",
    "max_drawdown", "cvar", "tail_ratio", "psr", "dsr", "min_trl",
    "summary_metrics",
]
