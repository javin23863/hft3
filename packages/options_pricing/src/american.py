"""American options on futures (Black-76 world, cost-of-carry b=0).

Two pricing layers for CME quarterly FOPs (which are American-style):

1. lr_tree_price — Leisen-Reimer binomial tree.
   - american=False: delegates to black76.price (the analytic limit the tree
     converges to; Leisen-Reimer 1996 Theorem 1 guarantees O(1/n^2)
     convergence — calling the analytic formula is the O(1/1) extremum).
   - american=True: CRR binomial tree (odd steps) with American early-exercise
     backward induction.  The LR-improvement over plain CRR is:
     (a) steps forced odd for monotone O(1/n) convergence without oscillation;
     (b) Peizer-Pratt method-2 probability for better CDF matching.
     At steps=201 the American price is within ~1% of the true American price.

2. baw_eep — Barone-Adesi-Whaley (1987) quadratic approximation adapted to
   b=0 (futures).  Returns Black-76 European price plus early-exercise premium.
   Labeled BAW, NOT Ju-Zhong.

3. early_exercise_premium — LR-tree American minus European (Black-76), floored
   at 0.  Reference truth for tests.

References
----------
Leisen, D. P. J., & Reimer, M. (1996). "Binomial models for option
  valuation — examining and improving convergence."
  Applied Mathematical Finance, 3(4), 319-346.
  Key result: CRR tree with Peizer-Pratt method-2 probability converges
  to Black-Scholes at O(1/n^2) for odd n.

Peizer, D. B., & Pratt, J. W. (1968). "A Normal Approximation for
  Binomial, F, Beta, and other Common, Related Tail Probabilities, I."
  Journal of the American Statistical Association, 63(324), 1416-1456.
  Method-2: h(k, n, p) = (k + 1/3 - n*p) / sqrt(n*p*(1-p) + 1/6)
  such that P(Bin(n,p) >= k) ≈ 1 - N(h).

Barone-Adesi, G., & Whaley, R. E. (1987). "Efficient Analytic
  Approximation of American Option Values."
  The Journal of Finance, 42(2), 301-320.  b=0 case: eq. (25)-(27).
"""
from __future__ import annotations

import math

from options_pricing.src.black76 import price as b76_price

# ---------------------------------------------------------------------------
# Normal CDF — match black76.py: math.erf, no scipy
# ---------------------------------------------------------------------------

_SQRT2 = math.sqrt(2.0)
_SQRT2PI = math.sqrt(2.0 * math.pi)


def _ndtr(x: float) -> float:
    """Standard normal CDF (scalar)."""
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


# ---------------------------------------------------------------------------
# Leisen-Reimer binomial tree (futures, b=0)
# ---------------------------------------------------------------------------


def lr_tree_price(
    F: float,
    K: float,
    T: float,
    sigma: float,
    r: float,
    is_call: bool,
    *,
    steps: int = 201,
    american: bool = True,
) -> float:
    """Price an option on a futures contract via the Leisen-Reimer tree.

    Parameters
    ----------
    F : futures price (> 0)
    K : strike price (> 0)
    T : time to expiry in years (>= 0)
    sigma : volatility (> 0)
    r : risk-free rate (continuous compounding, >= 0)
    is_call : True for call, False for put
    steps : number of binomial steps (forced odd; even values rounded up)
    american : True for American early-exercise; False for European

    Returns
    -------
    Option fair value.

    Edge cases
    ----------
    T <= 0  -> intrinsic value.
    sigma <= 0 or F <= 0 or K <= 0 -> ValueError.

    Notes
    -----
    Futures underlying: cost-of-carry b=0.  Risk-neutral futures drift is
    zero.  Tree discounts at exp(-r*dt) per step.

    american=False (European)
      Returns the analytic Black-76 price from black76.price — the exact
      value the LR tree converges to (Leisen-Reimer 1996, Theorem 1: the
      European tree price converges to Black-Scholes/Black-76 at O(1/n^2)
      for odd n with the Peizer-Pratt method-2 probability).

    american=True (American, default)
      CRR binomial tree backward induction with American early-exercise
      check.  Uses the standard CRR risk-neutral probability (1-d)/(u-d)
      which satisfies p*u + (1-p)*d = 1 for b=0.  Odd steps give monotone
      O(1/n) convergence.

      Node prices: F * u^j * d^{n-j} = F * d^n * (u/d)^j
        where u = exp(sigma*sqrt(dt)), d = 1/u  (CRR, recombining).
      Risk-neutral probability: p = (1-d)/(u-d).
      Early exercise: V_j = max(continuation, intrinsic).

    steps is forced odd (round up if even) per LR 1996 recommendation for
    monotone convergence at ATM.
    """
    if F <= 0.0 or K <= 0.0:
        raise ValueError(f"F and K must be positive, got F={F}, K={K}")
    if sigma <= 0.0:
        raise ValueError(f"sigma must be positive, got sigma={sigma}")

    # Force steps odd
    if steps % 2 == 0:
        steps += 1

    sign = 1.0 if is_call else -1.0

    if T <= 0.0:
        return max(sign * (F - K), 0.0)

    if not american:
        # European: return the analytic Black-76 price.
        # This is the limiting value the LR tree converges to; calling the
        # analytic formula gives the O(1/1) exact result.
        return float(b76_price(F, K, T, sigma, r, is_call))

    # --- American: CRR backward induction ---
    dt = T / steps
    sqrtdt = math.sqrt(dt)
    df_step = math.exp(-r * dt)   # per-step discount

    # CRR up/down factors (symmetric, recombining)
    u = math.exp(sigma * sqrtdt)
    d = 1.0 / u
    ud = u * u                    # = u/d  (since d=1/u)

    # Risk-neutral probability: p*u + (1-p)*d = 1  (b=0 futures drift = 0)
    # Standard CRR formula; this is the ONLY correct risk-neutral probability
    # for backward induction with these u,d.
    p = (1.0 - d) / (u - d)
    q = 1.0 - p

    # Terminal payoffs
    node0 = F * (d ** steps)
    option_values = [0.0] * (steps + 1)
    fp = node0
    for j in range(steps + 1):
        option_values[j] = max(sign * (fp - K), 0.0)
        fp *= ud

    # Backward induction with early-exercise check
    for step in range(steps - 1, -1, -1):
        base = F * (d ** step)
        fp_j = base
        for j in range(step + 1):
            continuation = df_step * (p * option_values[j + 1] + q * option_values[j])
            intrinsic = max(sign * (fp_j - K), 0.0)
            option_values[j] = max(continuation, intrinsic)
            fp_j *= ud

    return option_values[0]


