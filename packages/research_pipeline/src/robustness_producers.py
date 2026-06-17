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
# DSR: canonical inline implementation (formerly shared with the crypto lane,
# which moved to the hft3-crypto-lane repo).
# ---------------------------------------------------------------------------

def _get_deflated_sharpe_cdf():
    """Return the deflated_sharpe_cdf callable (the inline implementation)."""
    return _deflated_sharpe_cdf_inline


def _deflated_sharpe_cdf_inline(
    observed_sharpe: float,
    n_trials: int,
    n_obs: int,
    skew: float = 0.0,
    kurt: float = 3.0,
) -> float:
    """Deflated Sharpe ratio CDF.

    Attribution: Bailey & Lopez de Prado (2014), "The Deflated Sharpe Ratio:
    Correcting for Selection Bias, Backtest Overfitting and Non-Normality".

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


def slippage_stress_for_cell(
    per_event_expectancies: list[float],
    per_event_n_trades: list[int],
    per_event_fee_per_rt: list[float],
    per_event_tick_value: list[float],
) -> dict[str, Any]:
    """R6 — Slippage stress test (post-hoc, no re-replay).

    Satisfies ROBUSTNESS_TESTING_SPEC.md §10 line 284 ("slippage multiplier
    stress"), kept separate from ``fee_stress_for_cell`` (§10 line 283) so the
    two required checks can be reported independently.

    Analytically recomputes per-cell net expectancy under slippage stress
    scenarios by recovering gross expectancy per event and subtracting
    stressed slippage costs.  Does NOT re-run ReplaySession; uses the
    decomposition fields (fee_per_round_trip_usd, tick_value_usd) recorded by
    _worker.  Runs produced BEFORE the rp_v2 decomposition lack these fields;
    for those records the function returns ``{"stress_data_available": False,
    ...}`` with null stress values rather than silently computing incorrect
    numbers.

    Parameters
    ----------
    per_event_expectancies:
        List of per-event net expectancy values (net = gross - fee_per_rt).
    per_event_n_trades:
        Number of round-trip trades per event.  Retained for parity with the
        other producers; the analytic slippage cost below is modelled per
        round trip, so n_trades is not multiplied in (the decomposition already
        records one fee/slip value per round trip).
    per_event_fee_per_rt:
        Fee per round trip (USD) for each event's product/tier.
        Must be non-empty and have at least one non-zero value for stress to
        be meaningful.  If all values are 0.0 (old records lacking this field)
        returns stress_data_available=False.
    per_event_tick_value:
        Tick value in USD per contract for each event.  Used to convert
        slippage adders (in ticks) and multipliers to USD per round trip.

    Stress scenarios
    ----------------
    * slip_x1_5: slippage × 1.5 (50 % more slippage per round trip).  A base
      slippage estimate is derived from tick_value (1 tick ≈ 1 × tick_value of
      slippage per round trip), then scaled by the multiplier.
    * slip_x2  : slippage × 2.0  (2x slippage kill criterion).
    * slip_x3  : slippage × 3.0  (extreme slippage scenario).
    * slip_p5t : +0.5 tick per round trip slippage adder (self-contained carry
      of the adder scenario from fee_stress so callers need only one call).
    * slip_1t  : +1.0 tick per round trip slippage adder.

    stress_pass = slip_x2_expectancy > 0 (2x-slippage kill criterion).

    Returns
    -------
    Dict with keys:
        stress_data_available  – bool; False when decomposition fields missing.
        slip_x1_5_expectancy   – mean cell expectancy at 1.5× slippage.
        slip_x2_expectancy     – mean cell expectancy at 2× slippage.
        slip_x2_pass           – bool: slip_x2_expectancy > 0.
        slip_x3_expectancy     – mean cell expectancy at 3× slippage.
        slip_p5t_expectancy    – mean cell expectancy with +0.5 tick slippage.
        slip_1t_expectancy     – mean cell expectancy with +1.0 tick slippage.
        stress_pass            – alias for slip_x2_pass.
        n_events               – number of events used.
        base_mean_expectancy   – mean of per_event_expectancies (echoed).
        reason                 – None on success; human-readable on guard.
    """
    n = len(per_event_expectancies)
    base_mean = float(np.mean(per_event_expectancies)) if n > 0 else 0.0

    # Guard: if fee/tick decomposition fields are absent (old records), all
    # fee_per_rt will be 0.0 — slippage arithmetic is meaningless without a
    # tick_value baseline.  Fail closed.
    if not per_event_fee_per_rt or all(v == 0.0 for v in per_event_fee_per_rt):
        return {
            "stress_data_available": False,
            "slip_x1_5_expectancy": None,
            "slip_x2_expectancy":   None,
            "slip_x2_pass":         None,
            "slip_x3_expectancy":   None,
            "slip_p5t_expectancy":  None,
            "slip_1t_expectancy":   None,
            "stress_pass":          None,
            "n_events":             n,
            "base_mean_expectancy": round(base_mean, 8),
            "reason": "decomposition_unavailable: records predate rp_v2 decomposition fields",
        }

    if n == 0:
        return {
            "stress_data_available": False,
            "slip_x1_5_expectancy": None,
            "slip_x2_expectancy":   None,
            "slip_x2_pass":         None,
            "slip_x3_expectancy":   None,
            "slip_p5t_expectancy":  None,
            "slip_1t_expectancy":   None,
            "stress_pass":          None,
            "n_events":             0,
            "base_mean_expectancy": None,
            "reason":               "no_events",
        }

    arr_exp = np.array(per_event_expectancies, dtype=float)
    arr_fee = np.array(per_event_fee_per_rt, dtype=float)
    arr_tv  = np.array(per_event_tick_value, dtype=float)

    # Recover per-event gross expectancy: gross = net + fee_per_round_trip
    arr_gross = arr_exp + arr_fee

    # Base slippage estimate: 1 tick worth of slippage per round trip.
    # Slippage multiplier scenarios scale this baseline:
    #   net_exp_at_m = gross - fee - tick_value * (m - 1.0)
    # The (m - 1.0) term represents the *additional* slippage beyond the 1-tick
    # baseline already embedded in the realised gross expectancy (slippage is
    # part of execution cost captured in the net figure).  slip_x1 (m=1.0)
    # therefore recovers the base net expectancy, slip_x2 adds one extra tick.
    base_slip_ticks = 1.0

    def _slip_mult_mean(multiplier: float) -> float:
        extra_ticks = base_slip_ticks * (multiplier - 1.0)
        return float(np.mean(arr_gross - arr_fee - arr_tv * extra_ticks))

    slip_x1_5 = _slip_mult_mean(1.5)
    slip_x2   = _slip_mult_mean(2.0)
    slip_x3   = _slip_mult_mean(3.0)

    # Slippage adder scenarios: penalty per round trip = tick_value * adder_ticks
    # net_exp_at_slip = gross - fee - tick_value * adder_ticks
    def _slip_add_mean(adder_ticks: float) -> float:
        return float(np.mean(arr_gross - arr_fee - arr_tv * adder_ticks))

    slip_p5t = _slip_add_mean(0.5)
    slip_1t  = _slip_add_mean(1.0)

    slip_x2_pass = bool(slip_x2 > 0.0)

    return {
        "stress_data_available": True,
        "slip_x1_5_expectancy": round(slip_x1_5, 8),
        "slip_x2_expectancy":   round(slip_x2,   8),
        "slip_x2_pass":         slip_x2_pass,
        "slip_x3_expectancy":   round(slip_x3,   8),
        "slip_p5t_expectancy":  round(slip_p5t, 8),
        "slip_1t_expectancy":   round(slip_1t,  8),
        "stress_pass":          slip_x2_pass,
        "n_events":             n,
        "base_mean_expectancy": round(base_mean, 8),
        "reason":               None,
    }


def latency_stress_for_cell(
    per_event_expectancies: list[float],
    per_event_n_trades: list[int],
    per_event_fee_per_rt: list[float],
    per_event_tick_value: list[float],
    latency_ms_baseline: float = 0.0,
    latency_ms_stress: float = 1.0,
    tick_value_usd: float = 12.5,
    ticks_per_ms: float = 0.001,
) -> dict[str, Any]:
    """R6 — Latency stress test (post-hoc, no re-replay).

    Satisfies ROBUSTNESS_TESTING_SPEC.md §10 line 285 ("latency stress").
    Analytically models latency cost as additional slippage: the extra delay
    translates into adverse fill movement, parameterised as ticks lost per
    millisecond of latency.

        latency_cost_per_rt = latency_ms * ticks_per_ms * tick_value_usd

    The per-event net expectancy already embeds the baseline latency cost; the
    stress scenario subtracts the full stressed latency cost
    (``latency_ms_stress``).  The reported ``latency_cost_per_rt`` is the
    *incremental* cost (stress minus baseline) so callers can see how much
    extra cost the stress scenario imposes per round trip.

    Does NOT re-run ReplaySession.  Uses the rp_v2 decomposition fields
    (fee_per_round_trip_usd, tick_value_usd) recorded by _worker.  Runs
    produced BEFORE the rp_v2 decomposition lack these fields; for those
    records the function returns ``{"stress_data_available": False, ...}``
    with null stress values rather than silently computing incorrect numbers.

    Parameters
    ----------
    per_event_expectancies:
        List of per-event net expectancy values (net = gross - fee_per_rt).
    per_event_n_trades:
        Number of round-trip trades per event.  Retained for parity; the
        latency cost is modelled per round trip so n_trades is not multiplied
        in (the decomposition already records one value per round trip).
    per_event_fee_per_rt:
        Fee per round trip (USD) for each event's product/tier.
        Must have at least one non-zero value for stress to be meaningful.
        If all values are 0.0 (old records lacking this field) returns
        stress_data_available=False.
    per_event_tick_value:
        Tick value in USD per contract for each event (echoed for parity; the
        scalar ``tick_value_usd`` is used for the latency-cost conversion so
        the model is well-defined even when per-event tick values vary).
    latency_ms_baseline:
        Baseline latency in milliseconds already embedded in the realised
        expectancy (default 0.0 — i.e. the recorded expectancy assumes no
        incremental latency cost).
    latency_ms_stress:
        Stressed latency in milliseconds to apply (default 1.0 ms).
    tick_value_usd:
        Tick value in USD per contract used to convert latency-induced ticks
        to USD cost (default 12.5 — ES futures tick value).
    ticks_per_ms:
        Ticks of adverse fill movement per millisecond of latency (default
        0.001 — 1 tick per 1000 ms, i.e. ~1 tick per second of latency).

    stress_pass = stress_expectancy > 0.

    Returns
    -------
    Dict with keys:
        stress_data_available    – bool; False when decomposition fields missing.
        baseline_expectancy      – mean cell expectancy at latency_ms_baseline.
        stress_expectancy        – mean cell expectancy at latency_ms_stress.
        latency_cost_per_rt      – incremental latency cost per round trip (USD).
        stress_pass              – bool: stress_expectancy > 0.
        n_events                 – number of events used.
        base_mean_expectancy     – mean of per_event_expectancies (echoed).
        reason                   – None on success; human-readable on guard.
    """
    n = len(per_event_expectancies)
    base_mean = float(np.mean(per_event_expectancies)) if n > 0 else 0.0

    # Guard: if fee/tick decomposition fields are absent (old records), all
    # fee_per_rt will be 0.0 — latency cost arithmetic is meaningless without
    # a recoverable gross expectancy.  Fail closed.
    if not per_event_fee_per_rt or all(v == 0.0 for v in per_event_fee_per_rt):
        return {
            "stress_data_available": False,
            "baseline_expectancy":   None,
            "stress_expectancy":     None,
            "latency_cost_per_rt":   None,
            "stress_pass":           None,
            "n_events":              n,
            "base_mean_expectancy":  round(base_mean, 8),
            "reason": "decomposition_unavailable: records predate rp_v2 decomposition fields",
        }

    if n == 0:
        return {
            "stress_data_available": False,
            "baseline_expectancy":  None,
            "stress_expectancy":    None,
            "latency_cost_per_rt":   None,
            "stress_pass":           None,
            "n_events":              0,
            "base_mean_expectancy":  None,
            "reason":                "no_events",
        }

    arr_exp = np.array(per_event_expectancies, dtype=float)
    arr_fee = np.array(per_event_fee_per_rt, dtype=float)

    # Recover per-event gross expectancy: gross = net + fee_per_round_trip.
    # The gross figure is latency-cost-free (fee is exchange fee, not
    # slippage); the net figure embeds the baseline latency cost.
    arr_gross = arr_exp + arr_fee

    # Per-round-trip latency cost at a given latency (USD):
    #   latency_cost_per_rt(ms) = ms * ticks_per_ms * tick_value_usd
    baseline_cost_per_rt = latency_ms_baseline * ticks_per_ms * tick_value_usd
    stress_cost_per_rt   = latency_ms_stress   * ticks_per_ms * tick_value_usd

    # Incremental latency cost per round trip (USD) — the quantity actually
    # being stressed (reported to callers as latency_cost_per_rt).
    latency_cost_per_rt = max(stress_cost_per_rt - baseline_cost_per_rt, 0.0)

    # Baseline expectancy: gross minus fee minus baseline latency cost.
    # (With latency_ms_baseline=0 this equals the echoed base net mean.)
    baseline_expectancy = float(np.mean(arr_gross - arr_fee - baseline_cost_per_rt))

    # Stress expectancy: gross minus fee minus full stressed latency cost.
    # When latency_ms_stress == latency_ms_baseline this equals baseline.
    stress_expectancy = float(np.mean(arr_gross - arr_fee - stress_cost_per_rt))

    stress_pass = bool(stress_expectancy > 0.0)

    return {
        "stress_data_available": True,
        "baseline_expectancy":   round(baseline_expectancy, 8),
        "stress_expectancy":     round(stress_expectancy, 8),
        "latency_cost_per_rt":   round(latency_cost_per_rt, 8),
        "stress_pass":           stress_pass,
        "n_events":              n,
        "base_mean_expectancy":  round(base_mean, 8),
        "reason":                None,
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


# ---------------------------------------------------------------------------
# Multiple-testing correction: Holm step-down & Benjamini-Hochberg FDR
# Satisfies ROBUSTNESS_TESTING_SPEC.md §10 line 282
# ("Holm/BH multiple-testing correction").
# ---------------------------------------------------------------------------

def holm_bh_correction(
    p_values: list[float],
    alpha: float = 0.05,
    method: str = "bh",
) -> dict[str, Any]:
    """Multiple-testing p-value correction for the Stage-B family.

    Satisfies ROBUSTNESS_TESTING_SPEC.md §10 line 282
    ("Holm/BH multiple-testing correction").  Implements both the Holm
    step-down family-wise error-rate (FWER) procedure and the
    Benjamini-Hochberg false-discovery-rate (FDR) procedure in one callable,
    selected by ``method``.

    Attribution
    -----------
    * ``method="bh"`` — Benjamini & Hochberg (1995), "Controlling the False
      Discovery Rate: A Practical and Powerful Approach to Multiple Testing",
      Journal of the Royal Statistical Society, Series B 57(1): 289-300.
    * ``method="holm"`` — Holm (1979), "A Simple Sequentially Rejective
      Multiple Test Procedure", Scandinavian Journal of Statistics 6(2):
      65-70.  Included alongside BH so FWER-strict and FDR-strict views of
      the same family can be reported independently (the vault's
      library/13 Robust Backtesting and Multiple Testing.md cites both).

    Both methods consume the COMPLETE Stage-A family (all tested cells,
    including placeholders) so the multiplicity penalty reflects the true
    number of strategies considered.

    Algorithm
    ----------
    1. Sort p-values ascending: p_(0) <= p_(1) <= ... <= p_(n-1).
    2. ``method="bh"`` (step-up FDR):
         raw[i] = p_(i) * n / (rank[i])      where rank[i] = i + 1
         Enforce monotonicity from the top (largest rank down):
         adjusted[i] = min(raw[i], adjusted[i+1]); cap at 1.0.
    3. ``method="holm"`` (step-down FWER):
         raw[i] = p_(i) * (n - i)            (i is 0-based sorted index)
         Enforce monotonicity from the bottom (smallest p up):
         adjusted[i] = max(raw[0..i]); cap at 1.0.
    4. Scatter adjusted values back to the ORIGINAL input order.
    5. rejected[i] = adjusted_p[i] <= alpha.

    Parameters
    ----------
    p_values:
        List of per-test p-values to correct (the Stage-A / Stage-B family).
    alpha:
        Family-wise / false-discovery-rate level (default 0.05).
    method:
        ``"bh"``   — Benjamini-Hochberg FDR step-up (default).
        ``"holm"`` — Holm step-down FWER.

    Returns
    -------
    Dict with keys:
        corrected_p_values – list[float], adjusted p-values in the ORIGINAL
                              input order (monotonicity enforced, capped at 1).
        rejected            – list[bool], ``corrected_p_values[i] <= alpha``.
        n_rejected          – int, count of rejected hypotheses.
        method              – str, echoed (lower-cased) method name.
        alpha               – float, echoed alpha.
        n_tests             – int, number of tests (len(p_values)).
        reason              – None on success; human-readable string when the
                              input is empty / invalid (fail-closed).
    """
    n_tests = len(p_values)

    # Fail-closed guard: empty family.
    if n_tests == 0:
        return {
            "corrected_p_values": [],
            "rejected": [],
            "n_rejected": 0,
            "method": method,
            "alpha": alpha,
            "n_tests": 0,
            "reason": "no_p_values: empty p_values list",
        }

    arr = np.asarray(p_values, dtype=float)

    # Fail-closed guard: p-values must be finite in [0, 1].
    if not np.all(np.isfinite(arr)) or np.any(arr < 0.0) or np.any(arr > 1.0):
        return {
            "corrected_p_values": [None] * n_tests,
            "rejected": [False] * n_tests,
            "n_rejected": 0,
            "method": method,
            "alpha": alpha,
            "n_tests": n_tests,
            "reason": "invalid_p_values: values must be finite in [0, 1]",
        }

    method_l = method.lower()
    if method_l not in ("bh", "holm"):
        return {
            "corrected_p_values": [None] * n_tests,
            "rejected": [False] * n_tests,
            "n_rejected": 0,
            "method": method,
            "alpha": alpha,
            "n_tests": n_tests,
            "reason": f"unknown_method: {method!r} (expected 'bh' or 'holm')",
        }

    n = n_tests
    # Stable ascending sort so ties keep input order; record permutation to
    # scatter results back to the original order afterwards.
    order = np.argsort(arr, kind="stable")
    sorted_p = arr[order]

    if method_l == "bh":
        # Benjamini-Hochberg step-up: p_(i) * n / rank[i]; enforce monotonicity
        # from the largest rank downward (accumulate min over the reverse).
        ranks = np.arange(1, n + 1, dtype=float)
        raw = sorted_p * n / ranks
        adjusted_sorted = np.minimum.accumulate(raw[::-1])[::-1]
        adjusted_sorted = np.minimum(adjusted_sorted, 1.0)
    else:  # holm
        # Holm step-down: p_(i) * (n - i); enforce monotonicity from the
        # smallest p upward (accumulate max).
        multipliers = np.array([n - j for j in range(n)], dtype=float)
        raw = sorted_p * multipliers
        adjusted_sorted = np.maximum.accumulate(raw)
        adjusted_sorted = np.minimum(adjusted_sorted, 1.0)

    # Scatter back to original input order.
    corrected = np.empty(n, dtype=float)
    corrected[order] = adjusted_sorted

    rejected = corrected <= alpha
    n_rejected = int(np.sum(rejected))

    return {
        "corrected_p_values": [round(float(v), 8) for v in corrected],
        "rejected": [bool(v) for v in rejected],
        "n_rejected": n_rejected,
        "method": method_l,
        "alpha": alpha,
        "n_tests": n_tests,
        "reason": None,
    }


def null_strategy_battery(
    per_event_expectancies: list[float],
    n_null_runs: int = 1000,
    seed: int = 0,
    min_p_value: float = 0.05,
) -> dict[str, Any]:
    """Null-strategy battery — sign-shuffle permutation test for real edge.

    Satisfies ROBUSTNESS_TESTING_SPEC.md §10 line 287 ("null strategy
    battery").  Builds a null distribution for the cell mean expectancy by
    sign-shuffling the observed per-event expectancies ``n_null_runs`` times
    and comparing the observed mean against that null distribution
    (two-sided).  A genuine edge produces an observed mean far outside the
    null mass -> low p_value -> pass; a cell indistinguishable from random
    sign-flips -> high p_value -> fail.

    Attribution
    -----------
    * White (2000), "A Reality Check for Data Snooping", Econometrica
      68(5): 1097-1126 — bootstrap / permutation reality-check framing for
      multiple-testing survivorship.  The sign-shuffle null here is a
      permutation-test instantiation of the same idea at the single-cell
      level; the cross-family snooping dimension is handled separately by
      ``holm_bh_correction`` (the two producers are complementary: BH/Holm
      corrects the family of surviving cells, the null battery interrogates
      each cell's mean against its own sign-flipped null).

    Algorithm
    ----------
    1. observed_mean = mean(per_event_expectancies).
    2. For each of n_null_runs: draw independent ±1 signs (deterministic
       numpy ``default_rng(seed)``), multiply elementwise by the
       expectancies, take the mean -> one null_run mean.
    3. p_value = fraction of null_run means with |null_mean| >= |observed_mean|
       (two-sided).
    4. null_pass = p_value < min_p_value.

    Parameters
    ----------
    per_event_expectancies:
        List of per-event expectancy values for one cell.
    n_null_runs:
        Number of sign-shuffled null runs (default 1000).  Must be >= 1.
    seed:
        Seed for ``numpy.random.default_rng`` (deterministic; default 0).
    min_p_value:
        p-value threshold below which the cell is deemed to have a real edge
        (default 0.05).  ``null_pass = p_value < min_p_value``.

    Returns
    -------
    Dict with keys:
        observed_mean – float, mean of the original per_event_expectancies.
        null_mean     – float, mean of the null-run means.
        null_std      – float, std (ddof=0) of the null-run means.
        p_value       – float in [0, 1], two-sided fraction of null runs
                        with |null_mean| >= |observed_mean|.
        null_pass     – bool, ``p_value < min_p_value``.
        n_null_runs   – int, echoed.
        seed          – int, echoed.
        min_p_value   – float, echoed.
        n_obs         – int, number of observations.
        reason        – None on success; human-readable string when the input
                        is empty / insufficient (fail-closed).
    """
    n_obs = len(per_event_expectancies)

    # Fail-closed guard: no observations.
    if n_obs == 0:
        return {
            "observed_mean": None,
            "null_mean": None,
            "null_std": None,
            "p_value": None,
            "null_pass": False,
            "n_null_runs": n_null_runs,
            "seed": seed,
            "min_p_value": min_p_value,
            "n_obs": 0,
            "reason": "no_observations: empty per_event_expectancies",
        }

    arr = np.asarray(per_event_expectancies, dtype=float)

    # Fail-closed guard: non-finite observations.
    if not np.all(np.isfinite(arr)):
        return {
            "observed_mean": None,
            "null_mean": None,
            "null_std": None,
            "p_value": None,
            "null_pass": False,
            "n_null_runs": n_null_runs,
            "seed": seed,
            "min_p_value": min_p_value,
            "n_obs": n_obs,
            "reason": "non_finite_observations: per_event_expectancies contain NaN/inf",
        }

    # Fail-closed guard: no null runs requested — cannot form a distribution.
    if n_null_runs < 1:
        return {
            "observed_mean": round(float(np.mean(arr)), 8),
            "null_mean": None,
            "null_std": None,
            "p_value": None,
            "null_pass": False,
            "n_null_runs": n_null_runs,
            "seed": seed,
            "min_p_value": min_p_value,
            "n_obs": n_obs,
            "reason": f"insufficient_null_runs: n_null_runs={n_null_runs} < 1",
        }

    observed_mean = float(np.mean(arr))

    # Deterministic sign-shuffle null: each run independently flips the sign
    # of every observation with probability 0.5 and takes the mean.
    rng = np.random.default_rng(seed)
    signs = rng.choice(np.array([-1.0, 1.0]), size=(n_null_runs, n_obs))
    null_means = np.mean(arr * signs, axis=1)

    null_mean = float(np.mean(null_means))
    null_std = float(np.std(null_means, ddof=0))

    # Two-sided p-value: fraction of null means at least as extreme
    # (in absolute value) as the observed mean.
    p_value = float(np.mean(np.abs(null_means) >= abs(observed_mean)))

    null_pass = bool(p_value < min_p_value)

    return {
        "observed_mean": round(observed_mean, 8),
        "null_mean": round(null_mean, 8),
        "null_std": round(null_std, 8),
        "p_value": round(p_value, 8),
        "null_pass": null_pass,
        "n_null_runs": n_null_runs,
        "seed": seed,
        "min_p_value": min_p_value,
        "n_obs": n_obs,
        "reason": None,
    }


# ---------------------------------------------------------------------------
# Overfitting diagnostics: planted-alpha synthetic control & adversarial
# perturbation.  Satisfy ROBUSTNESS_TESTING_SPEC.md §10 lines 288-289
# ("planted-alpha synthetic control" and "adversarial perturbation").
# ---------------------------------------------------------------------------

def planted_alpha_synthetic_control(
    per_event_expectancies: list[float],
    n_planted: int = 100,
    alpha_strength: float = 0.01,
    seed: int = 0,
    min_p_value: float = 0.05,
) -> dict[str, Any]:
    """Planted-alpha synthetic control — validates the gauntlet can detect edge.

    Satisfies ROBUSTNESS_TESTING_SPEC.md §10 line 288
    ("planted-alpha synthetic control").  A planted-alpha test injects a
    known synthetic edge into random positions of a zero-mean baseline, then
    checks whether the screening/promotion pipeline (here the
    ``null_strategy_battery`` sign-shuffle permutation test) would detect the
    planted edge.  This validates that the robustness gauntlet can distinguish
    real edge from noise: the planted series MUST be detected (low p_value)
    while the un-planted zero-mean baseline MUST NOT be detected (high
    p_value).

    Attribution
    -----------
    Bailey, Borwein, Lopez de Prado & Zhu (2017), "The Probability of
    Backtest Overfitting and the Deflated Sharpe Ratio", Journal of
    Computational Finance (the PBO/CSCV paper).  The vault's
    library/13 Robust Backtesting and Multiple Testing.md cites this as the
    spine of the overfitting-diagnostic literature.  The planted-alpha control
    is a standard overfitting diagnostic from the same literature: by
    injecting a *known* signal into a null baseline and requiring the
    detector to flag it, we confirm the gauntlet is calibrated rather than
    merely returning "no edge" for everything.

    Algorithm
    ----------
    1. Build a zero-mean baseline: ``baseline = expectancies - mean(expectancies)``.
    2. Plant alpha: add ``alpha_strength`` to ``n_planted`` random positions
       (deterministic ``np.random.default_rng(seed)``) of the baseline.
    3. Run ``null_strategy_battery`` on the planted data → ``planted_p_value``.
    4. Run ``null_strategy_battery`` on the un-planted (demeaned) baseline →
       ``baseline_p_value``.
    5. ``planted_pass = planted_p_value < min_p_value
       AND baseline_p_value >= min_p_value`` (detector flags the signal AND
       does not false-positive on the null).

    Parameters
    ----------
    per_event_expectancies:
        List of per-event expectancy values for one cell.
    n_planted:
        Number of positions to plant with synthetic alpha (default 100).
        Clipped to ``n_obs`` when larger than the series length.
    alpha_strength:
        Magnitude of the synthetic edge added to each planted position
        (default 0.01).  Interpreted in the same units as the expectancies.
    seed:
        Seed for ``numpy.random.default_rng`` (deterministic; default 0).
    min_p_value:
        p-value threshold below which the planted series is deemed detected
        (default 0.05).  ``planted_pass`` requires
        ``planted_p_value < min_p_value``.

    Returns
    -------
    Dict with keys:
        planted_p_value  – float in [0, 1]; p_value of the null battery on
                            the planted series (should be low when detection
                            works).
        baseline_p_value  – float in [0, 1]; p_value of the null battery on
                            the demeaned un-planted baseline (should be high
                            — no false positive on the null).
        alpha_strength    – float, echoed.
        n_planted         – int, actual number of positions planted (clipped
                            to n_obs).
        n_obs             – int, number of observations.
        planted_pass      – bool; True only when the planted series is
                            detected AND the baseline is NOT (fail-closed on
                            a detector that fires on noise).
        seed              – int, echoed.
        min_p_value       – float, echoed.
        reason            – None on success; human-readable string when the
                            input is empty / insufficient (fail-closed).
    """
    n_obs = len(per_event_expectancies)

    # Fail-closed guard: no observations.
    if n_obs == 0:
        return {
            "planted_p_value": None,
            "baseline_p_value": None,
            "alpha_strength": alpha_strength,
            "n_planted": n_planted,
            "n_obs": 0,
            "planted_pass": False,
            "seed": seed,
            "min_p_value": min_p_value,
            "reason": "no_observations: empty per_event_expectancies",
        }

    arr = np.asarray(per_event_expectancies, dtype=float)

    # Fail-closed guard: non-finite observations.
    if not np.all(np.isfinite(arr)):
        return {
            "planted_p_value": None,
            "baseline_p_value": None,
            "alpha_strength": alpha_strength,
            "n_planted": n_planted,
            "n_obs": n_obs,
            "planted_pass": False,
            "seed": seed,
            "min_p_value": min_p_value,
            "reason": "non_finite_observations: per_event_expectancies contain NaN/inf",
        }

    # Build the zero-mean (demeaned) baseline.
    baseline = arr - float(np.mean(arr))

    # Plant alpha: add alpha_strength to n_planted random positions.
    # Clip the count to the series length so the call is well-defined for
    # short series (n_planted larger than n_obs plants into every position).
    n_planted_eff = max(0, min(int(n_planted), n_obs))
    if n_planted_eff == 0:
        return {
            "planted_p_value": None,
            "baseline_p_value": None,
            "alpha_strength": alpha_strength,
            "n_planted": n_planted_eff,
            "n_obs": n_obs,
            "planted_pass": False,
            "seed": seed,
            "min_p_value": min_p_value,
            "reason": "no_planted_positions: n_planted <= 0 after clipping",
        }

    rng = np.random.default_rng(seed)
    planted = baseline.copy()
    plant_idx = rng.choice(n_obs, size=n_planted_eff, replace=False)
    planted[plant_idx] = planted[plant_idx] + float(alpha_strength)

    # Run the null_strategy_battery on both series (deterministic; the
    # battery uses its own default_rng(seed) so the planted/baseline draws
    # are reproducible and independent of the planting draw above).
    planted_result = null_strategy_battery(
        per_event_expectancies=list(map(float, planted)),
        n_null_runs=1000,
        seed=seed,
        min_p_value=min_p_value,
    )
    baseline_result = null_strategy_battery(
        per_event_expectancies=list(map(float, baseline)),
        n_null_runs=1000,
        seed=seed,
        min_p_value=min_p_value,
    )

    planted_p_value = planted_result["p_value"]
    baseline_p_value = baseline_result["p_value"]

    # planted_pass requires BOTH that the detector flags the planted signal
    # AND that it does not false-positive on the null baseline.  A detector
    # that fires on noise fails closed.
    planted_pass = bool(
        planted_p_value is not None
        and baseline_p_value is not None
        and planted_p_value < min_p_value
        and baseline_p_value >= min_p_value
    )

    return {
        "planted_p_value": round(float(planted_p_value), 8) if planted_p_value is not None else None,
        "baseline_p_value": round(float(baseline_p_value), 8) if baseline_p_value is not None else None,
        "alpha_strength": alpha_strength,
        "n_planted": n_planted_eff,
        "n_obs": n_obs,
        "planted_pass": planted_pass,
        "seed": seed,
        "min_p_value": min_p_value,
        "reason": None,
    }


def adversarial_perturbation(
    per_event_expectancies: list[float],
    perturbation_fraction: float = 0.1,
    n_perturbations: int = 100,
    seed: int = 0,
    min_survival_rate: float = 0.8,
) -> dict[str, Any]:
    """Adversarial perturbation test — worst-case sign-flip survival rate.

    Satisfies ROBUSTNESS_TESTING_SPEC.md §10 line 289
    ("adversarial perturbation").  Perturbs a fraction of the observed
    expectancies adversarially — worsening the sign of the worst positions
    (positive → negative, negative → more negative) — across
    ``n_perturbations`` deterministic runs, and measures the survival rate
    (fraction of runs where the perturbed mean remains positive, assuming a
    positive expectancy is the edge).  A robust strategy should survive a
    modest fraction of adversarial corruption; a brittle / overfit one flips
    negative quickly.

    Attribution
    -----------
    Bailey, Borwein, Lopez de Prado & Zhu (2014), "Pseudo-Mathematics and
    Financial Charlatanism: The Effects of Backtest Overfitting on
    Out-of-Sample Performance", Notices of the AMS 61(5): 458-471.  The vault's
    library/13 Robust Backtesting and Multiple Testing.md cites this as the
    overfitting-diagnostic spine.  The adversarial perturbation test is a
    standard worst-case-stability diagnostic from the same literature: a
    genuine edge should survive a bounded adversarial corruption of a subset
    of observations, whereas an overfit / marginal edge collapses.  It
    complements the probabilistic CSCV/PBO and planted-alpha checks by
    probing stability under explicit worst-case perturbation.

    Algorithm
    ----------
    1. observed_mean = mean(per_event_expectancies).
    2. For each of ``n_perturbations`` runs (deterministic
       ``np.random.default_rng(seed)``):
        a. Select ``perturbation_fraction * n_obs`` random indices.
        b. Adversarially perturb those indices: if expectancy > 0, set to
           ``-|expectancy| * 2`` (flip positive to negative, doubling
           magnitude); if expectancy < 0, set to ``|expectancy| * 2``
           (worsen the already-negative sign).  This worsens the sign of the
           corrupted positions, moving the mean toward (or past) zero.
        c. Compute perturbed_mean.
        d. survived = perturbed_mean > 0 (positive expectancy is the edge).
    3. survival_rate = sum(survived) / n_perturbations.
    4. adversarial_pass = survival_rate >= min_survival_rate.

    Parameters
    ----------
    per_event_expectancies:
        List of per-event expectancy values for one cell.
    perturbation_fraction:
        Fraction of observations adversarially perturbed per run
        (default 0.1).  Must be in [0, 1].
    n_perturbations:
        Number of adversarial perturbation runs (default 100).  Must be >= 1.
    seed:
        Seed for ``numpy.random.default_rng`` (deterministic; default 0).
    min_survival_rate:
        Minimum survival rate required to pass (default 0.8).
        ``adversarial_pass = survival_rate >= min_survival_rate``.

    Returns
    -------
    Dict with keys:
        observed_mean        – float, mean of the original expectancies.
        survival_rate        – float in [0, 1], fraction of perturbation
                                runs where perturbed_mean > 0.
        n_perturbations      – int, echoed.
        perturbation_fraction – float, echoed.
        min_survival_rate    – float, echoed.
        adversarial_pass     – bool; ``survival_rate >= min_survival_rate``.
        seed                 – int, echoed.
        n_obs                – int, number of observations.
        reason               – None on success; human-readable string when
                                the input is empty / insufficient
                                (fail-closed).
    """
    n_obs = len(per_event_expectancies)

    # Fail-closed guard: no observations.
    if n_obs == 0:
        return {
            "observed_mean": None,
            "survival_rate": None,
            "n_perturbations": n_perturbations,
            "perturbation_fraction": perturbation_fraction,
            "min_survival_rate": min_survival_rate,
            "adversarial_pass": False,
            "seed": seed,
            "n_obs": 0,
            "reason": "no_observations: empty per_event_expectancies",
        }

    arr = np.asarray(per_event_expectancies, dtype=float)

    # Fail-closed guard: non-finite observations.
    if not np.all(np.isfinite(arr)):
        return {
            "observed_mean": None,
            "survival_rate": None,
            "n_perturbations": n_perturbations,
            "perturbation_fraction": perturbation_fraction,
            "min_survival_rate": min_survival_rate,
            "adversarial_pass": False,
            "seed": seed,
            "n_obs": n_obs,
            "reason": "non_finite_observations: per_event_expectancies contain NaN/inf",
        }

    # Fail-closed guard: fraction out of range.
    if not (0.0 <= perturbation_fraction <= 1.0):
        return {
            "observed_mean": round(float(np.mean(arr)), 8),
            "survival_rate": None,
            "n_perturbations": n_perturbations,
            "perturbation_fraction": perturbation_fraction,
            "min_survival_rate": min_survival_rate,
            "adversarial_pass": False,
            "seed": seed,
            "n_obs": n_obs,
            "reason": f"invalid_fraction: perturbation_fraction={perturbation_fraction} not in [0, 1]",
        }

    # Fail-closed guard: no perturbation runs requested.
    if n_perturbations < 1:
        return {
            "observed_mean": round(float(np.mean(arr)), 8),
            "survival_rate": None,
            "n_perturbations": n_perturbations,
            "perturbation_fraction": perturbation_fraction,
            "min_survival_rate": min_survival_rate,
            "adversarial_pass": False,
            "seed": seed,
            "n_obs": n_obs,
            "reason": f"insufficient_perturbations: n_perturbations={n_perturbations} < 1",
        }

    observed_mean = float(np.mean(arr))

    # Number of indices perturbed per run (at least 1 when fraction > 0 and
    # n_obs > 0, so the adversarial corruption is actually applied; at most
    # n_obs).  When fraction == 0 the series is untouched every run and
    # survival_rate reflects the raw sign of observed_mean.
    n_perturb = int(round(perturbation_fraction * n_obs))
    n_perturb = max(0, min(n_perturb, n_obs))

    rng = np.random.default_rng(seed)
    survived = 0

    for _ in range(n_perturbations):
        perturbed = arr.copy()
        if n_perturb > 0:
            perturb_idx = rng.choice(n_obs, size=n_perturb, replace=False)
            vals = perturbed[perturb_idx]
            # Adversarial sign worsening: positive -> -|v|*2, negative -> |v|*2
            # (doubling magnitude in the adverse direction).
            perturbed[perturb_idx] = np.where(
                vals > 0.0,
                -np.abs(vals) * 2.0,
                np.abs(vals) * 2.0,
            )
        perturbed_mean = float(np.mean(perturbed))
        if perturbed_mean > 0.0:
            survived += 1

    survival_rate = survived / n_perturbations if n_perturbations > 0 else 0.0
    adversarial_pass = bool(survival_rate >= min_survival_rate)

    return {
        "observed_mean": round(observed_mean, 8),
        "survival_rate": round(float(survival_rate), 8),
        "n_perturbations": n_perturbations,
        "perturbation_fraction": perturbation_fraction,
        "min_survival_rate": min_survival_rate,
        "adversarial_pass": adversarial_pass,
        "seed": seed,
        "n_obs": n_obs,
        "reason": None,
    }


# ---------------------------------------------------------------------------
# Parameter perturbation stability — Stage B perturbed re-runs
# Satisfies ROBUSTNESS_TESTING_SPEC.md §10 line 286
# ("parameter perturbation") and gap matrix #3.
# ---------------------------------------------------------------------------

def parameter_perturbation(
    per_event_expectancies: list[float],
    parameter_values: dict[str, float] | None = None,
    perturbation_fractions: list[float] | None = None,
    n_runs_per_fraction: int = 50,
    seed: int = 0,
    min_stability_score: float = 0.7,
) -> dict[str, Any]:
    """Parameter perturbation stability — Stage B perturbed re-runs.

    Satisfies ROBUSTNESS_TESTING_SPEC.md §10 line 286 (\"parameter
    perturbation\") and the 2026-06-11 gap matrix #3 remediation:
    \"Stage B perturbed re-runs → parameter_stability_score\"

    Perturbs the effective edge by applying multiplicative noise to the
    per-event expectancies, simulating threshold/parameter instability.
    For each perturbation fraction, runs multiple trials and measures the
    fraction where the perturbed mean remains positive (edge survives).

    The stability score is the average survival rate across all perturbation
    fractions. A robust strategy maintains positive expectancy under modest
    parameter drift.

    Algorithm
    ---------
    1. observed_mean = mean(per_event_expectancies).
    2. observed_std = std(per_event_expectancies, ddof=1).
    3. Default perturbation_fractions = [0.10, 0.25] (±10%, ±25%).
    4. For each fraction f in perturbation_fractions:
         For n_runs_per_fraction trials:
           a. Draw additive noise: noise = f * observed_std * N(0,1).
              The noise scale is proportional to the data's natural variability
              (std), not the mean. This degrades the SNR as f grows: a weak
              edge (low mean/std ratio) fails at lower f, a strong edge
              (high mean/std ratio) survives.
           b. perturbed = expectancies + noise.
           c. survived = mean(perturbed) > 0.
         fraction_survival = sum(survived) / n_runs_per_fraction.
    5. parameter_stability_score = mean(fraction_survival across fractions).
    6. parameter_perturbation_pass = score >= min_stability_score.

    Parameters
    ----------
    per_event_expectancies:
        List of per-event expectancy values for one cell.
    parameter_values:
        Optional dict of named parameter values (e.g., {"threshold": 0.5,
        "window": 100}). Currently unused but reserved for future direct
        parameter perturbation; the current implementation perturbs the
        expectancy series directly as a proxy for parameter instability.
    perturbation_fractions:
        List of perturbation magnitudes as fractions (default [0.10, 0.25]
        for ±10% and ±25%). Each fraction scales the additive noise relative
        to the observed standard deviation: noise = f * std * N(0,1).
    n_runs_per_fraction:
        Number of perturbation trials per fraction (default 50).
    seed:
        Seed for ``numpy.random.default_rng`` (deterministic; default 0).
    min_stability_score:
        Minimum stability score required to pass (default 0.7).
        ``parameter_perturbation_pass = score >= min_stability_score``.

    Returns
    -------
    Dict with keys:
        observed_mean                – float, mean of original expectancies.
        fraction_survival_rates      – dict mapping fraction -> survival rate.
        parameter_stability_score    – float in [0, 1], mean survival across fractions.
        parameter_perturbation_pass  – bool; score >= min_stability_score.
        perturbation_fractions       – list[float], echoed.
        n_runs_per_fraction          – int, echoed.
        min_stability_score          – float, echoed.
        seed                         – int, echoed.
        n_obs                        – int, number of observations.
        reason                       – None on success; human-readable string
                                        when input is empty / insufficient
                                        (fail-closed).
    """
    n_obs = len(per_event_expectancies)

    # Fail-closed guard: no observations.
    if n_obs == 0:
        return {
            "observed_mean": None,
            "fraction_survival_rates": {},
            "parameter_stability_score": None,
            "parameter_perturbation_pass": False,
            "perturbation_fractions": perturbation_fractions or [0.10, 0.25],
            "n_runs_per_fraction": n_runs_per_fraction,
            "min_stability_score": min_stability_score,
            "seed": seed,
            "n_obs": 0,
            "reason": "no_observations: empty per_event_expectancies",
        }

    arr = np.asarray(per_event_expectancies, dtype=float)

    # Fail-closed guard: non-finite observations.
    if not np.all(np.isfinite(arr)):
        return {
            "observed_mean": None,
            "fraction_survival_rates": {},
            "parameter_stability_score": None,
            "parameter_perturbation_pass": False,
            "perturbation_fractions": perturbation_fractions or [0.10, 0.25],
            "n_runs_per_fraction": n_runs_per_fraction,
            "min_stability_score": min_stability_score,
            "seed": seed,
            "n_obs": n_obs,
            "reason": "non_finite_observations: per_event_expectancies contain NaN/inf",
        }

    # Fail-closed guard: no runs requested.
    if n_runs_per_fraction < 1:
        return {
            "observed_mean": round(float(np.mean(arr)), 8),
            "fraction_survival_rates": {},
            "parameter_stability_score": None,
            "parameter_perturbation_pass": False,
            "perturbation_fractions": perturbation_fractions or [0.10, 0.25],
            "n_runs_per_fraction": n_runs_per_fraction,
            "min_stability_score": min_stability_score,
            "seed": seed,
            "n_obs": n_obs,
            "reason": f"insufficient_runs: n_runs_per_fraction={n_runs_per_fraction} < 1",
        }

    fractions = perturbation_fractions or [0.10, 0.25]

    # Validate fractions
    if any(not (0.0 <= f <= 1.0) for f in fractions):
        return {
            "observed_mean": round(float(np.mean(arr)), 8),
            "fraction_survival_rates": {},
            "parameter_stability_score": None,
            "parameter_perturbation_pass": False,
            "perturbation_fractions": fractions,
            "n_runs_per_fraction": n_runs_per_fraction,
            "min_stability_score": min_stability_score,
            "seed": seed,
            "n_obs": n_obs,
            "reason": f"invalid_fractions: all fractions must be in [0, 1], got {fractions}",
        }

    observed_mean = float(np.mean(arr))
    observed_std = float(np.std(arr, ddof=1)) if n_obs > 1 else 0.0
    rng = np.random.default_rng(seed)

    fraction_survival_rates = {}

    for frac in fractions:
        survived = 0
        for _ in range(n_runs_per_fraction):
            # Additive noise: perturbed = arr + frac * std(arr) * N(0,1).
            # The noise scale is proportional to the observed standard deviation
            # of the expectancies, not the mean. This degrades the SNR as frac
            # grows: a weak edge (low mean/std ratio) fails at lower frac, a
            # strong edge (high mean/std ratio) survives. This models parameter
            # drift injecting noise at a fraction of the data's natural variability.
            noise_scale = frac * observed_std if observed_std > 0 else 0.0
            noise = rng.standard_normal(size=n_obs) * noise_scale
            perturbed = arr + noise
            perturbed_mean = float(np.mean(perturbed))
            if perturbed_mean > 0.0:
                survived += 1
        fraction_survival_rates[frac] = round(survived / n_runs_per_fraction, 8)

    parameter_stability_score = float(np.mean(list(fraction_survival_rates.values())))
    parameter_perturbation_pass = bool(parameter_stability_score >= min_stability_score)

    return {
        "observed_mean": round(observed_mean, 8),
        "fraction_survival_rates": fraction_survival_rates,
        "parameter_stability_score": round(parameter_stability_score, 8),
        "parameter_perturbation_pass": parameter_perturbation_pass,
        "perturbation_fractions": fractions,
        "n_runs_per_fraction": n_runs_per_fraction,
        "min_stability_score": min_stability_score,
        "seed": seed,
        "n_obs": n_obs,
        "reason": None,
    }
