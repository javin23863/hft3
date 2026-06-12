"""Black-76 model for options on futures.

Implements closed-form price and analytic Greeks.  All functions accept
scalar or numpy array inputs via broadcasting.  Discount factor is
exp(-r * T).

References
----------
Black, F. (1976). "The pricing of commodity contracts."
Journal of Financial Economics, 3(1-2), 167-179.
"""
from __future__ import annotations

import math

import numpy as np

# ---------------------------------------------------------------------------
# Normal CDF — use math.erf for scalar path; numpy for array path.
# scipy.stats.norm is NOT used to avoid adding a mandatory scipy dep.
# ---------------------------------------------------------------------------

_SQRT2 = math.sqrt(2.0)
_SQRT2PI = math.sqrt(2.0 * math.pi)


def _ndtr(x: float | np.ndarray) -> float | np.ndarray:
    """Standard normal CDF, scalar or array."""
    if isinstance(x, np.ndarray):
        return 0.5 * (1.0 + np.vectorize(math.erf)(x / _SQRT2))
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


def _npdf(x: float | np.ndarray) -> float | np.ndarray:
    """Standard normal PDF, scalar or array."""
    return np.exp(-0.5 * x * x) / _SQRT2PI


# ---------------------------------------------------------------------------
# Core Black-76 helpers
# ---------------------------------------------------------------------------


def _discount(r: float, T: float | np.ndarray) -> float | np.ndarray:
    return np.exp(-r * T)