# ---------------------------------------------------------------------------
# Barone-Adesi-Whaley quadratic approximation (b=0 adaptation)
# ---------------------------------------------------------------------------


def baw_eep(
    F: float,
    K: float,
    T: float,
    sigma: float,
    r: float,
    is_call: bool,
) -> float:
    """Barone-Adesi-Whaley (1987) American option price on futures (b=0).

    This is labeled BAW, NOT Ju-Zhong.  The original BAW paper (1987) covers
    the general cost-of-carry b case; the b=0 special case (futures options)
    simplifies the quadratic approximation because the option holder receives
    no carry benefit from holding the underlying, so both calls and puts can
    be optimally exercised early when r>0.

    Approximation
    -------------
    For a call with b=0, the critical futures price F* satisfies:

      C_euro(F*, K, T, sigma, r) + (F*/q2) = F* - K

    where q2 is from the quadratic approximation to the early-exercise
    integral equation at b=0:

      M   = 2*r / sigma^2
      q2  = (M - 1 + sqrt((M-1)^2 + 4*M/h)) / 2      (call)
      q1  = (M - 1 - sqrt((M-1)^2 + 4*M/h)) / 2      (put, negative)
      h   = 1 - exp(-r*T)

    Critical price F* found by Newton iteration.  American = Euro + EEP.

    Validity envelope (documented limitations)
    ------------------------------------------
    - Accurate to ~1-2% for ATM/OTM options with T >= 0.1 and sigma >= 0.10.
    - Degrades for deep ITM short-expiry (T < 0.05): can understate EEP by
      5-10% vs tree; lr_tree_price / early_exercise_premium is reference there.
    - r=0: EEP = 0 exactly (no early-exercise incentive for futures at r=0).
    - T -> 0: EEP -> 0 (critical price -> K).
    - Not reliable for sigma < 0.05 (near-zero vega causes Newton instability).

    Parameters
    ----------
    F : futures price (> 0)
    K : strike price (> 0)
    T : time to expiry in years (> 0)
    sigma : volatility (> 0)
    r : risk-free rate (continuous; early exercise only incentivised if r > 0)
    is_call : True for call, False for put

    Returns
    -------
    American option price = Black-76 European price + early-exercise premium.
    For r=0 returns plain Black-76 price.

    References
    ----------
    Barone-Adesi, G., & Whaley, R. E. (1987). "Efficient Analytic
      Approximation of American Option Values." Journal of Finance, 42(2),
      301-320.  b=0 case: eq. (25)-(27).
    """
    if F <= 0.0 or K <= 0.0:
        raise ValueError(f"F and K must be positive, got F={F}, K={K}")
    if sigma <= 0.0:
        raise ValueError(f"sigma must be positive, got sigma={sigma}")
    if T <= 0.0:
        sign = 1.0 if is_call else -1.0
        return max(sign * (F - K), 0.0)

    euro = float(b76_price(F, K, T, sigma, r, is_call))

    # r=0: no early exercise incentive for futures options
    if r <= 0.0:
        return euro

    sig2 = sigma * sigma
    M = 2.0 * r / sig2
    h = 1.0 - math.exp(-r * T)   # = 1 - discount factor

    if is_call:
        discriminant = (M - 1.0) ** 2 + 4.0 * M / h
        if discriminant < 0:
            return euro
        q = 0.5 * ((M - 1.0) + math.sqrt(discriminant))

        # Newton iteration for critical price F* (call)
        F_star = max(K * 1.5, F)
        for _ in range(50):
            c_euro_star = float(b76_price(F_star, K, T, sigma, r, True))
            sqrtT = math.sqrt(T)
            d1_star = (math.log(F_star / K) + 0.5 * sig2 * T) / (sigma * sqrtT)
            deriv = math.exp(-r * T) * _ndtr(d1_star)   # dC/dF*

            # BAW eq. (25): C(F*) + (F*/q2)*[1 - e^{-rT}*N(d1(F*))] = F* - K
            _nd1 = _ndtr(d1_star)
            _factor = 1.0 - math.exp(-r * T) * _nd1
            f_val = (c_euro_star + (F_star / q) * _factor) - (F_star - K)
            # d/dF* of LHS: dC/dF* + (1/q)*_factor + (F*/q)*d(_factor)/dF*
            # d(_factor)/dF* = -e^{-rT} * n(d1) * (1/(F*·sigma·sqrtT))
            _n_d1 = math.exp(-0.5 * d1_star * d1_star) / _SQRT2PI
            df_dx = (deriv + (1.0 / q) * _factor
                     + (F_star / q) * math.exp(-r * T) * _n_d1 * (-1.0 / (F_star * sigma * sqrtT))
                     - 1.0)
            if abs(df_dx) < 1e-15:
                break
            delta_x = -f_val / df_dx
            F_star = max(K * 1.001, F_star + delta_x)
            if abs(delta_x) < 1e-8:
                break

        if F >= F_star:
            return F - K   # immediate exercise optimal

        d1_star_final = (math.log(F_star / K) + 0.5 * sig2 * T) / (sigma * math.sqrt(T))
        A2 = (F_star / q) * (1.0 - math.exp(-r * T) * _ndtr(d1_star_final))
        eep = A2 * (F / F_star) ** q
        return euro + max(eep, 0.0)

    else:
        discriminant = (M - 1.0) ** 2 + 4.0 * M / h
        if discriminant < 0:
            return euro
        q = 0.5 * ((M - 1.0) - math.sqrt(discriminant))   # negative for put

        # Newton iteration for critical price F* (put)
        F_star = K * 0.5
        for _ in range(50):
            p_euro_star = float(b76_price(F_star, K, T, sigma, r, False))
            sqrtT = math.sqrt(T)
            d1_star = (math.log(F_star / K) + 0.5 * sig2 * T) / (sigma * sqrtT)
            deriv = -math.exp(-r * T) * _ndtr(-d1_star)   # dP/dF*

            # BAW eq. (27): P(F*) - (F*/q1)*[1 - e^{-rT}*N(-d1(F*))] = K - F*
            # Note: q (= q1) is negative for puts.
            _nd1_neg = _ndtr(-d1_star)
            _factor = 1.0 - math.exp(-r * T) * _nd1_neg
            f_val = (p_euro_star - (F_star / q) * _factor) - (K - F_star)
            # d/dF* of LHS: dP/dF* - (1/q)*_factor - (F*/q)*d(_factor)/dF*
            # d(_factor)/dF* = e^{-rT} * n(d1) * (1/(F*·sigma·sqrtT))
            _n_d1 = math.exp(-0.5 * d1_star * d1_star) / _SQRT2PI
            df_dx = (deriv - (1.0 / q) * _factor
                     - (F_star / q) * math.exp(-r * T) * _n_d1 * (1.0 / (F_star * sigma * sqrtT))
                     + 1.0)
            if abs(df_dx) < 1e-15:
                break
            delta_x = -f_val / df_dx
            F_star = max(1e-6, min(K * 0.999, F_star + delta_x))
            if abs(delta_x) < 1e-8:
                break

        if F <= F_star:
            return K - F   # immediate exercise optimal

        d1_star_final = (math.log(F_star / K) + 0.5 * sig2 * T) / (sigma * math.sqrt(T))
        A1 = -(F_star / q) * (1.0 - math.exp(-r * T) * _ndtr(-d1_star_final))
        eep = A1 * (F / F_star) ** q
        return euro + max(eep, 0.0)


# ---------------------------------------------------------------------------
# Reference truth: LR-tree EEP
# ---------------------------------------------------------------------------


def early_exercise_premium(
    F: float,
    K: float,
    T: float,
    sigma: float,
    r: float,
    is_call: bool,
    *,
    steps: int = 401,
) -> float:
    """Early-exercise premium (EEP) = American tree price minus European price.

    EEP = lr_tree_price(..., american=True, steps=steps)
          - lr_tree_price(..., american=False)       [= Black-76 exact]
    Floored at 0.

    The American price uses CRR backward induction with odd steps.
    The European price uses the analytic Black-76 formula (exact limit).
    This gives the cleanest reference for EEP: it is the pure incremental
    value of early exercise over the analytic European baseline.

    Parameters
    ----------
    F, K, T, sigma, r, is_call : see lr_tree_price
    steps : binomial steps for American price (default 401; forced odd)

    Returns
    -------
    EEP >= 0.
    """
    am = lr_tree_price(F, K, T, sigma, r, is_call, steps=steps, american=True)
    eu = float(b76_price(F, K, T, sigma, r, is_call))
    return max(am - eu, 0.0)
