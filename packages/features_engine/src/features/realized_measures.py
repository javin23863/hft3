"""Realized-measures library from L3 MBO trade data.

All functions operate on plain numpy arrays (ts_ns, price) extracted from NPZ
structured arrays (fields: ev/local_ts/px/qty, where ev & TRADE_EVENT != 0
identifies trades).  No I/O, no side effects.

References
----------
Andersen, Bollerslev, Diebold & Labys (2001) — realized variance via log-returns.
Zhang, Mykland & Aït-Sahalia (2005) — subsampled/averaged RV.
Barndorff-Nielsen & Shephard (2004) — realized semivariances.
Amaya, Christoffersen, Jacobs & Vasquez (2015) — intraday realized skewness/kurtosis.
Andersen, Bollerslev, Diebold & Labys (2000) — realized quarticity.
"""
from __future__ import annotations

import numpy as np

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_NAN = float("nan")


def _last_price_grid(
    ts_ns: np.ndarray,
    px: np.ndarray,
    grid_ns: int,
) -> np.ndarray:
    """Forward-fill last trade price onto a regular nanosecond grid.

    Returns the grid prices; the grid spans [ts_ns[0], ts_ns[-1]] in steps of
    *grid_ns*.  No lookahead: each bar uses the last trade *before or at* that
    bar boundary.

    Parameters
    ----------
    ts_ns:
        Sorted trade timestamps in nanoseconds.
    px:
        Trade prices corresponding to *ts_ns*.
    grid_ns:
        Grid step in nanoseconds (e.g. 1_000_000_000 for 1 s).

    Returns
    -------
    np.ndarray of shape (n_bars,).  Returns empty array when fewer than 2
    valid inputs are provided.
    """
    ts_ns = np.asarray(ts_ns, dtype=np.int64)
    px = np.asarray(px, dtype=np.float64)
    if ts_ns.size < 2:
        return np.empty(0, dtype=np.float64)

    t0 = ts_ns[0]
    t1 = ts_ns[-1]
    if t1 <= t0 or grid_ns <= 0:
        return np.empty(0, dtype=np.float64)

    # Grid bar-end timestamps
    n_bars = int((t1 - t0) // grid_ns) + 1
    bar_ends = t0 + np.arange(n_bars, dtype=np.int64) * grid_ns

    # For each bar_end find the index of the last trade <= bar_end
    # np.searchsorted(ts_ns, bar_end, side='right') - 1
    indices = np.searchsorted(ts_ns, bar_ends, side="right") - 1

    # Bars before the first trade have no price; forward-fill
    # indices == -1 means no trade yet for that bar
    grid_px = np.full(n_bars, _NAN, dtype=np.float64)
    valid = indices >= 0
    grid_px[valid] = px[indices[valid]]

    # Forward-fill NaN entries (bars that had no trade before them)
    for i in range(1, n_bars):
        if np.isnan(grid_px[i]) and not np.isnan(grid_px[i - 1]):
            grid_px[i] = grid_px[i - 1]

    return grid_px


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def log_returns(
    ts_ns: np.ndarray,
    px: np.ndarray,
    grid_ns: int,
) -> np.ndarray:
    """Resample last trade price to a fixed grid and return log-returns.

    Andersen-Bollerslev (2001) — no-lookahead forward-fill resampling.

    Parameters
    ----------
    ts_ns:
        Trade timestamps in nanoseconds (monotone non-decreasing).
    px:
        Trade prices.
    grid_ns:
        Grid step in nanoseconds (e.g. 1_000_000_000 for 1 s).

    Returns
    -------
    np.ndarray of log-returns (length = n_bars - 1).  Returns empty array
    when fewer than 2 valid prices exist on the grid.
    """
    grid_px = _last_price_grid(ts_ns, px, grid_ns)
    if grid_px.size < 2:
        return np.empty(0, dtype=np.float64)

    # Drop leading NaNs (no price observed yet)
    first_valid = 0
    while first_valid < len(grid_px) and np.isnan(grid_px[first_valid]):
        first_valid += 1
    grid_px = grid_px[first_valid:]
    if grid_px.size < 2:
        return np.empty(0, dtype=np.float64)

    with np.errstate(divide="ignore", invalid="ignore"):
        rets = np.log(grid_px[1:] / grid_px[:-1])
    return rets


def realized_variance(returns: np.ndarray) -> float:
    """Sum of squared log-returns — standard RV estimator.

    Andersen, Bollerslev, Diebold & Labys (2001).

    Parameters
    ----------
    returns:
        Array of log-returns from a single resampled series.

    Returns
    -------
    float — sum(r^2).  Returns nan when fewer than 2 returns.
    """
    r = np.asarray(returns, dtype=np.float64)
    if r.size < 2:
        return _NAN
    return float(np.sum(r ** 2))


def subsampled_rv(
    ts_ns: np.ndarray,
    px: np.ndarray,
    grid_ns: int,
    n_offsets: int = 5,
) -> float:
    """Subsampled/averaged realized variance (Zhang-Mykland-Aït-Sahalia, 2005).

    Averages RV computed over *n_offsets* equally-spaced sub-grids of the base
    grid.  Reduces microstructure noise relative to a single-grid estimator.

    Parameters
    ----------
    ts_ns:
        Trade timestamps in nanoseconds.
    px:
        Trade prices.
    grid_ns:
        Base grid step in nanoseconds.
    n_offsets:
        Number of sub-grids to average (default 5).

    Returns
    -------
    float — averaged RV across sub-grids.  Returns nan when insufficient data.
    """
    ts_ns = np.asarray(ts_ns, dtype=np.int64)
    px = np.asarray(px, dtype=np.float64)
    if ts_ns.size < 2 or n_offsets < 1:
        return _NAN

    step = max(1, grid_ns // n_offsets)
    rv_list: list[float] = []
    for k in range(n_offsets):
        offset = k * step
        # Shift timestamps by -offset so the grid starts from t0+offset
        ts_shifted = ts_ns - offset
        valid = ts_shifted >= ts_ns[0]
        if np.sum(valid) < 2:
            continue
        rets = log_returns(ts_shifted[valid], px[valid], grid_ns)
        rv = realized_variance(rets)
        if not np.isnan(rv):
            rv_list.append(rv)

    if not rv_list:
        return _NAN
    return float(np.mean(rv_list))


def realized_quarticity(returns: np.ndarray, grid_per_day: float) -> float:
    """Scaled sum of fourth-power log-returns (Andersen et al., 2000).

    RQ = (N/3) * sum(r^4) where N = grid_per_day (e.g. 23400 for 1-s bars in
    a 6.5-hour trading day).

    Parameters
    ----------
    returns:
        Array of log-returns.
    grid_per_day:
        Number of intervals per trading day for the chosen grid.

    Returns
    -------
    float.  Returns nan when fewer than 2 returns.
    """
    r = np.asarray(returns, dtype=np.float64)
    if r.size < 2:
        return _NAN
    n = float(grid_per_day)
    return float((n / 3.0) * np.sum(r ** 4))


def realized_semivariances(returns: np.ndarray) -> tuple[float, float]:
    """Decompose RV into downside (RS-) and upside (RS+) semivariances.

    Barndorff-Nielsen & Shephard (2004): RS- = sum(r^2 | r<0),
    RS+ = sum(r^2 | r>0).

    Parameters
    ----------
    returns:
        Array of log-returns.

    Returns
    -------
    (RS_minus, RS_plus) — each is nan when fewer than 2 returns.
    """
    r = np.asarray(returns, dtype=np.float64)
    if r.size < 2:
        return (_NAN, _NAN)
    rs_minus = float(np.sum(r[r < 0] ** 2))
    rs_plus = float(np.sum(r[r > 0] ** 2))
    return (rs_minus, rs_plus)


def realized_skewness(returns: np.ndarray, grid_per_day: float) -> float:
    """Intraday realized skewness (Amaya-Christoffersen-Jacobs-Vasquez, 2015).

    RSkew = sqrt(N) * sum(r^3) / RV^1.5 where N = grid_per_day.

    Parameters
    ----------
    returns:
        Array of log-returns.
    grid_per_day:
        Number of intervals per trading day.

    Returns
    -------
    float.  Returns nan when fewer than 2 returns or RV == 0.
    """
    r = np.asarray(returns, dtype=np.float64)
    if r.size < 2:
        return _NAN
    rv = float(np.sum(r ** 2))
    if rv == 0.0:
        return _NAN
    n = float(grid_per_day)
    return float(np.sqrt(n) * np.sum(r ** 3) / (rv ** 1.5))


def realized_kurtosis(returns: np.ndarray, grid_per_day: float) -> float:
    """Intraday realized kurtosis (Amaya-Christoffersen-Jacobs-Vasquez, 2015).

    RKurt = N * sum(r^4) / RV^2 where N = grid_per_day.

    Parameters
    ----------
    returns:
        Array of log-returns.
    grid_per_day:
        Number of intervals per trading day.

    Returns
    -------
    float.  Returns nan when fewer than 2 returns or RV == 0.
    """
    r = np.asarray(returns, dtype=np.float64)
    if r.size < 2:
        return _NAN
    rv = float(np.sum(r ** 2))
    if rv == 0.0:
        return _NAN
    n = float(grid_per_day)
    return float(n * np.sum(r ** 4) / (rv ** 2))
