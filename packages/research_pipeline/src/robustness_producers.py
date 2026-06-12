"""Robustness metric producers for the CME event-universe Stage B pipeline.

Produces deflated_sharpe_ratio / PBO / bootstrap_ci / fee_stress that the
scorecard (packages/model_metrics/scorecard.py:147-152) consumes via the
robustness_stability category (thresholds: minimum_deflated_sharpe, maximum_pbo).

All functions are pure / deterministic (no global state, fixed seeds).

producer_version: rp_v2
  rp_v1: DSR (deflated_sharpe_for_cell), CSCV-PBO (cscv_pbo), bootstrap CI (bootstrap_ci)
  rp_v2: adds R6 fee/slippage stress (fee_stress_for_cell); backward-compatible additive.
         Records from pre-rp_v2 runs will show stress_data_available=False.
"""
from __future__ import annotations

import itertools
import math
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# DSR: import the canonical implementation from crypto_lane; copy inline with
# attribution if path not available (e.g. import fails in a minimal test env).
# ---------------------------------------------------------------------------

def _get_deflated_sharpe_cdf():
    """Return the deflated_sharpe_cdf callable.

    Tries to import from packages/crypto_lane/src/ml/walk_forward_runner.py
    (the authoritative implementation, Bailey & Lopez de Prado 2014).
    Falls back to the inline copy (identical logic, preserved for portability).
    """
    try:
        from crypto_lane.src.ml.walk_forward_runner import deflated_sharpe_cdf  # type: ignore[import]
        return deflated_sharpe_cdf
    except Exception:  # noqa: BLE001
        return _deflated_sharpe_cdf_inline


def _deflated_sharpe_cdf_inline(
    observed_sharpe: float,
    n_trials: int,
    n_obs: int,
    skew: float = 0.0,
    kurt: float = 3.0,
) -> float:
    """Inline copy of deflated_sharpe_cdf from crypto_lane.src.ml.walk_forward_runner.

    Attribution: Bailey & Lopez de Prado (2014), "The Deflated Sharpe Ratio:
    Correcting for Selection Bias, Backtest Overfitting and Non-Normality".
    Copied verbatim (modulo this docstring) from
    packages/crypto_lane/src/ml/walk_forward_runner.py::deflated_sharpe_cdf
    to avoid a hard cross-package dependency at import time.

    Returns the normal CDF in [0, 1]; higher = more extreme.
    To convert to a one-sided p-value use ``1 - deflated_sharpe_cdf(...)``.
    """
    if n_trials <= 0 or n_obs <= 2:
        return 0.0
    from math import erf, lgamma, sqrt

    if n_trials > 1:
        inner = (
            2.0 * lgamma(n_trials / 2.0 + 0.5)
            - lgamma(n_trials / 2.0)
            - n_trials * 0.5 * 0.5772
        )
        e_max = sqrt(inner) if inner > 0 else 0.0
    else:
        e_max = 0.0
    se = 1.0 / sqrt(n_obs - 1.0)
    denom = se * (
        1.0
        + (skew * observed_sharpe) / 2.0
        + ((kurt - 1.0) * observed_sharpe ** 2) / 6.0
    )
    if denom == 0.0:
        return 0.0
    z = (observed_sharpe - e_max) / denom
    return 0.5 * (1.0 + erf(z / math.sqrt(2.0)))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def deflated_sharpe_for_cell(
    per_event_expectancies: list[float],
    n_trials: int,
) -> dict[str, Any]:
    """Compute deflated Sharpe statistics for a single (hyp, event_type, band) cell.

    Parameters
    ----------
    per_event_expectancies:
        List of per-event expectancy values from the aggregation pool.
    n_trials:
        Full family size (len(tested_cells) including placeholders) — the
        multiplicity correction denominator.  Must be the COMPLETE stage-A
        family, not just the cells that were re-run in stage B, so that the
        DSR penalty reflects the true number of strategies considered.

    Returns
    -------
    Dict with keys:
        sharpe       – mean / std of per_event_expectancies (annualisation-free;
                       units: per-event signal-to-noise ratio).
        dsr_cdf      – CDF value in [0, 1]; higher = more extreme.
        dsr_pass     – bool: dsr_cdf >= 0.95 (institution threshold).
        one_sided_p  – 1 - dsr_cdf (one-sided p-value vs the expected-max null).
        n_obs        – number of events used.
        n_trials     – n_trials passed in (echoed for audit).
        skew         – sample skewness of per_event_expectancies.
        kurt         – sample excess kurtosis + 3 (total kurtosis, Gaussian = 3).
        reason       – None on success; human-readable string when n_obs < 3.
    """
    n_obs = len(per_event_expectancies)

    if n_obs < 3:
        return {
            "sharpe": None,
            "dsr_cdf": None,
            "dsr_pass": None,
            "one_sided_p": None,
            "n_obs": n_obs,
            "n_trials": n_trials,
            "skew": None,
            "kurt": None,
            "reason": f"insufficient_events: n_obs={n_obs} < 3",
        }

    arr = np.array(per_event_expectancies, dtype=float)
    mu = float(np.mean(arr))
    sigma = float(np.std(arr, ddof=1))

    if sigma < 1e-15:
        return {
            "sharpe": None,
            "dsr_cdf": None,
            "dsr_pass": None,
            "one_sided_p": None,
            "n_obs": n_obs,
            "n_trials": n_trials,
            "skew": None,
            "kurt": None,
            "reason": "zero_variance: std of per_event_expectancies is effectively zero",
        }

    sharpe = mu / sigma

    # Higher-moment adjustments for DSR
    skew = float(
        np.mean((arr - mu) ** 3) / (sigma ** 3) if sigma > 1e-15 else 0.0
    )
    # Total kurtosis (Gaussian = 3)
    kurt = float(
        np.mean((arr - mu) ** 4) / (sigma ** 4) if sigma > 1e-15 else 3.0
    )

    deflated_sharpe_cdf = _get_deflated_sharpe_cdf()
    dsr_cdf = deflated_sharpe_cdf(
        observed_sharpe=sharpe,
        n_trials=n_trials,
        n_obs=n_obs,
        skew=skew,
        kurt=kurt,
    )

    return {
        "sharpe": round(sharpe, 8),
        "dsr_cdf": round(dsr_cdf, 8),
        "dsr_pass": bool(dsr_cdf >= 0.95),
        "one_sided_p": round(1.0 - dsr_cdf, 8),
        "n_obs": n_obs,
        "n_trials": n_trials,
        "skew": round(skew, 8),
        "kurt": round(kurt, 8),
        "reason": None,
    }


