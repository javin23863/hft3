"""Statistical edge tests for the continuous CME lane (Phase 6 §10).

Implements Bailey & Lopez de Prado probabilistic Sharpe tools using only
NumPy/SciPy (no new dependencies). All functions accept per-trade or per-bar
returns and return JSON-serialisable floats.

References (PDF §16):
- PSR / MinTRL: Bailey & Lopez de Prado, "The Sharpe Ratio Efficient Frontier"
- DSR:        Bailey & Lopez de Prado, "The Deflated Sharpe Ratio"
- Multiple testing: Harvey & Liu, "Evaluating Trading Strategies"
"""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np
from scipy.special import erfc  # Norm.cdf via 0.5*erfc(-x/sqrt(2))


def _norm_cdf(x: float) -> float:
    """Standard normal CDF; stdlib-friendly via erfc."""
    return 0.5 * erfc(-x / math.sqrt(2.0))


def _norm_ppf(p: float) -> float:
    """Inverse standard normal CDF via scipy."""
    from scipy.stats import norm

    return float(norm.ppf(p))


def _as_returns(returns: Iterable[float]) -> np.ndarray:
    arr = np.asarray(list(returns), dtype=np.float64)
    if arr.size == 0:
        return arr
    arr = arr[np.isfinite(arr)]
    return arr


def sharpe_ratio(returns: Iterable[float], periods: int = 1, risk_free: float = 0.0) -> float:
    """Annualised-independent Sharpe ratio of the supplied return stream.

    ``periods`` is the sampling frequency per period (e.g. 252 for daily->annual).
    Returns the raw (non-annualised) Sharpe when ``periods == 1``.
    """
    arr = _as_returns(returns)
    if arr.size < 2:
        return 0.0
    mean = float(arr.mean())
    std = float(arr.std(ddof=1))
    if std <= 0.0:
        return 0.0
    sr = (mean - risk_free) / std
    return sr * math.sqrt(periods) if periods and periods != 1 else sr


def sortino_ratio(returns: Iterable[float], periods: int = 1, target: float = 0.0) -> float:
    """Sortino ratio using downside deviation only."""
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
    mean = float(arr.mean())
    sr = (mean - target) / dd_std
    return sr * math.sqrt(periods) if periods and periods != 1 else sr


def skewness(returns: Iterable[float]) -> float:
    arr = _as_returns(returns)
    if arr.size < 3:
        return 0.0
    mean = float(arr.mean())
    std = float(arr.std(ddof=1))
    if std <= 0.0:
        return 0.0
    return float(np.mean(((arr - mean) / std) ** 3))


def kurtosis(returns: Iterable[float], excess: bool = True) -> float:
    """Kurtosis (excess by default, matching Pearson)."""
    arr = _as_returns(returns)
    if arr.size < 4:
        return 0.0
    mean = float(arr.mean())
    var = float(arr.var(ddof=1))
    if var <= 0.0:
        return 0.0
    k = float(np.mean(((arr - mean) / math.sqrt(var)) ** 4))
    return k - 3.0 if excess else k


def max_drawdown(returns: Iterable[float]) -> float:
    """Maximum peak-to-trough drawdown of the cumulative return curve (positive number).

    Uses the additive equity curve (cumulative sum of returns) to avoid
    overflow from multiplicative compounding on large per-trade returns.
    """
    arr = _as_returns(returns)
    if arr.size == 0:
        return 0.0
    equity = np.cumsum(arr)
    running_max = np.maximum.accumulate(equity)
    drawdowns = running_max - equity
    return float(np.max(drawdowns)) if drawdowns.size else 0.0


def cvar(returns: Iterable[float], alpha: float = 0.05) -> float:
    """Conditional Value at Risk (expected tail loss) at level ``alpha``.

    ``alpha`` is the tail probability (e.g. 0.05 for 95% CVaR). Returns the
    mean of the worst ``alpha`` fraction of returns (a negative number for
    losing strategies).
    """
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


def probabilistic_sharpe_ratio(
    returns: Iterable[float],
    sharpe_benchmark: float = 0.0,
    periods: int = 1,
) -> float:
    """PSR: probability that the true Sharpe exceeds ``sharpe_benchmark``.

    Bailey & Lopez de Prado (2012). Uses the sample Sharpe, skew, and kurtosis
    to form a non-normality-adjusted Z statistic.
    """
    arr = _as_returns(returns)
    n = arr.size
    if n < 3:
        return 0.0
    sr = sharpe_ratio(arr, periods=1)
    if sr == 0.0:
        return 0.0
    skew = skewness(arr)
    kurt = kurtosis(arr, excess=True) + 3.0  # raw kurtosis
    denom = math.sqrt(1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr)
    if denom <= 0.0:
        return 0.0
    z = (sr - sharpe_benchmark) * math.sqrt(n - 1) / denom
    return _norm_cdf(z)