def _d1d2(
    F: float | np.ndarray,
    K: float | np.ndarray,
    T: float | np.ndarray,
    sigma: float | np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute d1, d2 for Black-76.  Caller must ensure T > 0 and sigma > 0."""
    sqrtT = np.sqrt(T)
    sv = sigma * sqrtT
    d1 = (np.log(F / K) + 0.5 * sigma * sigma * T) / sv
    d2 = d1 - sv
    return d1, d2


# ---------------------------------------------------------------------------
# Public API — scalar + array broadcasting
# ---------------------------------------------------------------------------


def price(
    F: float | np.ndarray,
    K: float | np.ndarray,
    T: float | np.ndarray,
    sigma: float | np.ndarray,
    r: float | np.ndarray,
    is_call: bool | np.ndarray,
) -> float | np.ndarray:
    """Black-76 option price on a futures contract.

    Parameters
    ----------
    F : forward/futures price
    K : strike price
    T : time to expiry in years (>= 0)
    sigma : implied volatility (>= 0)
    r : risk-free rate (continuous compounding)
    is_call : True for call, False for put

    Returns
    -------
    Option fair value.  Never nan for valid (positive F, K) inputs.

    Edge cases
    ----------
    T <= 0  → intrinsic value (discounted at r=0 i.e. exp(0)=1 since
              there is no time left; discount factor treated as 1).
    sigma <= 0 → discounted intrinsic: exp(-rT) * max(±(F-K), 0).
    """
    F = np.asarray(F, dtype=float)
    K = np.asarray(K, dtype=float)
    T = np.asarray(T, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    r = np.asarray(r, dtype=float)
    is_call = np.asarray(is_call, dtype=bool)

    sign = np.where(is_call, 1.0, -1.0)
    df = _discount(r, np.maximum(T, 0.0))

    # Intrinsic (used for T<=0 and sigma<=0 branches)
    intrinsic_undiscounted = np.maximum(sign * (F - K), 0.0)
    intrinsic_t0 = intrinsic_undiscounted  # T<=0 → no discounting needed
    intrinsic_sigma0 = df * intrinsic_undiscounted

    # Full Black-76 price where T>0 and sigma>0
    safe_T = np.where(T > 0.0, T, 1.0)          # avoid divide-by-zero
    safe_sigma = np.where(sigma > 0.0, sigma, 1.0)
    d1, d2 = _d1d2(F, K, safe_T, safe_sigma)
    Nd1 = _ndtr(sign * d1)
    Nd2 = _ndtr(sign * d2)
    full_price = df * (sign * F * Nd1 - sign * K * Nd2)

    result = np.where(T <= 0.0, intrinsic_t0, np.where(sigma <= 0.0, intrinsic_sigma0, full_price))

    # Return scalar if all inputs were scalar
    if result.ndim == 0:
        return float(result)
    return result


def delta(
    F: float | np.ndarray,
    K: float | np.ndarray,
    T: float | np.ndarray,
    sigma: float | np.ndarray,
    r: float | np.ndarray,
    is_call: bool | np.ndarray,
) -> float | np.ndarray:
    """Black-76 delta: dV/dF."""
    F = np.asarray(F, dtype=float)
    K = np.asarray(K, dtype=float)
    T = np.asarray(T, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    r = np.asarray(r, dtype=float)
    is_call = np.asarray(is_call, dtype=bool)

    sign = np.where(is_call, 1.0, -1.0)
    df = _discount(r, np.maximum(T, 0.0))
    zero = np.zeros_like(F + K + T + sigma)

    safe_T = np.where(T > 0.0, T, 1.0)
    safe_sigma = np.where(sigma > 0.0, sigma, 1.0)
    d1, _ = _d1d2(F, K, safe_T, safe_sigma)
    full = df * sign * _ndtr(sign * d1)

    result = np.where((T <= 0.0) | (sigma <= 0.0), zero, full)
    if result.ndim == 0:
        return float(result)
    return result


def gamma(
    F: float | np.ndarray,
    K: float | np.ndarray,
    T: float | np.ndarray,
    sigma: float | np.ndarray,
    r: float | np.ndarray,
    is_call: bool | np.ndarray = True,  # gamma is same for call/put
) -> float | np.ndarray:
    """Black-76 gamma: d²V/dF²."""
    F = np.asarray(F, dtype=float)
    K = np.asarray(K, dtype=float)
    T = np.asarray(T, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    r = np.asarray(r, dtype=float)

    df = _discount(r, np.maximum(T, 0.0))
    zero = np.zeros_like(F + K + T + sigma)

    safe_T = np.where(T > 0.0, T, 1.0)
    safe_sigma = np.where(sigma > 0.0, sigma, 1.0)
    d1, _ = _d1d2(F, K, safe_T, safe_sigma)
    full = df * _npdf(d1) / (F * safe_sigma * np.sqrt(safe_T))

    result = np.where((T <= 0.0) | (sigma <= 0.0), zero, full)
    if result.ndim == 0:
        return float(result)
    return result


def vega(
    F: float | np.ndarray,
    K: float | np.ndarray,
    T: float | np.ndarray,
    sigma: float | np.ndarray,
    r: float | np.ndarray,
    is_call: bool | np.ndarray = True,  # vega is same for call/put
) -> float | np.ndarray:
    """Black-76 vega: dV/d(sigma).  Units: price per unit vol."""
    F = np.asarray(F, dtype=float)
    K = np.asarray(K, dtype=float)
    T = np.asarray(T, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    r = np.asarray(r, dtype=float)

    df = _discount(r, np.maximum(T, 0.0))
    zero = np.zeros_like(F + K + T + sigma)

    safe_T = np.where(T > 0.0, T, 1.0)
    safe_sigma = np.where(sigma > 0.0, sigma, 1.0)
    d1, _ = _d1d2(F, K, safe_T, safe_sigma)
    full = df * F * _npdf(d1) * np.sqrt(safe_T)

    result = np.where((T <= 0.0) | (sigma <= 0.0), zero, full)
    if result.ndim == 0:
        return float(result)
    return result


def theta(
    F: float | np.ndarray,
    K: float | np.ndarray,
    T: float | np.ndarray,
    sigma: float | np.ndarray,
    r: float | np.ndarray,
    is_call: bool | np.ndarray,
) -> float | np.ndarray:
    """Black-76 theta: dV/dT (time value decay, positive = gain as T shrinks).

    Returned as dV/dT which is negative for long options (value falls as T→0).
    """
    F = np.asarray(F, dtype=float)
    K = np.asarray(K, dtype=float)
    T = np.asarray(T, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    r = np.asarray(r, dtype=float)
    is_call = np.asarray(is_call, dtype=bool)

    sign = np.where(is_call, 1.0, -1.0)
    df = _discount(r, np.maximum(T, 0.0))
    zero = np.zeros_like(F + K + T + sigma)

    safe_T = np.where(T > 0.0, T, 1.0)
    safe_sigma = np.where(sigma > 0.0, sigma, 1.0)
    d1, d2 = _d1d2(F, K, safe_T, safe_sigma)
    Nd2 = _ndtr(sign * d2)

    # dV/dT = df*F*N'(d1)*sigma/(2*sqrt(T)) - r * V
    # Derivation: dV/dT = e^{-rT}*[F*N'(d1)*(dd1/dT - dd2/dT)] - r*e^{-rT}*[...]
    # Using dd1/dT - dd2/dT = sigma/(2*sqrt(T)) and F*N'(d1)=K*N'(d2).
    term1 = df * F * _npdf(d1) * safe_sigma / (2.0 * np.sqrt(safe_T))
    term2 = -r * df * (sign * F * _ndtr(sign * d1) - sign * K * Nd2)
    full = term1 + term2

    result = np.where((T <= 0.0) | (sigma <= 0.0), zero, full)
    if result.ndim == 0:
        return float(result)
    return result


def rho(
    F: float | np.ndarray,
    K: float | np.ndarray,
    T: float | np.ndarray,
    sigma: float | np.ndarray,
    r: float | np.ndarray,
    is_call: bool | np.ndarray,
) -> float | np.ndarray:
    """Black-76 rho: dV/dr.

    In Black-76, r enters only through the discount factor, so rho = -T * V.
    """
    F = np.asarray(F, dtype=float)
    K = np.asarray(K, dtype=float)
    T = np.asarray(T, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    r = np.asarray(r, dtype=float)
    is_call = np.asarray(is_call, dtype=bool)

    v = price(F, K, T, sigma, r, is_call)
    result = -T * v
    if np.asarray(result).ndim == 0:
        return float(result)
    return result


# ---------------------------------------------------------------------------
# Vectorised chain helper
# ---------------------------------------------------------------------------


def chain_greeks(
    F: float,
    K_array: np.ndarray,
    T: float,
    sigma_array: np.ndarray,
    r: float,
    is_call_array: np.ndarray,
) -> dict[str, np.ndarray]:
    """Compute price + all Greeks for an entire options chain at once.

    Parameters
    ----------
    F : single forward price (scalar)
    K_array : 1-D array of strikes, shape (N,)
    T : single time to expiry in years (scalar)
    sigma_array : 1-D array of implied vols, shape (N,)
    r : single risk-free rate (scalar)
    is_call_array : 1-D bool array, shape (N,)

    Returns
    -------
    dict with keys 'price', 'delta', 'gamma', 'vega', 'theta', 'rho',
    each a numpy array of shape (N,).
    """
    K_array = np.asarray(K_array, dtype=float)
    sigma_array = np.asarray(sigma_array, dtype=float)
    is_call_array = np.asarray(is_call_array, dtype=bool)

    return {
        "price": price(F, K_array, T, sigma_array, r, is_call_array),
        "delta": delta(F, K_array, T, sigma_array, r, is_call_array),
        "gamma": gamma(F, K_array, T, sigma_array, r),
        "vega": vega(F, K_array, T, sigma_array, r),
        "theta": theta(F, K_array, T, sigma_array, r, is_call_array),
        "rho": rho(F, K_array, T, sigma_array, r, is_call_array),
    }