def cscv_pbo(
    matrix: np.ndarray,
    n_splits: int = 8,
) -> dict[str, Any]:
    """Combinatorially Symmetric Cross-Validation PBO (Bailey et al. 2015).

    Parameters
    ----------
    matrix:
        2-D array, shape (n_blocks, n_configs).
        Rows = time blocks (events grouped into n_splits blocks in date order).
        Cols = candidate cells/configs.
        Entry [i, j] = mean expectancy of config j in block i.
        Cells with NaN indicate the config had no events in that block.

    n_splits:
        Number of time blocks.  Combinations are taken over n_splits choosing
        n_splits // 2.

    Returns
    -------
    Dict with keys:
        pbo          – float in [0, 1]: fraction of partitions where the
                       train-winner ranks in the bottom half of test.
                       PBO near 0 = good (winner generalises).
                       PBO near 0.5 = random / no generalisable best config.
        n_splits     – actual n_splits used (echoed).
        n_configs    – number of configs included (after NaN-masking).
        n_partitions – number of C(n_splits, n_splits//2) partitions evaluated.
        reason       – None on success; human-readable string on guard failure.
    """
    MAX_PARTITIONS = 200  # cap: first 200 in combination order (deterministic)

    if matrix is None or not hasattr(matrix, "shape"):
        return {"pbo": None, "n_splits": n_splits, "n_configs": None,
                "n_partitions": 0, "reason": "matrix_invalid: not an ndarray"}

    if matrix.ndim != 2:
        return {"pbo": None, "n_splits": n_splits, "n_configs": None,
                "n_partitions": 0, "reason": f"matrix_invalid: ndim={matrix.ndim} != 2"}

    n_blocks, n_configs_raw = matrix.shape

    if n_blocks < 4:
        return {
            "pbo": None,
            "n_splits": n_splits,
            "n_configs": n_configs_raw,
            "n_partitions": 0,
            "reason": f"insufficient_blocks: n_blocks={n_blocks} < 4",
        }

    if n_configs_raw < 2:
        return {
            "pbo": None,
            "n_splits": n_splits,
            "n_configs": n_configs_raw,
            "n_partitions": 0,
            "reason": f"insufficient_configs: n_configs={n_configs_raw} < 2",
        }

    # Mask: only include configs with full block coverage (no NaN in any block)
    mat = np.array(matrix, dtype=float)
    full_coverage_mask = ~np.any(np.isnan(mat), axis=0)
    n_excluded = int(np.sum(~full_coverage_mask))
    mat = mat[:, full_coverage_mask]
    n_configs = mat.shape[1]

    if n_configs < 2:
        return {
            "pbo": None,
            "n_splits": n_splits,
            "n_configs": n_configs,
            "n_partitions": 0,
            "n_excluded": n_excluded,
            "reason": f"insufficient_configs_after_nan_mask: n_configs={n_configs} < 2",
        }

    all_block_idx = list(range(n_blocks))
    half = n_blocks // 2

    # Generate all C(n_blocks, half) partitions; cap at MAX_PARTITIONS
    all_combos = list(itertools.islice(
        itertools.combinations(all_block_idx, half), MAX_PARTITIONS
    ))
    n_partitions = len(all_combos)

    bottom_half_count = 0

    for train_blocks in all_combos:
        train_set = set(train_blocks)
        test_blocks = [b for b in all_block_idx if b not in train_set]

        # Per-config mean across train / test blocks
        train_sharpes = np.mean(mat[list(train_blocks), :], axis=0)  # shape (n_configs,)
        test_sharpes = np.mean(mat[test_blocks, :], axis=0)

        # Winner = argmax of train-set Sharpe
        winner_idx = int(np.argmax(train_sharpes))

        # Rank the winner's test performance among all configs (1 = best, ascending)
        # rank 1 = highest test Sharpe (best), rank n_configs = lowest (worst)
        winner_test = float(test_sharpes[winner_idx])
        rank = int(np.sum(test_sharpes >= winner_test))  # configs with test >= winner
        # rank here = number of configs at least as good as winner in test
        # (including winner itself, so rank >= 1)
        # "bottom half" = winner ranks below the median config in test
        # i.e. rank > n_configs / 2
        if rank <= n_configs / 2:
            # winner is in top half of test — this is a GOOD partition (not bottom)
            pass
        else:
            bottom_half_count += 1

    pbo = bottom_half_count / n_partitions if n_partitions > 0 else float("nan")

    return {
        "pbo": round(float(pbo), 8),
        "n_splits": n_splits,
        "n_configs": n_configs,
        "n_partitions": n_partitions,
        "n_excluded": n_excluded,
        "reason": None,
    }