def deflated_sharpe_ratio(
    returns: Iterable[float],
    num_trials: int = 1,
    sharpe_variance: float | None = None,
    p_false_positive: float = 0.05,
    periods: int = 1,
) -> float:
    """DSR: PSR with a benchmark inflated by multiple-testing selection.

    Bailey & Lopez de Prado (2014). ``num_trials`` is the number of independent
    strategy variants tried (search budget). ``sharpe_variance`` is the
    variance of the trial Sharpe ratios; if None it is estimated from the
    sample Sharpe variance (1/T).
    """
    arr = _as_returns(returns)
    n = arr.size
    if n < 3 or num_trials < 1:
        return 0.0
    if p_false_positive <= 0.0 or p_false_positive >= 1.0:
        raise ValueError("p_false_positive must be in (0, 1)")

    sr_hat = sharpe_ratio(arr, periods=1)
    if sr_hat == 0.0:
        return 0.0

    if sharpe_variance is None:
        sharpe_variance = 1.0 / max(n - 1, 1)
    sharpe_stdev = math.sqrt(max(sharpe_variance, 1e-18))

    gamma = 0.5772156649015329  # Euler-Mascheroni constant
    embias = (1.0 - gamma) * _norm_ppf(1.0 - p_false_positive) + gamma * _norm_ppf(
        1.0 - p_false_positive ** num_trials
    )
    sr_benchmark = sharpe_stdev * embias
    return probabilistic_sharpe_ratio(arr, sharpe_benchmark=sr_benchmark, periods=periods)


def minimum_track_record_length(
    returns: Iterable[float],
    confidence: float = 0.95,
    sharpe_benchmark: float = 0.0,
) -> float:
    """MinTRL: minimum number of observations needed for PSR >= ``confidence``.

    Bailey & Lopez de Prado (2014). Solves the PSR Z equation for n given the
    sample Sharpe, skew, and kurtosis. Returns inf when the strategy cannot
    reach the target confidence with observed non-normality.
    """
    if not 0.5 < confidence < 1.0:
        raise ValueError("confidence must be in (0.5, 1)")
    arr = _as_returns(returns)
    if arr.size < 3:
        return float("inf")
    sr = sharpe_ratio(arr, periods=1)
    if sr <= sharpe_benchmark:
        return float("inf")
    skew = skewness(arr)
    kurt = kurtosis(arr, excess=True) + 3.0
    z_target = _norm_ppf(confidence)
    coeff = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr
    if coeff <= 0.0:
        return float("inf")
    delta = sr - sharpe_benchmark
    if delta <= 0.0:
        return float("inf")
    n = 1.0 + (z_target * math.sqrt(coeff) / delta) ** 2
    return float(n)


def summary_metrics(
    returns: Iterable[float],
    *,
    num_trials: int = 1,
    periods: int = 1,
) -> dict:
    """One-shot metrics bundle for a continuous candidate evaluation.

    Returns a JSON-serialisable dict with all PDF §10.1 statistical metrics
    that are computable from a return stream alone. Cost-adjusted variants
    (net PnL, fill-adjusted PnL) are added by ``cost_model`` + ``continuous_evaluation``.
    """
    arr = _as_returns(returns)
    n = int(arr.size)
    if n == 0:
        return {
            "n": 0,
            "sharpe": 0.0,
            "sortino": 0.0,
            "psr": 0.0,
            "dsr": 0.0,
            "min_trl": float("inf"),
            "max_drawdown": 0.0,
            "cvar_95": 0.0,
            "cvar_99": 0.0,
            "tail_ratio": 0.0,
            "skew": 0.0,
            "kurtosis": 0.0,
            "mean_return": 0.0,
            "std_return": 0.0,
        }
    return {
        "n": n,
        "mean_return": float(arr.mean()),
        "std_return": float(arr.std(ddof=1)) if n > 1 else 0.0,
        "sharpe": sharpe_ratio(arr, periods=periods),
        "sortino": sortino_ratio(arr, periods=periods),
        "psr": probabilistic_sharpe_ratio(arr, periods=periods),
        "dsr": deflated_sharpe_ratio(arr, num_trials=num_trials, periods=periods),
        "min_trl": minimum_track_record_length(arr),
        "max_drawdown": max_drawdown(arr),
        "cvar_95": cvar(arr, alpha=0.05),
        "cvar_99": cvar(arr, alpha=0.01),
        "tail_ratio": tail_ratio(arr),
        "skew": skewness(arr),
        "kurtosis": kurtosis(arr, excess=True),
    }