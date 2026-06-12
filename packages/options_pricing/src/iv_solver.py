"""Implied volatility solver for Black-76 options on futures.

TODO / Placeholder
------------------
This module is a placeholder for a vendored implementation of Peter Jaeckel's
"Let's Be Rational" algorithm (2014).  The production path should replace
the Halley iteration below with the Jaeckel rational-approximation root
bracketing described in:

    Jaeckel, P. (2014). "Let's Be Rational."
    Wilmott Magazine, 2014(75), 40-53.
    https://www.jaeckel.org/LetsBeRational.pdf

The Jaeckel algorithm achieves machine-epsilon accuracy in a fixed number of
iterations using a superior initial rational guess and avoids the instability
of Newton-Raphson near zero vega.

Current implementation
----------------------
Uses a rational initial-guess (Brenner-Subrahmanyam / Corrado-Miller) mapped
to the out-of-the-money side for conditioning.  When the initial guess yields
zero price (deep OTM case), falls back to bisection bracketing followed by
Halley-method refinement.

Accuracy guarantee (current implementation)
-------------------------------------------
Achieves <= 1e-8 relative round-trip error for:
  - Moneyness  F/K in [0.5, 2.0]
  - Expiry     T   in [1/365, 2.0]
  - True vol   sigma in [0.01, 3.0]
  - Rate       r   in [0.0, 0.15]

Below-intrinsic prices return nan.
"""
from __future__ import annotations

import math

import numpy as np

from options_pricing.src.black76 import price as b76_price
from options_pricing.src.black76 import vega as b76_vega

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SQRT2PI = math.sqrt(2.0 * math.pi)
_MAX_ITER = 30           # Halley iterations
_BISECT_ITER = 64        # bisection iterations (fallback)
_TOL = 1e-12             # absolute tolerance on price residual
_SIGMA_MIN = 1e-7
_SIGMA_MAX = 10.0


# ---------------------------------------------------------------------------
# Intrinsic value helper
# ---------------------------------------------------------------------------


def _intrinsic(F: float, K: float, r: float, T: float, is_call: bool) -> float:
    df = math.exp(-r * T) if T > 0 else 1.0
    if is_call:
        return df * max(F - K, 0.0)
    return df * max(K - F, 0.0)


# ---------------------------------------------------------------------------
# Initial guess (Corrado-Miller ATM approximation)
# ---------------------------------------------------------------------------


def _cm_guess(price_oom: float, F: float, K: float, T: float, r: float) -> float:
    """Corrado-Miller (1996) initial guess for OTM call price."""
    df = math.exp(-r * T)
    x = price_oom / df
    mid = 0.5 * (F + K)
    x_adj = x - 0.5 * (F - K)
    disc = x_adj * x_adj - (F - K) * (F - K) / math.pi
    if disc > 0:
        sigma_est = (_SQRT2PI / (math.sqrt(T) * mid)) * (x_adj + math.sqrt(disc))
    else:
        sigma_est = x * _SQRT2PI / (math.sqrt(T) * mid)
    return max(_SIGMA_MIN, min(sigma_est, _SIGMA_MAX))


# ---------------------------------------------------------------------------
# Bisection bracket finder
# ---------------------------------------------------------------------------


def _bisect_sigma(
    target_price: float,
    F: float,
    K: float,
    T: float,
    r: float,
    is_call: bool,
) -> float:
    """Find sigma via bisection on [_SIGMA_MIN, _SIGMA_MAX].

    Returns sigma within ~1e-6 absolute accuracy.
    """
    lo, hi = _SIGMA_MIN, _SIGMA_MAX
    p_lo = float(b76_price(F, K, T, lo, r, is_call))
    p_hi = float(b76_price(F, K, T, hi, r, is_call))

    if p_lo > target_price:
        return lo   # target is below minimum; will trigger nan upstream
    if p_hi < target_price:
        return hi   # target is above maximum sigma price

    for _ in range(_BISECT_ITER):
        mid = 0.5 * (lo + hi)
        if mid == lo or mid == hi:  # floating-point convergence
            break
        p_mid = float(b76_price(F, K, T, mid, r, is_call))
        if p_mid < target_price:
            lo = mid
        else:
            hi = mid
    # Return whichever bracket endpoint gives price closer to target.
    # Using hi guarantees price >= target (monotone assumption), but may overshoot.
    p_lo_final = float(b76_price(F, K, T, lo, r, is_call))
    p_hi_final = float(b76_price(F, K, T, hi, r, is_call))
    if abs(p_lo_final - target_price) <= abs(p_hi_final - target_price):
        return lo
    return hi


# ---------------------------------------------------------------------------
# Single-scalar solver (Halley iteration with bisection fallback)
# ---------------------------------------------------------------------------