def fee_stress_for_cell(
    per_event_expectancies: list[float],
    per_event_n_trades: list[int],
    per_event_fee_per_rt: list[float],
    per_event_tick_value: list[float],
) -> dict[str, Any]:
    """R6 — Fee/slippage stress test (post-hoc, no re-replay).

    Analytically recomputes per-cell net expectancy under stress scenarios by
    recovering gross expectancy per event and subtracting stressed costs.

    Design constraint
    -----------------
    Does NOT re-run ReplaySession.  Uses the decomposition fields added to
    _worker's per-event output (fee_per_round_trip_usd, tick_value_usd) to
    derive gross expectancy, then applies multipliers / adders analytically.
    Runs produced BEFORE this commit lack these fields; for those records
    the function returns ``{"stress_data_available": False, ...}`` with null
    stress values rather than silently computing incorrect numbers.

    Parameters
    ----------
    per_event_expectancies:
        List of per-event net expectancy values (net = gross - fee_per_rt).
    per_event_n_trades:
        Number of round-trip trades per event (used for slippage total).
    per_event_fee_per_rt:
        Fee per round trip (USD) for each event's product/tier.
        Must be non-empty and > 0 for stress to be meaningful.
        If all values are 0.0 (old records lacking this field) returns
        stress_data_available=False.
    per_event_tick_value:
        Tick value in USD per contract for each event.
        Used to convert slip adder (in ticks) to USD per round trip.

    Stress scenarios
    ----------------
    * fee_x1_5: fees × 1.5  (50 % higher exchange costs / tier downgrade)
    * fee_x2  : fees × 2.0  (2x kill criterion — documented CC5/CC7 gate)
    * fee_x3  : fees × 3.0  (extreme fee scenario)
    * slip_p5t: +0.5 tick per round trip slippage adder
    * slip_1t : +1.0 tick per round trip slippage adder

    stress_pass = fee_x2_pass (2x-fee kill criterion; consumed at CC5/CC7
    promotion gate — does NOT mutate Holm/BH survivor logic).

    Returns
    -------
    Dict with keys:
        stress_data_available  – bool; False when fee decomposition missing.
        fee_x1_5_expectancy    – mean cell expectancy at 1.5× fees.
        fee_x2_expectancy      – mean cell expectancy at 2× fees.
        fee_x2_pass            – bool: fee_x2_expectancy > 0.
        fee_x3_expectancy      – mean cell expectancy at 3× fees.
        slip_p5t_expectancy    – mean cell expectancy with +0.5 tick slippage.
        slip_1t_expectancy     – mean cell expectancy with +1.0 tick slippage.
        stress_pass            – alias for fee_x2_pass (2x kill criterion).
        n_events               – number of events used.
        base_mean_expectancy   – mean of per_event_expectancies (echoed).
        reason                 – None on success; human-readable on guard.
    """
    n = len(per_event_expectancies)
    base_mean = float(np.mean(per_event_expectancies)) if n > 0 else 0.0

    # Guard: if fee decomposition fields are absent (old records),
    # all fee_per_rt will be 0.0 — stress arithmetic is meaningless.
    if not per_event_fee_per_rt or all(v == 0.0 for v in per_event_fee_per_rt):
        return {
            "stress_data_available": False,
            "fee_x1_5_expectancy": None,
            "fee_x2_expectancy": None,
            "fee_x2_pass": None,
            "fee_x3_expectancy": None,
            "slip_p5t_expectancy": None,
            "slip_1t_expectancy": None,
            "stress_pass": None,
            "n_events": n,
            "base_mean_expectancy": round(base_mean, 8),
            "reason": "fee_decomposition_unavailable: records predate rp_v2 decomposition fields",
        }

    if n == 0:
        return {
            "stress_data_available": False,
            "fee_x1_5_expectancy": None,
            "fee_x2_expectancy": None,
            "fee_x2_pass": None,
            "fee_x3_expectancy": None,
            "slip_p5t_expectancy": None,
            "slip_1t_expectancy": None,
            "stress_pass": None,
            "n_events": 0,
            "base_mean_expectancy": None,
            "reason": "no_events",
        }

    arr_exp = np.array(per_event_expectancies, dtype=float)
    arr_fee = np.array(per_event_fee_per_rt, dtype=float)
    arr_tv  = np.array(per_event_tick_value, dtype=float)

    # Recover per-event gross expectancy: gross = net + fee_per_round_trip
    arr_gross = arr_exp + arr_fee

    # Fee multiplier scenarios: net_exp_at_m = gross - fee * m
    def _stressed_mean(multiplier: float) -> float:
        return float(np.mean(arr_gross - arr_fee * multiplier))

    fee_x1_5 = _stressed_mean(1.5)
    fee_x2   = _stressed_mean(2.0)
    fee_x3   = _stressed_mean(3.0)

    # Slippage adder scenarios: penalty per round trip = tick_value * adder_ticks
    # net_exp_at_slip = gross - fee - tick_value * adder_ticks
    def _slip_mean(adder_ticks: float) -> float:
        return float(np.mean(arr_gross - arr_fee - arr_tv * adder_ticks))

    slip_p5t = _slip_mean(0.5)
    slip_1t  = _slip_mean(1.0)

    fee_x2_pass = bool(fee_x2 > 0.0)

    return {
        "stress_data_available": True,
        "fee_x1_5_expectancy": round(fee_x1_5, 8),
        "fee_x2_expectancy":   round(fee_x2,   8),
        "fee_x2_pass":         fee_x2_pass,
        "fee_x3_expectancy":   round(fee_x3,   8),
        "slip_p5t_expectancy": round(slip_p5t, 8),
        "slip_1t_expectancy":  round(slip_1t,  8),
        "stress_pass":         fee_x2_pass,
        "n_events":            n,
        "base_mean_expectancy": round(base_mean, 8),
        "reason":              None,
    }


