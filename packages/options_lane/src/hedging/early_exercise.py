"""Early-exercise hygiene monitor for American futures options (WS-3.5).

Defensive both directions:
- Long positions: detect when immediate exercise is optimal (forfeit of
  remaining time value is justified by carry/margin-release benefit).
- Short positions: anticipate counterparty assignment by monitoring how
  little time value remains in the option.

For futures options (Black-76, cost-of-carry b=0) early exercise is only
rational when r > 0: the option holder captures cash (intrinsic) whose
carry exceeds the remaining time value.  At r=0 the EEP is exactly zero
and early exercise is never optimal.

Engine: options_pricing.src.american.lr_tree_price (LR binomial tree, CRR
backward induction, odd steps).  The European baseline is Black-76 exact.

STATUS: ZERO-ALPHA hedging/ops tool.  No trading edge is claimed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from options_pricing.src.black76 import price as b76_price
from options_pricing.src.american import lr_tree_price

# ---------------------------------------------------------------------------
# Try importing Position from stress_grid; define a local Protocol fallback
# so this module never creates a circular import.
# ---------------------------------------------------------------------------

try:
    from options_risk.src.stress_grid import Position as _StressPosition
    _HAVE_STRESS_POSITION = True
except ImportError:
    _HAVE_STRESS_POSITION = False
    _StressPosition = None  # type: ignore[assignment]


@runtime_checkable
class PositionLike(Protocol):
    """Minimal protocol satisfied by options_risk.stress_grid.Position.

    Modules that cannot import stress_grid.Position (e.g. due to optional
    dependency) can supply any object matching this interface instead.
    """

    kind: str            # 'option' | 'future'
    qty: float           # signed; negative = short
    multiplier: float
    T: float             # years to expiry
    style: str           # 'E' | 'A'
    is_call: bool | None
    K: float | None      # strike
    sigma: float | None  # implied vol


# ---------------------------------------------------------------------------
# ExerciseAdvice dataclass
# ---------------------------------------------------------------------------

_EPS = 1e-8   # tolerance for optimal_now comparison


@dataclass(frozen=True)
class ExerciseAdvice:
    """Output of exercise_signal() for one option.

    Fields
    ------
    european_value : Black-76 European price (exact analytic).
    american_value : LR-tree American price.
    eep : early-exercise premium = american_value - european_value, >= 0.
    intrinsic : max(sign*(F-K), 0), undiscounted.
    time_value_remaining : american_value - intrinsic.
        Positive when holding has option value beyond intrinsic.
        When this approaches zero the tree exercises at the root.
    optimal_now : True when immediate exercise is the optimal policy.
        Condition: american_value <= intrinsic + _EPS.
        Rationale: the LR tree already encodes the early-exercise decision
        via backward induction.  When the root's American value equals
        intrinsic, the tree chose exercise at the root — meaning no
        holding value remains.  Equivalently, time_value_remaining < eps.
    """

    european_value: float
    american_value: float
    eep: float
    intrinsic: float
    time_value_remaining: float
    optimal_now: bool


# ---------------------------------------------------------------------------
# exercise_signal
# ---------------------------------------------------------------------------

def exercise_signal(
    F: float,
    K: float,
    T: float,
    sigma: float,
    r: float,
    is_call: bool,
    *,
    steps: int = 201,
) -> ExerciseAdvice:
    """Compute early-exercise signal for a long American futures option.

    Parameters
    ----------
    F : futures price (> 0).
    K : strike price (> 0).
    T : time to expiry in years (>= 0).
    sigma : implied volatility (> 0).
    r : risk-free rate (continuous compounding).
        At r=0 the EEP is exactly zero; optimal_now will be False for
        any option with time value remaining.
    is_call : True for call, False for put.
    steps : LR-tree steps (forced odd internally by lr_tree_price).
        Default 201 gives good accuracy for production hygiene checks.
        Reduce for batch scans where speed matters.

    Returns
    -------
    ExerciseAdvice dataclass.

    Edge cases
    ----------
    T <= 0 : option expired; intrinsic returned, optimal_now=True if ITM.
    r = 0  : EEP = 0 exactly; optimal_now = False for any T > 0.

    Notes
    -----
    optimal_now derivation: the LR tree performs backward induction with
    early-exercise check at every node.  At the root (node 0), the tree
    returns max(continuation_value, intrinsic).  When the root value
    equals intrinsic (within floating-point noise), the tree chose
    immediate exercise — meaning the continuation value did not exceed
    intrinsic.  Testing american_value <= intrinsic + eps is the correct
    and simplest implementation of this check.
    """
    if F <= 0.0 or K <= 0.0:
        raise ValueError(f"F and K must be positive, got F={F}, K={K}")
    if sigma <= 0.0:
        raise ValueError(f"sigma must be positive, got sigma={sigma}")

    sign = 1.0 if is_call else -1.0
    intrinsic = max(sign * (F - K), 0.0)

    if T <= 0.0:
        is_itm = sign * (F - K) > 0.0
        return ExerciseAdvice(
            european_value=intrinsic,
            american_value=intrinsic,
            eep=0.0,
            intrinsic=intrinsic,
            time_value_remaining=0.0,
            optimal_now=is_itm,
        )

    european_value = float(b76_price(F, K, T, sigma, r, is_call))
    american_value = lr_tree_price(F, K, T, sigma, r, is_call, steps=steps, american=True)
    eep = max(american_value - european_value, 0.0)
    time_value_remaining = american_value - intrinsic
    # When american_value == intrinsic (within eps), the tree chose exercise at root.
    # r=0 guard: at zero rates the EEP is exactly zero for futures options — no carry
    # benefit from releasing margin — so exercise is never strictly optimal regardless
    # of moneyness.  Indifference is mapped to "hold" (optimal_now=False).
    if r <= 0.0:
        optimal_now = False
    else:
        optimal_now = american_value <= intrinsic + _EPS

    return ExerciseAdvice(
        european_value=european_value,
        american_value=american_value,
        eep=eep,
        intrinsic=intrinsic,
        time_value_remaining=time_value_remaining,
        optimal_now=optimal_now,
    )


# ---------------------------------------------------------------------------
# assignment_risk
# ---------------------------------------------------------------------------

_RISK_HIGH = "high"
_RISK_MEDIUM = "medium"
_RISK_LOW = "low"

# Operational default thresholds (starting points only — not calibrated truth).
# CME ES FOP tick = 0.25 index points = $12.50 per contract (multiplier 50).
# A threshold of 2 ticks ($25) is a conservative starting point; operators
# should back-test this against historical exercise frequency before relying on it.
_DEFAULT_THRESHOLD_TICKS = 2
_DEFAULT_TICK_SIZE = 0.25      # ES FOP tick size (index points)
_DEFAULT_MULTIPLIER = 50.0     # ES contract multiplier


def assignment_risk(
    F: float,
    K: float,
    T: float,
    sigma: float,
    r: float,
    is_call: bool,
    *,
    threshold_time_value_ticks: float = _DEFAULT_THRESHOLD_TICKS,
    tick_size: float = _DEFAULT_TICK_SIZE,
    multiplier: float = _DEFAULT_MULTIPLIER,
    steps: int = 201,
) -> str:
    """Assess assignment risk for a short American futures option position.

    The counterparty (long) will exercise when it is optimal for them.
    A short holder should expect assignment when the time value remaining
    in the option is very low — the counterparty has little to lose by
    exercising now.

    Parameters
    ----------
    F, K, T, sigma, r, is_call : option parameters (same as exercise_signal).
    threshold_time_value_ticks : dollar threshold expressed in ticks.
        If time_value_remaining < threshold * tick_size: risk = HIGH.
        If time_value_remaining < 3 * threshold * tick_size: risk = MEDIUM.
        Default: 2 ticks (OPERATIONAL STARTING POINT, not calibrated truth).
    tick_size : price of one tick (index points; ES = 0.25).
        Default: 0.25 (ES FOP).
    multiplier : contract multiplier (dollars per point; ES = 50).
        Default: 50.0.
    steps : LR-tree steps for exercise_signal. Default 201.

    Returns
    -------
    'high'   : time_value_remaining < threshold_time_value_ticks * tick_size.
               Counterparty exercise imminent.
    'medium' : time_value_remaining < 3 * threshold_time_value_ticks * tick_size.
               Elevated watch; pre-plan futures hedge.
    'low'    : otherwise.

    Notes
    -----
    Thresholds are in price points (same units as option price / intrinsic).
    The multiplier parameter is reserved for future dollar-value extensions
    but is not used in the current threshold comparison (thresholds are in
    price points, not dollars) — keeping them consistent with the option
    price units returned by the pricing engine.
    Defaults are OPERATIONAL STARTING POINTS; operators must review against
    historical exercise data before relying on them in production.
    """
    advice = exercise_signal(F, K, T, sigma, r, is_call, steps=steps)
    tv = advice.time_value_remaining
    threshold_pts = threshold_time_value_ticks * tick_size
    if tv < threshold_pts:
        return _RISK_HIGH
    if tv < 3.0 * threshold_pts:
        return _RISK_MEDIUM
    return _RISK_LOW


# ---------------------------------------------------------------------------
# Advisory dataclass (returned by scan_chain)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ChainAdvisory:
    """Advisory record for one position in the book.

    Fields
    ------
    position_index : index into the input positions list (for traceability).
    side : 'long' when qty > 0, 'short' when qty < 0.
    advice : ExerciseAdvice from exercise_signal().
    assignment_risk_level : 'high'|'medium'|'low' (always computed; meaningful
        for short positions, informational for longs).
    action : human-readable advisory string.
        Long + optimal_now  -> "EXERCISE: contact FCM before cutoff"
        Short + high risk   -> "ASSIGNMENT RISK HIGH: pre-plan futures hedge"
        Short + medium risk -> "ASSIGNMENT RISK MEDIUM: monitor closely"
        Otherwise           -> "HOLD"
    """

    position_index: int
    side: str
    advice: ExerciseAdvice
    assignment_risk_level: str
    action: str


def scan_chain(
    positions: list,
    marks: dict[str, float],
    *,
    r: float = 0.0,
    threshold_time_value_ticks: float = _DEFAULT_THRESHOLD_TICKS,
    tick_size: float = _DEFAULT_TICK_SIZE,
    multiplier: float = _DEFAULT_MULTIPLIER,
    steps: int = 201,
) -> list[ChainAdvisory]:
    """Scan a position book and return exercise/assignment advisories.

    Iterates over all option positions (kind='option', style='A') and
    produces a ChainAdvisory for each.  Non-option and European positions
    are skipped silently.

    Parameters
    ----------
    positions : list of PositionLike objects (or options_risk.Position).
        Each must have: kind, qty, style, is_call, K, sigma, T, multiplier.
        Non-option or European positions are skipped.
    marks : dict mapping a string key to a futures price mark.
        Convention: marks['F'] supplies the current futures price used for
        all positions.  If marks contains position-level keys they are
        ignored; a single 'F' entry is expected.
        If 'F' is not present, scan_chain raises KeyError.
    r : risk-free rate (scalar; applied uniformly across the book).
        Default 0.0.  Pass the current overnight rate for meaningful EEP.
    threshold_time_value_ticks : assignment_risk threshold (see assignment_risk).
    tick_size : tick size in price points (see assignment_risk).
    multiplier : contract multiplier (reserved; see assignment_risk).
    steps : LR-tree steps. Default 201.

    Returns
    -------
    List of ChainAdvisory, one per American option position.  Order matches
    the input list (filtered to American options).

    Notes
    -----
    The marks dict convention (single 'F' key) keeps scan_chain decoupled
    from any specific key-encoding scheme.  Callers with per-instrument
    marks should pre-select the relevant futures price and pass it as 'F'.
    """
    F = marks["F"]
    advisories: list[ChainAdvisory] = []

    for idx, pos in enumerate(positions):
        # Skip non-options and non-American options
        if getattr(pos, "kind", None) != "option":
            continue
        if getattr(pos, "style", None) != "A":
            continue

        is_call = pos.is_call
        K = pos.K
        sigma = pos.sigma
        T = pos.T

        if is_call is None or K is None or sigma is None:
            continue

        advice = exercise_signal(F, K, T, sigma, r, is_call, steps=steps)
        risk_level = assignment_risk(
            F, K, T, sigma, r, is_call,
            threshold_time_value_ticks=threshold_time_value_ticks,
            tick_size=tick_size,
            multiplier=multiplier,
            steps=steps,
        )

        qty = getattr(pos, "qty", 0.0)
        # Skip flat positions: qty=0 has no exposure to monitor.
        if qty == 0:
            continue
        side = "long" if qty > 0 else "short"

        if side == "long" and advice.optimal_now:
            action = "EXERCISE: contact FCM before cutoff"
        elif side == "short" and risk_level == _RISK_HIGH:
            action = "ASSIGNMENT RISK HIGH: pre-plan futures hedge"
        elif side == "short" and risk_level == _RISK_MEDIUM:
            action = "ASSIGNMENT RISK MEDIUM: monitor closely"
        else:
            action = "HOLD"

        advisories.append(ChainAdvisory(
            position_index=idx,
            side=side,
            advice=advice,
            assignment_risk_level=risk_level,
            action=action,
        ))

    return advisories
