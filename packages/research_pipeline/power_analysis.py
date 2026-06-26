"""Statistical power analysis for the continuous CME lane (Phase 6 §10).

Reject conclusions when the sample size is too small for the target effect
size. Uses standard Cohen's d / t-test power formulas via scipy.

Reference (PDF §16): Harvey & Liu, "Evaluating Trading Strategies" — multiple
testing and false discovery in finance.
"""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np
from scipy import stats as _stats


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


def power_ttest(
    returns: Iterable[float],
    *,
    alpha: float = 0.05,
    benchmark: float = 0.0,
    alternative: str = "greater",
) -> dict:
    """Power of a one-sample t-test for mean > benchmark on the observed sample.

    Returns ``{"power": float, "effect_size": float, "n": int, "alpha": float,
    "sufficient": bool}``. ``sufficient`` is True when power >= 0.8 (standard
    social-science convention adapted for trading research).
    """
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
    try:
        from scipy.stats import power as _power  # scipy >= 1.13
        result = _power.ttest_power(
            effect_size=effect, nobs=n, alpha=alpha, alternative=alternative
        )
        power = float(result)
    except Exception:
        # Fallback: normal approximation for one-sided t-test power.
        ncp = effect * math.sqrt(n)
        z_crit = _stats.norm.ppf(1.0 - alpha)
        power = float(_stats.norm.cdf(ncp - z_crit))
    return {
        "power": power,
        "effect_size": float(d),
        "n": n,
        "alpha": alpha,
        "sufficient": bool(power >= 0.8),
    }


def minimum_sample_size(
    target_effect: float = 0.2,
    *,
    alpha: float = 0.05,
    target_power: float = 0.8,
) -> int:
    """Minimum sample size to detect ``target_effect`` (Cohen's d) with the
    given power and significance level (one-sided t-test).
    """
    if target_effect <= 0.0:
        raise ValueError("target_effect must be positive")
    if not 0.0 < alpha < 1.0 or not 0.0 < target_power < 1.0:
        raise ValueError("alpha and target_power must be in (0, 1)")
    n = 4
    while True:
        try:
            from scipy.stats import power as _power
            p = float(_power.ttest_power(
                effect_size=target_effect, nobs=n, alpha=alpha, alternative="greater"
            ))
        except Exception:
            ncp = target_effect * math.sqrt(n)
            z_crit = _stats.norm.ppf(1.0 - alpha)
            p = float(_stats.norm.cdf(ncp - z_crit))
        if p >= target_power:
            return n
        n += 1
        if n > 1_000_000:
            return n


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
            "power": 0.0,
            "effect_size": 0.0,
            "n": n,
            "alpha": alpha,
            "sufficient": False,
            "min_n_for_small_effect": minimum_sample_size(0.2, alpha=alpha),
            "min_n_for_medium_effect": minimum_sample_size(0.5, alpha=alpha),
        }
    t = power_ttest(arr, alpha=alpha, benchmark=benchmark)
    return {
        "power": t["power"],
        "effect_size": t["effect_size"],
        "n": n,
        "alpha": alpha,
        "sufficient": t["sufficient"],
        "min_n_for_small_effect": minimum_sample_size(0.2, alpha=alpha),
        "min_n_for_medium_effect": minimum_sample_size(0.5, alpha=alpha),
    }