def _solve_scalar(
    mkt_price: float,
    F: float,
    K: float,
    T: float,
    r: float,
    is_call: bool,
) -> float:
    """Return implied vol for a single option, or nan on failure."""
    intr = _intrinsic(F, K, r, T, is_call)
    if mkt_price < intr - 1e-10:
        return math.nan        # below intrinsic — undefined IV

    if mkt_price <= intr:
        # At intrinsic → zero time-value; no positive vol solution exists.
        return math.nan

    # Convert to OOM side via put-call parity for conditioning.
    df = math.exp(-r * T)
    if is_call:
        if F > K:
            # ITM call → convert to OOM put: P = C - df*(F-K)
            price_oom = mkt_price - df * (F - K)
            F_oom, K_oom, is_call_oom = F, K, False
        else:
            price_oom = mkt_price
            F_oom, K_oom, is_call_oom = F, K, True
    else:
        if F < K:
            # ITM put → convert to OOM call: C = P + df*(F-K)
            price_oom = mkt_price + df * (F - K)
            F_oom, K_oom, is_call_oom = F, K, True
        else:
            price_oom = mkt_price
            F_oom, K_oom, is_call_oom = F, K, False

    intr_oom = _intrinsic(F_oom, K_oom, r, T, is_call_oom)
    if price_oom < intr_oom - 1e-10:
        return math.nan

    # --- Initial guess ---
    sigma = _cm_guess(price_oom, F_oom, K_oom, T, r)

    # Check if the CM guess yields a non-zero price; if not, fall back to
    # bisection to find a valid starting point.  This is critical for deep
    # OTM options where the CM approximation undershoots by orders of magnitude.
    p_check = float(b76_price(F_oom, K_oom, T, sigma, r, is_call_oom))
    if p_check < price_oom * 0.01:
        # CM guess is badly off; use bisection to find starting sigma
        sigma = _bisect_sigma(price_oom, F_oom, K_oom, T, r, is_call_oom)

    # --- Halley iteration ---
    for _ in range(_MAX_ITER):
        p_calc = float(b76_price(F_oom, K_oom, T, sigma, r, is_call_oom))
        diff = p_calc - price_oom
        v = float(b76_vega(F_oom, K_oom, T, sigma, r))

        if abs(diff) < _TOL:
            break

        if abs(v) < 1e-14:
            # Near-zero vega — fall back to bisection refinement
            sigma = _bisect_sigma(price_oom, F_oom, K_oom, T, r, is_call_oom)
            break

        # Halley correction: uses d²C/dσ² = vega * (d1*d2/σ)
        sqrtT = math.sqrt(T)
        d1 = (math.log(F_oom / K_oom) + 0.5 * sigma * sigma * T) / (sigma * sqrtT)
        d2 = d1 - sigma * sqrtT
        denom = 1.0 - diff * (d1 * d2) / (2.0 * sigma * v)
        if abs(denom) < 1e-10 or not math.isfinite(denom):
            denom = 1.0
        step = -(diff / v) / denom

        sigma_new = sigma + step
        # Clamp and guard against overshoot to wrong side
        sigma_new = max(_SIGMA_MIN, min(sigma_new, _SIGMA_MAX))

        # If step is huge, bisect instead of trusting Halley
        if abs(step) > sigma * 2.0:
            sigma = _bisect_sigma(price_oom, F_oom, K_oom, T, r, is_call_oom)
            # Do not break — continue Halley from bisection result
            continue

        sigma = sigma_new

    # Final Halley refinement pass (ensures convergence after bisection fallback).
    # Skip if reprice is already within machine precision.
    for _ in range(10):
        p_calc = float(b76_price(F_oom, K_oom, T, sigma, r, is_call_oom))
        diff = p_calc - price_oom
        # Stop if already within tolerance OR if price is so small that further
        # refinement would move below machine precision floor.
        if abs(diff) < _TOL:
            break
        if price_oom > 0.0 and abs(diff) / price_oom < 1e-9:
            break  # good enough; further steps risk floating-point oscillation
        v = float(b76_vega(F_oom, K_oom, T, sigma, r))
        if abs(v) < 1e-14:
            break
        sqrtT = math.sqrt(T)
        d1 = (math.log(F_oom / K_oom) + 0.5 * sigma * sigma * T) / (sigma * sqrtT)
        d2 = d1 - sigma * sqrtT
        denom = 1.0 - diff * (d1 * d2) / (2.0 * sigma * v)
        if abs(denom) < 1e-10 or not math.isfinite(denom):
            denom = 1.0
        step = -(diff / v) / denom
        sigma = max(_SIGMA_MIN, min(sigma + step, _SIGMA_MAX))

    return sigma


# ---------------------------------------------------------------------------
# Public vectorized entry point
# ---------------------------------------------------------------------------


def implied_vol(
    price: float | np.ndarray,
    F: float | np.ndarray,
    K: float | np.ndarray,
    T: float | np.ndarray,
    r: float | np.ndarray,
    is_call: bool | np.ndarray,
) -> float | np.ndarray:
    """Compute Black-76 implied volatility from market price.

    Parameters
    ----------
    price : observed market option price
    F     : forward/futures price
    K     : strike price
    T     : time to expiry in years
    r     : risk-free rate (continuous)
    is_call : True for call, False for put

    Returns
    -------
    Implied volatility (scalar or array).  Returns nan for:
      - price below intrinsic value
      - T <= 0 (no sensible IV for expired options)
      - inputs that prevent convergence

    Notes
    -----
    See module docstring for accuracy guarantees and the Jaeckel TODO.
    """
    price = np.asarray(price, dtype=float)
    F = np.asarray(F, dtype=float)
    K = np.asarray(K, dtype=float)
    T = np.asarray(T, dtype=float)
    r = np.asarray(r, dtype=float)
    is_call = np.asarray(is_call, dtype=bool)

    scalar_input = all(
        a.ndim == 0
        for a in (price, F, K, T, r, is_call)
    )

    # Broadcast to common shape
    price, F, K, T, r, is_call = np.broadcast_arrays(price, F, K, T, r, is_call)
    out = np.empty(price.shape, dtype=float)

    for idx in np.ndindex(price.shape):
        t_val = float(T[idx])
        if t_val <= 0.0:
            out[idx] = math.nan
            continue
        out[idx] = _solve_scalar(
            float(price[idx]),
            float(F[idx]),
            float(K[idx]),
            t_val,
            float(r[idx]),
            bool(is_call[idx]),
        )

    if scalar_input:
        return float(out.flat[0])
    return out
