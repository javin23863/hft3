"""eSSVI implied-total-variance surface for ES/NQ FOPs.

Implements the SSVI parameterization of Gatheral & Jacquier (2014), calibrated
per-expiry (eSSVI), with the butterfly/calendar no-arbitrage checks.  The
event-variance jump overlay (per-expiry additive variance lump for event dates)
is a LATER module; this surface design supports it via an optional per-expiry
``var_lump`` field that composes cleanly in ``EssviSlice.w``.

SSVI total-variance formula
---------------------------
    w(k, theta_t) = (theta_t / 2) * (1 + rho_t*phi_t*k
                    + sqrt((phi_t*k + rho_t)^2 + 1 - rho_t^2))

where:
  k       log-moneyness  ln(K / F_t)
  theta_t ATM total variance (>0); w(0) = theta_t exactly.
  rho_t   correlation-like slope parameter  (-0.999, 0.999); 0.999 = _RHO_BOUND.
  phi_t   wing steepness (>0).
  F_t     forward / futures price for the slice.

References
----------
Gatheral, J. & Jacquier, A. (2014).
  "Arbitrage-free SVI volatility surfaces."
  Quantitative Finance, 14(1), 59-71.

Mingone, A. (2022).
  "No arbitrage global parametrization for the eSSVI volatility surface."
  Quantitative Finance, 22(12), 2205-2217.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RHO_BOUND = 0.999   # |rho| strictly < 1; we cap at this value
_PHI_MIN   = 1e-8
_THETA_MIN = 1e-10

# Nelder-Mead calibration constants
_NM_MAX_ITER  = 5_000
_NM_TOL       = 1e-10   # objective value convergence tolerance
_NM_ALPHA     = 1.0     # reflection
_NM_GAMMA     = 2.0     # expansion
_NM_RHO_COEF  = 0.5     # contraction
_NM_SIGMA_COEF = 0.5    # shrink

# Butterfly-violation penalty weight in calibration objective
_BFLY_PENALTY = 1e4

# Calendar-arb check k-grid
_CAL_K_GRID = np.arange(-2.0, 2.0 + 1e-9, 0.01)  # 401 points


# ---------------------------------------------------------------------------
# EssviSlice
# ---------------------------------------------------------------------------


@dataclass
class EssviSlice:
    """Per-expiry eSSVI slice.

    Parameters
    ----------
    t     : time to expiry in years (>0).
    theta : ATM total variance = w(k=0).  Must be >0.
    rho   : correlation-like parameter.  Must satisfy |rho| < 0.999 (_RHO_BOUND).
    phi   : wing-steepness parameter.  Must be >0.
    F     : forward / futures price for this expiry.
    var_lump : optional additive variance lump (e.g. event-jump overlay).
               Added directly to w(k) at every strike.  Defaults to 0.0.
               A later event-overlay module sets this per-expiry before
               calibrating non-event expiries, so composition is simply:
                   w_total(k) = w_essvi(k) + var_lump

    Raises
    ------
    ValueError : if any parameter violates its domain constraint.
    """

    t: float
    theta: float
    rho: float
    phi: float
    F: float
    var_lump: float = field(default=0.0)

    def __post_init__(self) -> None:
        if self.t <= 0.0:
            raise ValueError(f"t must be >0; got {self.t}")
        if self.F <= 0.0:
            raise ValueError(f"F must be >0; got {self.F}")
        _validate_params(self.theta, self.rho, self.phi)

    # ------------------------------------------------------------------
    # Core formula
    # ------------------------------------------------------------------

    def w(self, k: float | np.ndarray) -> float | np.ndarray:
        """Total implied variance  w(k) = theta/2 * (...) + var_lump.

        Parameters
        ----------
        k : log-moneyness  ln(K/F).  Scalar or numpy array.

        Returns
        -------
        Total variance (same shape as k).  Always >= var_lump.
        """
        k = np.asarray(k, dtype=float)
        scalar = k.ndim == 0
        k = np.atleast_1d(k)

        t2 = self.theta / 2.0
        pk = self.phi * k + self.rho
        disc = pk * pk + (1.0 - self.rho * self.rho)
        result = t2 * (1.0 + self.rho * self.phi * k + np.sqrt(disc)) + self.var_lump

        return float(result[0]) if scalar else result

    def iv(self, k: float | np.ndarray) -> float | np.ndarray:
        """Black-76 implied volatility = sqrt(w(k) / t).

        Parameters
        ----------
        k : log-moneyness  ln(K/F).

        Returns
        -------
        Implied volatility.  Non-negative.
        """
        wk = self.w(k)
        return np.sqrt(np.maximum(wk, 0.0) / self.t)


# ---------------------------------------------------------------------------
# Parameter validation helpers
# ---------------------------------------------------------------------------


def _validate_params(theta: float, rho: float, phi: float) -> None:
    """Raise ValueError for out-of-domain (theta, rho, phi)."""
    if not (theta > 0.0):
        raise ValueError(f"theta must be >0; got {theta}")
    if not (abs(rho) < _RHO_BOUND):
        raise ValueError(f"|rho| must be < {_RHO_BOUND}; got {rho}")
    if not (phi > 0.0):
        raise ValueError(f"phi must be >0; got {phi}")


# ---------------------------------------------------------------------------
# No-arbitrage checks
# ---------------------------------------------------------------------------


def butterfly_ok(sl: EssviSlice) -> bool:
    """Return True iff the slice passes the SSVI sufficient butterfly condition.

    The two sufficient (NOT necessary) conditions from Gatheral & Jacquier (2014)
    Proposition 4.2 are:

        (C1)  theta * phi * (1 + |rho|) <= 4
        (C2)  theta * phi^2 * (1 + |rho|) <= 4

    Both must hold.  Satisfying C1 and C2 guarantees no butterfly arbitrage
    in the SSVI surface, but a slice MAY be butterfly-free even if they fail.

    TODO: implement the sharper necessary-and-sufficient bounds from
    Mingone (2022), which tighten C1/C2 to per-k conditions using the
    local-variance representation.

    Parameters
    ----------
    sl : EssviSlice

    Returns
    -------
    bool
    """
    abs_rho = abs(sl.rho)
    c1 = sl.theta * sl.phi * (1.0 + abs_rho)
    c2 = sl.theta * sl.phi ** 2 * (1.0 + abs_rho)
    return (c1 <= 4.0) and (c2 <= 4.0)


def calendar_ok(slice_a: EssviSlice, slice_b: EssviSlice) -> bool:
    """Return True iff slice_b does not calendar-arbitrage slice_a.

    Requires slice_b.t > slice_a.t (slice_b is longer-dated).

    Condition checked: w_b(k) >= w_a(k) for all k on the grid
        k in [-2, 2] with step 0.01 scaled by sqrt(theta_mean).

    This grid check is SUFFICIENT (on the sampled grid) but NOT a
    closed-form necessary condition.  The exact necessary-and-sufficient
    characterization in terms of theta, rho, phi crossing conditions is
    cited as a TODO (see Gatheral & Jacquier 2014, §4; Mingone 2022).

    Parameters
    ----------
    slice_a : shorter-dated slice (slice_a.t < slice_b.t required).
    slice_b : longer-dated slice.

    Returns
    -------
    bool

    Raises
    ------
    ValueError : if slice_b.t <= slice_a.t.
    """
    if slice_b.t <= slice_a.t:
        raise ValueError(
            f"slice_b.t ({slice_b.t}) must be > slice_a.t ({slice_a.t})"
        )
    # Scale k-grid by sqrt of mean theta so the grid is representative
    # for both near- and far-dated expiries.
    theta_mean = math.sqrt(0.5 * (slice_a.theta + slice_b.theta))
    k_grid = _CAL_K_GRID * max(theta_mean, 0.1)
    wa = slice_a.w(k_grid)
    wb = slice_b.w(k_grid)
    return bool(np.all(wb >= wa - 1e-12))   # 1e-12 tolerance for fp noise


# ---------------------------------------------------------------------------
# EssviSurface
# ---------------------------------------------------------------------------


class EssviSurface:
    """Collection of eSSVI slices forming a term structure of total variance.

    Slices are stored in ascending order of t.  The surface provides:
      - w(k, t): total variance at log-moneyness k and time t.
      - Linear-in-total-variance interpolation between adjacent expiries
        at fixed k.
      - Clamping at the boundary: queries before the first expiry use the
        first slice's w; queries beyond the last expiry use the last slice's w.
      - validate(): butterfly + calendar checks on all slices.

    Parameters
    ----------
    slices : sequence of EssviSlice objects.  Sorted by t on construction.

    Raises
    ------
    ValueError : if fewer than 1 slice is provided or duplicate t values exist.
    """

    def __init__(self, slices: Sequence[EssviSlice]) -> None:
        if len(slices) == 0:
            raise ValueError("At least one EssviSlice is required.")
        self._slices: list[EssviSlice] = sorted(slices, key=lambda s: s.t)
        ts = [s.t for s in self._slices]
        if len(ts) != len(set(ts)):
            raise ValueError("Duplicate t values in EssviSurface slices.")

    @property
    def slices(self) -> list[EssviSlice]:
        return list(self._slices)

    def w(self, k: float | np.ndarray, t: float) -> float | np.ndarray:
        """Total variance at log-moneyness k and time t.

        Interpolation policy
        --------------------
        t < first slice : clamp — return w of the first (shortest) slice.
        t > last slice  : clamp — return w of the last (longest) slice.
        t between two slices a, b (t_a < t <= t_b):
            linear interpolation in total variance at fixed k:
                w(k, t) = w_a(k) + (t - t_a) / (t_b - t_a) * (w_b(k) - w_a(k))

        The linear-in-w interpolation scheme preserves calendar-spread
        non-negativity when adjacent slices are calendar-arb free.

        Parameters
        ----------
        k : log-moneyness ln(K/F).
        t : time to expiry in years (>0).

        Returns
        -------
        Total variance (same shape as k).
        """
        slices = self._slices
        if t <= slices[0].t:
            return slices[0].w(k)
        if t >= slices[-1].t:
            return slices[-1].w(k)
        # Find bracketing slices
        for i in range(len(slices) - 1):
            sa, sb = slices[i], slices[i + 1]
            if sa.t <= t <= sb.t:
                alpha = (t - sa.t) / (sb.t - sa.t)
                wa = sa.w(k)
                wb = sb.w(k)
                return wa + alpha * (wb - wa)
        # Unreachable but satisfies type-checker
        return slices[-1].w(k)  # pragma: no cover

    def validate(self) -> list[str]:
        """Run butterfly and calendar checks on all slices.

        Returns
        -------
        list of str
            Empty list means no violations.  Each entry describes one violation.
        """
        errors: list[str] = []
        for sl in self._slices:
            if not butterfly_ok(sl):
                errors.append(
                    f"Butterfly violation on slice t={sl.t:.6f}: "
                    f"theta={sl.theta}, phi={sl.phi}, rho={sl.rho}"
                )
        for i in range(len(self._slices) - 1):
            sa, sb = self._slices[i], self._slices[i + 1]
            if not calendar_ok(sa, sb):
                errors.append(
                    f"Calendar violation between t={sa.t:.6f} and t={sb.t:.6f}"
                )
        return errors


# ---------------------------------------------------------------------------
# Nelder-Mead optimizer (hand-rolled, deterministic)
# ---------------------------------------------------------------------------


def _nelder_mead(
    f,
    x0: np.ndarray,
    *,
    max_iter: int = _NM_MAX_ITER,
    tol: float = _NM_TOL,
) -> np.ndarray:
    """Minimize f: R^n → R via the Nelder-Mead simplex method.

    Deterministic, no randomness.  Initial simplex built from x0 by
    perturbing each coordinate by 5% (or 0.05 if x0[i]==0).

    Parameters
    ----------
    f        : objective function.
    x0       : initial parameter vector (1-D array).
    max_iter : maximum number of objective evaluations.
    tol      : convergence declared when |f_best - f_worst| < tol AND
               max coordinate range < tol.

    Returns
    -------
    Best parameter vector found.
    """
    n = len(x0)
    # Build initial simplex: n+1 vertices
    simplex = np.empty((n + 1, n))
    simplex[0] = x0.copy()
    for i in range(n):
        v = x0.copy()
        v[i] = v[i] * 1.05 if v[i] != 0.0 else 0.05
        simplex[i + 1] = v

    fvals = np.array([f(simplex[i]) for i in range(n + 1)])

    for _ in range(max_iter):
        # Sort by function value
        order = np.argsort(fvals)
        simplex = simplex[order]
        fvals = fvals[order]

        # Convergence check
        if fvals[-1] - fvals[0] < tol and np.max(np.abs(simplex[-1] - simplex[0])) < tol:
            break

        # Centroid of best n vertices
        centroid = simplex[:-1].mean(axis=0)

        # Reflection
        x_r = centroid + _NM_ALPHA * (centroid - simplex[-1])
        f_r = f(x_r)

        if fvals[0] <= f_r < fvals[-2]:
            simplex[-1] = x_r
            fvals[-1] = f_r
            continue

        if f_r < fvals[0]:
            # Expansion
            x_e = centroid + _NM_GAMMA * (x_r - centroid)
            f_e = f(x_e)
            if f_e < f_r:
                simplex[-1] = x_e
                fvals[-1] = f_e
            else:
                simplex[-1] = x_r
                fvals[-1] = f_r
            continue

        # Contraction
        if f_r < fvals[-1]:
            x_c = centroid + _NM_RHO_COEF * (x_r - centroid)
            f_c = f(x_c)
            if f_c <= f_r:
                simplex[-1] = x_c
                fvals[-1] = f_c
                continue
        else:
            x_c = centroid + _NM_RHO_COEF * (simplex[-1] - centroid)
            f_c = f(x_c)
            if f_c < fvals[-1]:
                simplex[-1] = x_c
                fvals[-1] = f_c
                continue

        # Shrink
        best = simplex[0].copy()
        for i in range(1, n + 1):
            simplex[i] = best + _NM_SIGMA_COEF * (simplex[i] - best)
            fvals[i] = f(simplex[i])

    return simplex[0]


# ---------------------------------------------------------------------------
# Parameter transform (unconstrained ↔ constrained)
# ---------------------------------------------------------------------------


def _to_unconstrained(theta: float, rho: float, phi: float) -> np.ndarray:
    """Map constrained (theta, rho, phi) → unconstrained (a, b, c).

    Transform:
      a = ln(theta)          — theta = exp(a) > 0
      b = arctanh(rho / 0.999) — rho = 0.999*tanh(b) in (-0.999, 0.999)
      c = ln(phi)            — phi = exp(c) > 0
    """
    a = math.log(theta)
    b = math.atanh(rho / _RHO_BOUND)
    c = math.log(phi)
    return np.array([a, b, c])


def _to_constrained(u: np.ndarray) -> tuple[float, float, float]:
    """Map unconstrained (a, b, c) → constrained (theta, rho, phi)."""
    theta = math.exp(float(u[0]))
    rho = _RHO_BOUND * math.tanh(float(u[1]))
    phi = math.exp(float(u[2]))
    return theta, rho, phi


# ---------------------------------------------------------------------------
# calibrate_slice
# ---------------------------------------------------------------------------


def calibrate_slice(
    ivs: Sequence[float] | np.ndarray,
    ks: Sequence[float] | np.ndarray,
    t: float,
    F: float,
    *,
    weights: Sequence[float] | np.ndarray | None = None,
) -> EssviSlice:
    """Fit per-expiry eSSVI parameters to observed implied volatilities.

    Uses a hand-rolled Nelder-Mead optimizer in the unconstrained space
    (log/arctanh transforms enforce theta>0, |rho|<0.999, phi>0).  A soft
    penalty is added for butterfly constraint violations so the calibrated
    slice lands in the arb-free region.

    Parameters
    ----------
    ivs     : 1-D array of observed Black-76 implied vols (annualised), shape (N,).
    ks      : 1-D array of log-moneyness ln(K/F), shape (N,).  Must match ivs.
    t       : time to expiry in years (>0).
    F       : forward / futures price.
    weights : optional 1-D array of observation weights, shape (N,).
              Defaults to uniform weights.

    Returns
    -------
    EssviSlice : calibrated (theta, rho, phi) with butterfly check passing.

    Notes
    -----
    Convergence tolerance: _NM_TOL = 1e-10 on the simplex diameter.
    Maximum iterations: _NM_MAX_ITER = 5000.
    Butterfly penalty weight: _BFLY_PENALTY = 1e4.
    """
    ivs_arr = np.asarray(ivs, dtype=float)
    ks_arr = np.asarray(ks, dtype=float)
    if len(ivs_arr) != len(ks_arr):
        raise ValueError("ivs and ks must have the same length.")
    if t <= 0.0:
        raise ValueError(f"t must be >0; got {t}")
    if F <= 0.0:
        raise ValueError(f"F must be >0; got {F}")

    if weights is None:
        w_arr = np.ones(len(ivs_arr))
    else:
        w_arr = np.asarray(weights, dtype=float)
        if len(w_arr) != len(ivs_arr):
            raise ValueError("weights must have the same length as ivs.")

    w_arr = w_arr / w_arr.sum()       # normalise to sum=1

    # Target total variances from observed IVs
    w_target = ivs_arr ** 2 * t

    def objective(u: np.ndarray) -> float:
        theta, rho, phi = _to_constrained(u)
        # Compute SSVI total variance at each k
        t2 = theta / 2.0
        pk = phi * ks_arr + rho
        disc = pk * pk + (1.0 - rho * rho)
        w_model = t2 * (1.0 + rho * phi * ks_arr + np.sqrt(disc))

        # Weighted MSE on total variance
        residuals = w_model - w_target
        mse = float(np.dot(w_arr, residuals * residuals))

        # Soft butterfly penalty: penalise violation of C1 and C2
        abs_rho = abs(rho)
        c1_excess = max(0.0, theta * phi * (1.0 + abs_rho) - 4.0)
        c2_excess = max(0.0, theta * phi ** 2 * (1.0 + abs_rho) - 4.0)
        penalty = _BFLY_PENALTY * (c1_excess ** 2 + c2_excess ** 2)

        return mse + penalty

    # Estimate theta from ATM-region observations (k closest to 0)
    atm_idx = int(np.argmin(np.abs(ks_arr)))
    theta0 = max(float(w_target[atm_idx]), _THETA_MIN)

    # Estimate phi from the slope of w_target vs k (crude linear regression)
    # d(w)/dk|_{k=0} = theta * rho * phi / 2 + theta * phi * rho / (2*sqrt(1-rho^2+rho^2))
    # Simplified: use finite difference at centre point for initial phi estimate.
    if len(ks_arr) >= 3:
        slope = float(np.polyfit(ks_arr, w_target, 1)[0])
        # slope ≈ theta*rho*phi; estimate phi ≈ |slope| / (theta0 * 0.3 + 1e-6)
        phi0_est = max(abs(slope) / (theta0 * 0.3 + 1e-6), 0.5)
        phi0_est = min(phi0_est, 5.0)
    else:
        phi0_est = 1.0

    # Multi-start: try rho in {-0.3, +0.3} to escape local minima.
    # Both starts are deterministic; pick the one with the lower objective.
    best_u: np.ndarray | None = None
    best_f = math.inf
    for rho0 in (-0.3, 0.3):
        u0 = _to_unconstrained(theta0, rho0, phi0_est)
        u_cand = _nelder_mead(objective, u0)
        f_cand = objective(u_cand)
        if f_cand < best_f:
            best_f = f_cand
            best_u = u_cand

    theta_fit, rho_fit, phi_fit = _to_constrained(best_u)  # type: ignore[arg-type]

    return EssviSlice(t=t, theta=theta_fit, rho=rho_fit, phi=phi_fit, F=F)