def bootstrap_ci(
    per_event_expectancies: list[float],
    n_boot: int = 2000,
    seed: int = 0,
) -> dict[str, Any]:
    """Percentile bootstrap confidence interval for the mean.

    Parameters
    ----------
    per_event_expectancies:
        List of per-event expectancy values.
    n_boot:
        Number of bootstrap resamples (default 2000).
    seed:
        Random seed for numpy default_rng (deterministic).

    Returns
    -------
    Dict with keys:
        mean      – sample mean of per_event_expectancies.
        ci_lo_95  – 2.5th percentile of bootstrap means.
        ci_hi_95  – 97.5th percentile of bootstrap means.
        n         – number of observations.
    """
    n = len(per_event_expectancies)
    arr = np.array(per_event_expectancies, dtype=float)
    mean = float(np.mean(arr)) if n > 0 else float("nan")

    if n < 2:
        return {
            "mean": mean if n == 1 else None,
            "ci_lo_95": None,
            "ci_hi_95": None,
            "n": n,
        }

    rng = np.random.default_rng(seed)
    # Draw n_boot bootstrap samples (each of size n, with replacement)
    indices = rng.integers(0, n, size=(n_boot, n))
    boot_means = arr[indices].mean(axis=1)

    ci_lo = float(np.percentile(boot_means, 2.5))
    ci_hi = float(np.percentile(boot_means, 97.5))

    return {
        "mean": round(mean, 8),
        "ci_lo_95": round(ci_lo, 8),
        "ci_hi_95": round(ci_hi, 8),
        "n": n,
    }
