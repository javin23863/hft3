"""Zakamouline (2006) optimal delta-hedging band for proportional transaction costs.

Reference
---------
Zakamouline, V.I. (2006). "European option pricing and hedging with both fixed
and proportional transaction costs." Journal of Economic Dynamics and Control,
30(1), 1–25.

The approximation used here is the one popularised by Sinclair, E. (2010),
"Option Trading: Pricing and Volatility Strategies and Techniques" (Wiley),
which distils the Zakamouline asymptotic solution into two terms H0 and H1
(see equations below).

Validity envelope
-----------------
- Designed for a SINGLE European option hedged with the underlying futures.
- Applies proportional transaction costs only (no fixed-cost term here).
- Portfolio-level use (summing per-leg bands) is a HEURISTIC; the band
  interaction across correlated positions is not analytically modelled.
- The r=0 simplification (exp(-rT) → 1) is appropriate for futures hedging
  where cost-of-carry is embedded in the futures price.

Governance note
---------------
This module is a ZERO-ALPHA hedging/operations utility.  It minimises
transaction costs for a given options book; it does not generate directional
trading edge.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def _validate_band_inputs(
    F: float,
    T: float,
    sigma: float,
    risk_aversion: float,
    cost_rate: float,
) -> None:
    if T <= 0.0:
        raise ValueError(f"T must be > 0, got {T}")
    if sigma <= 0.0:
        raise ValueError(f"sigma must be > 0, got {sigma}")
    if risk_aversion <= 0.0:
        raise ValueError(f"risk_aversion must be > 0, got {risk_aversion}")
    if cost_rate < 0.0:
        raise ValueError(f"cost_rate must be >= 0, got {cost_rate}")
    if F <= 0.0:
        raise ValueError(f"F must be > 0, got {F}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def zakamouline_band(
    delta_bs: float,
    gamma_bs: float,
    F: float,
    T: float,
    sigma: float,
    *,
    risk_aversion: float,
    cost_rate: float,
    multiplier: float = 1.0,
) -> tuple[float, float]:
    """Zakamouline (2006) optimal delta-hedging band (lower, upper).

    Computes the no-trade band around the Black-Scholes delta for a single
    European option hedged with futures under proportional transaction costs.

    The band is delta_bs ± (H0 + H1) where:

        H0 = cost_rate / (risk_aversion * F * sigma² * T)

        H1 = 1.12 * cost_rate^0.31 * T^0.05
             * (exp(-r*T) / sigma)^0.25
             * (|gamma_bs| / risk_aversion)^0.5

    r=0 simplification for futures
    --------------------------------
    For options on futures, the underlying is a futures contract whose
    cost-of-carry is embedded in its price.  Setting r=0 is standard practice;
    exp(-r*T) → 1, so the (exp(-rT)/sigma)^0.25 term reduces to (1/sigma)^0.25.
    This is documented here rather than adding a redundant r parameter.

    Parameters
    ----------
    delta_bs : float
        Black-Scholes (Black-76) delta of the option position, in the same
        units as gamma_bs (typically futures-equivalent contracts).
    gamma_bs : float
        Black-Scholes (Black-76) gamma of the option position.
        Sign is ignored; |gamma_bs| is used.
    F : float
        Current futures price (> 0).
    T : float
        Time to expiry in years (> 0).
    sigma : float
        Implied volatility (annualised, > 0).
    risk_aversion : float
        Absolute risk-aversion coefficient (> 0).  Higher values → narrower band.
    cost_rate : float
        Proportional transaction cost as a fraction of F (>= 0).
        E.g. 0.0005 for 5 bps.  cost_rate=0 → zero band (H0=H1=0), which
        degenerates to the Whalley-Wilmott zero-cost limit: rehedge to exact delta.
    multiplier : float
        Contract multiplier (point value).  Included for dimensional consistency
        when delta_bs/gamma_bs are expressed per unit of notional; does not
        alter H0/H1 directly since the band is expressed in delta space.
        Default 1.0.

    Returns
    -------
    (lower, upper) : tuple[float, float]
        Lower and upper edges of the no-trade band around delta_bs.

    Raises
    ------
    ValueError
        If T <= 0, sigma <= 0, risk_aversion <= 0, cost_rate < 0, or F <= 0.
    """
    _validate_band_inputs(F, T, sigma, risk_aversion, cost_rate)

    if cost_rate == 0.0:
        return (delta_bs, delta_bs)

    # H0: static component from fixed-cost approximation term
    H0 = cost_rate / (risk_aversion * F * sigma * sigma * T)

    # H1: dynamic component (r=0 for futures: exp(-rT)=1 → (1/sigma)^0.25)
    # Zakamouline 2006 approximation coefficients: 1.12, 0.31, 0.05, 0.25, 0.5
    H1 = (
        1.12
        * cost_rate ** 0.31
        * T ** 0.05
        * (1.0 / sigma) ** 0.25
        * (abs(gamma_bs) / risk_aversion) ** 0.5
    )

    half_width = H0 + H1
    return (delta_bs - half_width, delta_bs + half_width)


def whalley_wilmott_band(
    gamma_bs: float,
    F: float,
    T: float,
    sigma: float,
    *,
    risk_aversion: float,
    cost_rate: float,
) -> float:
    """Whalley-Wilmott (1997) asymptotic half-width of the no-trade band.

    Reference
    ---------
    Whalley, A.E. & Wilmott, P. (1997). "An Asymptotic Analysis of an Optimal
    Hedging Model for Option Pricing with Transaction Costs." Mathematical
    Finance, 7(3), 307–324.

    Formula (standard asymptotic result):

        half_width = ( 1.5 * cost_rate * F * gamma_bs² / risk_aversion )^(1/3)

    This is the leading-order optimal band in the limit of small proportional
    costs.  It is provided here as a COMPARISON alternative to the Zakamouline
    approximation; the two formulas use different asymptotic regimes and
    parametrisations, so their numerical values should not be expected to order
    uniformly across parameter space.

    Parameters
    ----------
    gamma_bs : float
        Black-Scholes gamma.  Sign is ignored; gamma_bs² is used.
    F : float
        Current futures price (> 0).
    T : float
        Time to expiry in years (> 0).  Not used in the WW formula directly
        (time-dependence enters through gamma); accepted for interface symmetry.
    sigma : float
        Implied volatility (> 0).  Not used in the WW formula directly;
        accepted for interface symmetry.
    risk_aversion : float
        Absolute risk-aversion coefficient (> 0).
    cost_rate : float
        Proportional transaction cost as a fraction of F (>= 0).

    Returns
    -------
    half_width : float
        Half-width of the WW no-trade band.  Band is (delta - hw, delta + hw).

    Raises
    ------
    ValueError
        If T <= 0, sigma <= 0, risk_aversion <= 0, cost_rate < 0, or F <= 0.
    """
    _validate_band_inputs(F, T, sigma, risk_aversion, cost_rate)

    if cost_rate == 0.0:
        return 0.0

    inner = 1.5 * cost_rate * F * gamma_bs * gamma_bs / risk_aversion
    return inner ** (1.0 / 3.0)


# ---------------------------------------------------------------------------
# Stateful policy
# ---------------------------------------------------------------------------

class HedgeBandPolicy:
    """Stateful delta-hedging policy using the Zakamouline band.

    Tracks the current futures position and determines when and how much to
    rehedge based on whether the portfolio delta lies outside the no-trade band.

    Design principles
    -----------------
    Trade to NEAREST BAND EDGE — not to the mid (delta_bs).
    --------------------------------------------------------
    The entire purpose of the optimal band is to avoid over-trading: once the
    delta has drifted outside the band, it is optimal to trade only enough to
    re-enter the band (reach the nearest edge), not to restore the exact BS
    delta.  Trading to mid would eliminate the transaction-cost savings that
    the band is designed to capture.

    Zero-cost degeneracy
    --------------------
    When cost_rate=0, H0=H1=0, so the band collapses to a single point (lower
    == upper == delta_bs).  Any deviation from exact delta triggers a rehedge
    to exact delta — equivalent to the Whalley-Wilmott zero-cost limit of
    continuous hedging.  In practice cost_rate should be > 0 for futures.

    Integer contracts
    -----------------
    Futures trades are in whole contracts.  The trade quantity is rounded to
    the nearest integer.  If rounding would produce zero contracts, no trade is
    generated (the portfolio remains outside the band until the next tick).

    Parameters
    ----------
    cost_rate : float
        Proportional transaction cost (>= 0), passed through to band formulas.
    """

    def __init__(self, cost_rate: float) -> None:
        if cost_rate < 0.0:
            raise ValueError(f"cost_rate must be >= 0, got {cost_rate}")
        self.cost_rate = cost_rate
        self.current_futures_pos: int = 0

    def should_rehedge(
        self,
        portfolio_delta: float,
        band: tuple[float, float],
    ) -> int:
        """Determine the required futures trade quantity.

        Parameters
        ----------
        portfolio_delta : float
            Net delta of the entire portfolio (options + existing futures),
            expressed in futures-equivalent contract units.
        band : tuple[float, float]
            (lower, upper) band from zakamouline_band().

        Returns
        -------
        int
            Number of futures contracts to trade (positive = buy, negative =
            sell, 0 = no trade).  Trade is to the NEAREST band edge, rounded
            to the nearest integer contract.  The caller is responsible for
            executing the trade and calling update_position() afterwards.

        Notes
        -----
        When inside the band (lower <= portfolio_delta <= upper): returns 0.
        When outside the band, the target delta is the nearest edge:
            above upper → target = upper
            below lower → target = lower
        Trade qty = round(target - portfolio_delta), where portfolio_delta
        already includes the current futures position.
        """
        lower, upper = band
        if lower <= portfolio_delta <= upper:
            return 0

        if portfolio_delta > upper:
            target = upper
        else:
            target = lower

        qty = round(target - portfolio_delta)
        return int(qty)

    def update_position(self, trade_qty: int) -> None:
        """Record that a futures trade was executed.

        Parameters
        ----------
        trade_qty : int
            Number of contracts bought (positive) or sold (negative).
        """
        self.current_futures_pos += trade_qty
