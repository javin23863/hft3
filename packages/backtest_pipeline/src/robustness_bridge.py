"""VBT-4 robustness integration bridge.

Bridges between VectorBT candidate metrics and the existing robustness
producers (DSR, PBO/CSCV, bootstrap CI, WFC gate).  The bridge is a *consumer*
of pre-computed raw input data supplied by the caller (per-event expectancies,
CSCV matrix, WFC fold rows) and calls the existing producers to populate the
screening-artifact robustness fields.

Per VECTORBT_SCREENING_ENGINE_SPEC.md line 440-442: "VectorBT does not compute
DSR/PBO/CSCV or surface formulas in this bridge; it only consumes
already-produced robustness evidence."  This module calls the producers only
when the raw input data is supplied by the caller — it does not derive that
data itself.

Authority: docs/project/ROBUSTNESS_TESTING_SPEC.md (DSR/PBO/CSCV/WFC sections).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional

import numpy as np

from research_pipeline.src.robustness_producers import (
    adversarial_perturbation,
    bootstrap_ci,
    cscv_pbo,
    deflated_sharpe_for_cell,
    fee_stress_for_cell,
    holm_bh_correction,
    latency_stress_for_cell,
    null_strategy_battery,
    parameter_perturbation,
    planted_alpha_synthetic_control,
    slippage_stress_for_cell,
)
from workbench.src.robustness.wfc.gate import evaluate_wfc_gate

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fail-closed sentinels
# ---------------------------------------------------------------------------

_NOT_RUN = "not_run"
_PASS = "pass"
_FAIL = "fail"
_FRESH = "fresh"
_STALE = "stale"


def _producer_error(detail: str) -> Dict[str, Any]:
    return {"status": _NOT_RUN, "reason": f"producer_error: {detail}"}


def _not_run_sentinel() -> Dict[str, Any]:
    return {"status": _NOT_RUN, "reason": "robustness_input_missing"}


def _all_not_run_output(candidate_id: str = "") -> Dict[str, Any]:
    """Return the full fail-closed output when robustness input is missing.

    Must include every key that ``compute_robustness_evidence`` returns on the
    normal path so downstream consumers never see KeyError on fail-closed.
    """
    sentinel = _not_run_sentinel()
    return {
        "wfc_status": _NOT_RUN,
        "dsr_status": _NOT_RUN,
        "pbo_status": _NOT_RUN,
        "cscv_status": _NOT_RUN,
        "robustness_artifact_staleness": _STALE,
        "bootstrap_ci_or_not_run": dict(sentinel),
        "dsr_or_not_run": dict(sentinel),
        "pbo_or_not_run": dict(sentinel),
        "cscv_count_or_not_run": dict(sentinel),
        "fee_stress_or_not_run": dict(sentinel),
        "slippage_stress_or_not_run": dict(sentinel),
        "latency_stress_or_not_run": dict(sentinel),
        "holm_bh_or_not_run": dict(sentinel),
        "null_battery_or_not_run": dict(sentinel),
        "planted_alpha_or_not_run": dict(sentinel),
        "adversarial_or_not_run": dict(sentinel),
        "parameter_perturbation_or_not_run": dict(sentinel),
        "walk_forward_metrics": {
            "status": _NOT_RUN,
            "fold_matrix": [],
            "fold_train_test_dates": [],
            "fold_metrics": [],
            "walk_forward_efficiency": None,
            "fold_dispersion": None,
            "is_oos_gap": None,
            "oos_decay": None,
        },
        "wfc_metrics": {
            "status": _NOT_RUN,
            "metric_in_sample": [],
            "metric_out_of_sample": [],
            "pearson": None,
            "spearman": None,
            "scatter_data": [],
            "quadrant_counts": {},
            "high_is_high_oos_region": {},
            "rejection_reason": "robustness_input_missing",
        },
        "candidate_id": candidate_id,
    }


# ---------------------------------------------------------------------------
# Individual producer wrappers (try/except isolated)
# ---------------------------------------------------------------------------


def _run_dsr(
    per_event_expectancies: List[float],
    n_trials: int,
) -> Dict[str, Any]:
    try:
        result = deflated_sharpe_for_cell(per_event_expectancies, n_trials)
        dsr_pass = bool(result.get("dsr_pass"))
        status = _PASS if dsr_pass else _FAIL
        return {
            "status": status,
            "dsr_pass": dsr_pass,
            "dsr_cdf": result.get("dsr_cdf"),
            "sharpe": result.get("sharpe"),
            "one_sided_p": result.get("one_sided_p"),
            "n_obs": result.get("n_obs"),
            "n_trials": result.get("n_trials"),
            "skew": result.get("skew"),
            "kurt": result.get("kurt"),
            "reason": result.get("reason"),
        }
    except Exception as exc:  # noqa: BLE001 — fail-closed isolation
        logger.warning("DSR producer failed: %s", exc)
        return _producer_error(str(exc))


def _run_bootstrap_ci(
    per_event_expectancies: List[float],
) -> Dict[str, Any]:
    try:
        result = bootstrap_ci(per_event_expectancies)
        ci_lo = result.get("ci_lo_95")
        ci_hi = result.get("ci_hi_95")
        # Bootstrap passes if the CI lower bound is positive (mean > 0 at 95%).
        status = _PASS if (ci_lo is not None and ci_lo > 0) else _FAIL
        return {
            "status": status,
            "lower": ci_lo,
            "upper": ci_hi,
            "mean": result.get("mean"),
            "ci_lo_95": ci_lo,
            "ci_hi_95": ci_hi,
            "n": result.get("n"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("bootstrap_ci producer failed: %s", exc)
        return _producer_error(str(exc))


def _run_cscv_pbo(
    matrix: np.ndarray,
    n_splits: int = 8,
) -> Dict[str, Any]:
    try:
        result = cscv_pbo(matrix, n_splits=n_splits)
        pbo_val = result.get("pbo")
        # PBO pass: pbo is not None and < 0.5 (threshold from spec).
        pbo_pass = (
            pbo_val is not None
            and not (isinstance(pbo_val, float) and np.isnan(pbo_val))
            and pbo_val < 0.5
        )
        maximum_pbo = 0.5
        status = _PASS if pbo_pass else _FAIL
        return {
            "status": status,
            "pbo_pass": pbo_pass,
            "pbo": pbo_val,
            "maximum_pbo": maximum_pbo,
            "n_splits": result.get("n_splits"),
            "n_configs": result.get("n_configs"),
            "n_partitions": result.get("n_partitions"),
            "n_excluded": result.get("n_excluded"),
            "reason": result.get("reason"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("CSCV/PBO producer failed: %s", exc)
        return _producer_error(str(exc))


def _run_fee_stress(
    per_event_expectancies: List[float],
    per_event_n_trades: List[int],
    per_event_fee_per_rt: List[float],
    per_event_tick_value: List[float],
) -> Dict[str, Any]:
    try:
        result = fee_stress_for_cell(
            per_event_expectancies, per_event_n_trades,
            per_event_fee_per_rt, per_event_tick_value
        )
        stress_pass = result.get("stress_pass")
        status = _PASS if stress_pass is True else (_FAIL if stress_pass is False else _NOT_RUN)
        return {"status": status, **result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("fee_stress producer failed: %s", exc)
        return _producer_error(str(exc))


def _run_slippage_stress(
    per_event_expectancies: List[float],
    per_event_n_trades: List[int],
    per_event_fee_per_rt: List[float],
    per_event_tick_value: List[float],
) -> Dict[str, Any]:
    try:
        result = slippage_stress_for_cell(
            per_event_expectancies, per_event_n_trades,
            per_event_fee_per_rt, per_event_tick_value
        )
        stress_pass = result.get("stress_pass")
        status = _PASS if stress_pass is True else (_FAIL if stress_pass is False else _NOT_RUN)
        return {"status": status, **result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("slippage_stress producer failed: %s", exc)
        return _producer_error(str(exc))


def _run_latency_stress(
    per_event_expectancies: List[float],
    per_event_n_trades: List[int],
    per_event_fee_per_rt: List[float],
    per_event_tick_value: List[float],
) -> Dict[str, Any]:
    try:
        result = latency_stress_for_cell(
            per_event_expectancies, per_event_n_trades,
            per_event_fee_per_rt, per_event_tick_value
        )
        stress_pass = result.get("stress_pass")
        status = _PASS if stress_pass is True else (_FAIL if stress_pass is False else _NOT_RUN)
        return {"status": status, **result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("latency_stress producer failed: %s", exc)
        return _producer_error(str(exc))


def _run_holm_bh(
    p_values: List[float],
    alpha: float = 0.05,
    method: str = "bh",
) -> Dict[str, Any]:
    try:
        result = holm_bh_correction(p_values, alpha=alpha, method=method)
        reason = result.get("reason")
        n_rejected = result.get("n_rejected")
        # Per Codex P2-3: pass requires reason is None AND n_rejected > 0.
        # Zero rejections = no surviving hypothesis = fail.
        if reason is not None:
            status = _FAIL
        elif n_rejected is not None and n_rejected > 0:
            status = _PASS
        else:
            status = _FAIL
        return {"status": status, **result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("holm_bh_correction producer failed: %s", exc)
        return _producer_error(str(exc))


def _run_null_battery(
    per_event_expectancies: List[float],
    n_null_runs: int = 1000,
    seed: int = 0,
    min_p_value: float = 0.05,
) -> Dict[str, Any]:
    try:
        result = null_strategy_battery(
            per_event_expectancies, n_null_runs=n_null_runs,
            seed=seed, min_p_value=min_p_value
        )
        null_pass = result.get("null_pass")
        status = _PASS if null_pass is True else (_FAIL if null_pass is False else _NOT_RUN)
        return {"status": status, **result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("null_strategy_battery producer failed: %s", exc)
        return _producer_error(str(exc))


def _run_planted_alpha(
    per_event_expectancies: List[float],
    n_planted: int = 100,
    alpha_strength: float = 0.01,
    seed: int = 0,
    min_p_value: float = 0.05,
) -> Dict[str, Any]:
    try:
        result = planted_alpha_synthetic_control(
            per_event_expectancies, n_planted=n_planted,
            alpha_strength=alpha_strength, seed=seed, min_p_value=min_p_value
        )
        planted_pass = result.get("planted_pass")
        status = _PASS if planted_pass is True else (_FAIL if planted_pass is False else _NOT_RUN)
        return {"status": status, **result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("planted_alpha_synthetic_control producer failed: %s", exc)
        return _producer_error(str(exc))


def _run_adversarial(
    per_event_expectancies: List[float],
    perturbation_fraction: float = 0.1,
    n_perturbations: int = 100,
    seed: int = 0,
    min_survival_rate: float = 0.8,
) -> Dict[str, Any]:
    try:
        result = adversarial_perturbation(
            per_event_expectancies, perturbation_fraction=perturbation_fraction,
            n_perturbations=n_perturbations, seed=seed,
            min_survival_rate=min_survival_rate
        )
        adversarial_pass = result.get("adversarial_pass")
        status = _PASS if adversarial_pass is True else (_FAIL if adversarial_pass is False else _NOT_RUN)
        return {"status": status, **result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("adversarial_perturbation producer failed: %s", exc)
        return _producer_error(str(exc))


def _run_parameter_perturbation(
    per_event_expectancies: List[float],
    perturbation_fractions: List[float] | None = None,
    n_runs_per_fraction: int = 50,
    min_stability_score: float = 0.7,
    seed: int = 0,
) -> Dict[str, Any]:
    try:
        result = parameter_perturbation(
            per_event_expectancies,
            parameter_values=None,
            perturbation_fractions=perturbation_fractions,
            n_runs_per_fraction=n_runs_per_fraction,
            seed=seed,
            min_stability_score=min_stability_score,
        )
        param_pass = result.get("parameter_perturbation_pass")
        status = _PASS if param_pass is True else (_FAIL if param_pass is False else _NOT_RUN)
        return {"status": status, **result}
    except Exception as exc:  # noqa: BLE001
        logger.warning("parameter_perturbation producer failed: %s", exc)
        return _producer_error(str(exc))


def _run_wfc_gate(
    rows: List[Dict[str, Any]],
    cfg: Dict[str, Any],
    candidate_id: str = "",
) -> Dict[str, Any]:
    try:
        wfc_result = evaluate_wfc_gate(
            rows,
            cfg,
            run_id=candidate_id,
            model_id=candidate_id,
            strategy_id=candidate_id,
        )
        wfc_dict = wfc_result.to_dict()
        raw_status = str(wfc_dict.get("wfc_status", "ERROR")).upper().strip()
        # Only PASS counts as pass. CONDITIONAL is fail.
        status = _PASS if raw_status == "PASS" else _FAIL
        # Build the wfc_metrics dict with required keys.
        wfc_metrics = {
            "status": status,
            "metric_in_sample": wfc_dict.get("metric_in_sample", []),
            "metric_out_of_sample": wfc_dict.get("metric_out_of_sample", []),
            "pearson": wfc_dict.get("pearson"),
            "spearman": wfc_dict.get("spearman"),
            "scatter_data": wfc_dict.get("scatter_data", []),
            "quadrant_counts": wfc_dict.get("quadrant_counts", {}),
            "high_is_high_oos_region": wfc_dict.get("high_is_high_oos_region", {}),
            "rejection_reason": "; ".join(wfc_dict.get("rejection_reasons", []))
            if wfc_dict.get("rejection_reasons")
            else "not_rejected",
            "wfc_raw_status": raw_status,
            "n_parameter_combinations": wfc_dict.get("n_parameter_combinations", 0),
            "n_folds": wfc_dict.get("n_folds", 0),
            "positive_fold_ratio": wfc_dict.get("positive_fold_ratio", 0.0),
            "p_value": wfc_dict.get("p_value", 1.0),
            "outlier_sensitivity_pass": wfc_dict.get("outlier_sensitivity_pass", False),
            "cost_adjusted_pass": wfc_dict.get("cost_adjusted_pass", False),
            "drawdown_pass": wfc_dict.get("drawdown_pass", False),
        }
        return {
            "status": status,
            "wfc_status": status,
            "wfc_raw_status": raw_status,
            "wfc_metrics": wfc_metrics,
            "wfc_result": wfc_dict,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("WFC gate failed: %s", exc)
        return {
            "status": _FAIL,
            "wfc_status": _FAIL,
            "wfc_raw_status": "ERROR",
            "wfc_metrics": {
                "status": _FAIL,
                "metric_in_sample": [],
                "metric_out_of_sample": [],
                "pearson": None,
                "spearman": None,
                "scatter_data": [],
                "quadrant_counts": {},
                "high_is_high_oos_region": {},
                "rejection_reason": f"producer_error: {exc}",
            },
            "wfc_result": {},
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# Walk-forward metrics builder
# ---------------------------------------------------------------------------


def _build_walk_forward_metrics(
    wfc_rows: List[Dict[str, Any]],
    wfc_result_dict: Dict[str, Any],
) -> Dict[str, Any]:
    """Build the walk_forward_metrics dict from WFC fold rows.

    Extracts fold-level IS/OOS metrics, train/test date boundaries, and
    computes walk-forward efficiency / dispersion / OOS decay from the
    fold-level data when available.
    """
    fold_matrix: List[List[Any]] = []
    fold_train_test_dates: List[Dict[str, Any]] = []
    fold_metrics: List[Dict[str, Any]] = []

    # Group rows by fold_id to build per-fold aggregates.
    by_fold: Dict[str, List[Dict[str, Any]]] = {}
    for row in wfc_rows:
        fid = str(row.get("fold_id", "all"))
        by_fold.setdefault(fid, []).append(row)

    fold_sharpes_is: List[float] = []
    fold_sharpes_oos: List[float] = []

    for fid in sorted(by_fold.keys()):
        fold_rows = by_fold[fid]
        is_metrics = [r.get("is_metrics", {}) for r in fold_rows]
        oos_metrics = [r.get("oos_metrics", {}) for r in fold_rows]

        # Aggregate IS/OOS sharpe for this fold (mean across parameter combos).
        is_sharpe_vals = [
            float(m.get("sharpe", 0.0)) for m in is_metrics if isinstance(m, dict)
        ]
        oos_sharpe_vals = [
            float(m.get("sharpe", 0.0)) for m in oos_metrics if isinstance(m, dict)
        ]
        is_sharpe = float(np.mean(is_sharpe_vals)) if is_sharpe_vals else 0.0
        oos_sharpe = float(np.mean(oos_sharpe_vals)) if oos_sharpe_vals else 0.0
        fold_sharpes_is.append(is_sharpe)
        fold_sharpes_oos.append(oos_sharpe)

        fold_matrix.append([fid, is_sharpe, oos_sharpe])
        fold_metrics.append({"fold_id": fid, "is_sharpe": is_sharpe, "oos_sharpe": oos_sharpe})

        # Extract train/test dates if available in the row.
        train_dates = fold_rows[0].get("train_dates") if fold_rows else None
        test_dates = fold_rows[0].get("test_dates") if fold_rows else None
        fold_train_test_dates.append({
            "train": train_dates or [],
            "test": test_dates or [],
        })

    # Walk-forward efficiency: mean(OOS) / mean(IS) if mean(IS) > 0.
    mean_is = float(np.mean(fold_sharpes_is)) if fold_sharpes_is else 0.0
    mean_oos = float(np.mean(fold_sharpes_oos)) if fold_sharpes_oos else 0.0
    walk_forward_efficiency = mean_oos / mean_is if abs(mean_is) > 1e-15 else 0.0

    # Fold dispersion: std of OOS sharpes across folds.
    fold_dispersion = float(np.std(fold_sharpes_oos, ddof=1)) if len(fold_sharpes_oos) > 1 else 0.0

    # IS-OOS gap: mean(IS) - mean(OOS).
    is_oos_gap = mean_is - mean_oos

    # OOS decay: ratio of last fold OOS to first fold OOS (decay over time).
    oos_decay = 0.0
    if len(fold_sharpes_oos) >= 2 and abs(fold_sharpes_oos[0]) > 1e-15:
        oos_decay = fold_sharpes_oos[-1] / fold_sharpes_oos[0]

    return {
        "status": _PASS,
        "fold_matrix": fold_matrix,
        "fold_train_test_dates": fold_train_test_dates,
        "fold_metrics": fold_metrics,
        "walk_forward_efficiency": round(walk_forward_efficiency, 8),
        "fold_dispersion": round(fold_dispersion, 8),
        "is_oos_gap": round(is_oos_gap, 8),
        "oos_decay": round(oos_decay, 8),
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def compute_robustness_evidence(robustness_input: dict, candidate_id: str = "") -> dict:
    """Given robustness raw input data, call producers and return artifact fields.

    Input dict keys:
    - per_event_expectancies: list[float]  (for DSR + bootstrap + null battery + planted alpha + adversarial + parameter perturbation)
    - n_trials: int  (DSR multiplicity denominator)
    - cscv_matrix: np.ndarray  (n_blocks x n_configs, for PBO/CSCV)
    - wfc_rows: list[dict]  (fold rows for WFC gate)
    - wfc_cfg: dict  (WFC gate config, optional)
    - per_event_n_trades: list[int]  (for fee/slippage/latency stress)
    - per_event_fee_per_rt: list[float]  (fee per round trip USD)
    - per_event_tick_value: list[float]  (tick value USD per contract)
    - p_values: list[float]  (for Holm/BH correction)
    - holm_bh_alpha: float  (optional, default 0.05)
    - holm_bh_method: str  (optional, "bh" or "holm", default "bh")
    - perturbation_fractions: list[float]  (optional, for parameter perturbation, default [0.10, 0.25])
    - n_runs_per_fraction: int  (optional, for parameter perturbation, default 50)
    - min_stability_score: float  (optional, for parameter perturbation, default 0.7)

    Returns dict with keys matching the screening artifact fields:
    - wfc_status, dsr_status, pbo_status, cscv_status
    - robustness_artifact_staleness (str: "fresh" or "stale")
    - bootstrap_ci_or_not_run, dsr_or_not_run, pbo_or_not_run, cscv_count_or_not_run
    - fee_stress_or_not_run, slippage_stress_or_not_run, latency_stress_or_not_run
    - holm_bh_or_not_run, null_battery_or_not_run, planted_alpha_or_not_run, adversarial_or_not_run
    - parameter_perturbation_or_not_run
    - walk_forward_metrics (dict with fold_matrix, fold_train_test_dates, etc. if available)
    - wfc_metrics (dict with pearson, spearman, quadrant_counts, etc.)
    """
    # Fail-closed: missing/empty input.
    if not robustness_input or not isinstance(robustness_input, Mapping):
        return _all_not_run_output(candidate_id)

    per_event_expectancies = robustness_input.get("per_event_expectancies")
    n_trials = robustness_input.get("n_trials", 1)
    cscv_matrix = robustness_input.get("cscv_matrix")
    wfc_rows = robustness_input.get("wfc_rows")
    wfc_cfg = robustness_input.get("wfc_cfg") or {}
    # New §10 robustness producer inputs
    per_event_n_trades = robustness_input.get("per_event_n_trades")
    per_event_fee_per_rt = robustness_input.get("per_event_fee_per_rt")
    per_event_tick_value = robustness_input.get("per_event_tick_value")
    p_values = robustness_input.get("p_values")
    holm_bh_alpha = robustness_input.get("holm_bh_alpha", 0.05)
    holm_bh_method = robustness_input.get("holm_bh_method", "bh")
    # Parameter perturbation inputs
    perturbation_fractions = robustness_input.get("perturbation_fractions")
    n_runs_per_fraction = robustness_input.get("n_runs_per_fraction", 50)
    min_stability_score = robustness_input.get("min_stability_score", 0.7)
    perturbation_seed = robustness_input.get("perturbation_seed", 0)

    has_expectancies = (
        isinstance(per_event_expectancies, list) and len(per_event_expectancies) > 0
    )
    has_matrix = cscv_matrix is not None
    has_wfc = isinstance(wfc_rows, list) and len(wfc_rows) > 0
    has_stress_decomposition = (
        isinstance(per_event_n_trades, list) and len(per_event_n_trades) > 0 and
        isinstance(per_event_fee_per_rt, list) and len(per_event_fee_per_rt) > 0 and
        isinstance(per_event_tick_value, list) and len(per_event_tick_value) > 0
    )
    # Per Codex P2-6: verify decomposition arrays match event count to prevent
    # NumPy broadcasting a single-item list across all events.
    if has_stress_decomposition and has_expectancies:
        _n_exp = len(per_event_expectancies)
        for _arr_name, _arr in (
            ("per_event_n_trades", per_event_n_trades),
            ("per_event_fee_per_rt", per_event_fee_per_rt),
            ("per_event_tick_value", per_event_tick_value),
        ):
            if len(_arr) != _n_exp:
                has_stress_decomposition = False
                logger.warning(
                    "stress decomposition length mismatch: %s has %d elements, "
                    "expected %d (expectancies count)",
                    _arr_name, len(_arr), _n_exp,
                )
                break
    has_p_values = isinstance(p_values, list) and len(p_values) > 0

    # If absolutely no input data, return all not_run.
    if not has_expectancies and not has_matrix and not has_wfc and not has_stress_decomposition and not has_p_values:
        return _all_not_run_output(candidate_id)

    # Run each producer in isolation.
    dsr_result = _not_run_sentinel()
    if has_expectancies:
        dsr_result = _run_dsr(per_event_expectancies, int(n_trials) if n_trials else 1)

    bootstrap_result = _not_run_sentinel()
    if has_expectancies:
        bootstrap_result = _run_bootstrap_ci(per_event_expectancies)

    fee_stress_result = _not_run_sentinel()
    if has_expectancies and has_stress_decomposition:
        fee_stress_result = _run_fee_stress(
            per_event_expectancies, per_event_n_trades,
            per_event_fee_per_rt, per_event_tick_value
        )

    slippage_stress_result = _not_run_sentinel()
    if has_expectancies and has_stress_decomposition:
        slippage_stress_result = _run_slippage_stress(
            per_event_expectancies, per_event_n_trades,
            per_event_fee_per_rt, per_event_tick_value
        )

    latency_stress_result = _not_run_sentinel()
    if has_expectancies and has_stress_decomposition:
        latency_stress_result = _run_latency_stress(
            per_event_expectancies, per_event_n_trades,
            per_event_fee_per_rt, per_event_tick_value
        )

    holm_bh_result = _not_run_sentinel()
    if has_p_values:
        holm_bh_result = _run_holm_bh(p_values, alpha=holm_bh_alpha, method=holm_bh_method)

    null_battery_result = _not_run_sentinel()
    if has_expectancies:
        null_battery_result = _run_null_battery(per_event_expectancies)

    planted_alpha_result = _not_run_sentinel()
    if has_expectancies:
        planted_alpha_result = _run_planted_alpha(per_event_expectancies)

    adversarial_result = _not_run_sentinel()
    if has_expectancies:
        adversarial_result = _run_adversarial(per_event_expectancies)

    param_perturb_result = _not_run_sentinel()
    if has_expectancies:
        param_perturb_result = _run_parameter_perturbation(
            per_event_expectancies,
            perturbation_fractions=perturbation_fractions,
            n_runs_per_fraction=n_runs_per_fraction,
            min_stability_score=min_stability_score,
            seed=perturbation_seed,
        )

    pbo_result = _not_run_sentinel()
    if has_matrix:
        pbo_result = _run_cscv_pbo(cscv_matrix)

    wfc_result = _not_run_sentinel()
    wfc_metrics_dict: Dict[str, Any] = {
        "status": _NOT_RUN,
        "metric_in_sample": [],
        "metric_out_of_sample": [],
        "pearson": None,
        "spearman": None,
        "scatter_data": [],
        "quadrant_counts": {},
        "high_is_high_oos_region": {},
        "rejection_reason": "wfc_rows_not_provided",
    }
    walk_forward_metrics_dict: Dict[str, Any] = {
        "status": _NOT_RUN,
        "fold_matrix": [],
        "fold_train_test_dates": [],
        "fold_metrics": [],
        "walk_forward_efficiency": None,
        "fold_dispersion": None,
        "is_oos_gap": None,
        "oos_decay": None,
    }
    if has_wfc:
        wfc_result = _run_wfc_gate(wfc_rows, dict(wfc_cfg), candidate_id)
        wfc_metrics_dict = wfc_result.get("wfc_metrics", wfc_metrics_dict)
        # Build walk-forward metrics from the WFC rows.
        wfc_result_dict = wfc_result.get("wfc_result", {})
        walk_forward_metrics_dict = _build_walk_forward_metrics(wfc_rows, wfc_result_dict)

    # Determine status fields.
    dsr_status = dsr_result.get("status", _NOT_RUN)
    pbo_status = pbo_result.get("status", _NOT_RUN)
    fee_stress_pass = fee_stress_result.get("stress_pass")
    fee_stress_status = _PASS if (fee_stress_pass is True) else (_FAIL if (fee_stress_pass is False) else _NOT_RUN)
    slippage_stress_pass = slippage_stress_result.get("stress_pass")
    slippage_stress_status = _PASS if (slippage_stress_pass is True) else (_FAIL if (slippage_stress_pass is False) else _NOT_RUN)
    latency_stress_pass = latency_stress_result.get("stress_pass")
    latency_stress_status = _PASS if (latency_stress_pass is True) else (_FAIL if (latency_stress_pass is False) else _NOT_RUN)
    holm_bh_n_rejected = holm_bh_result.get("n_rejected")
    holm_bh_reason = holm_bh_result.get("reason")
    # Per Codex P2-7: Holm/BH "pass" requires the correction ran (no error)
    # AND at least one hypothesis survived alpha (n_rejected > 0). If every
    # hypothesis was rejected (n_rejected == 0), the family-level correction
    # found no surviving strategy — the candidate should not get fresh evidence.
    if not has_p_values:
        holm_bh_status = _NOT_RUN
    elif holm_bh_reason is not None:
        holm_bh_status = _FAIL
    elif holm_bh_n_rejected is not None and holm_bh_n_rejected > 0:
        holm_bh_status = _PASS
    else:
        holm_bh_status = _FAIL
    null_battery_pass = null_battery_result.get("null_pass")
    null_battery_status = _PASS if (null_battery_pass is True) else (_FAIL if (null_battery_pass is False) else _NOT_RUN)
    planted_alpha_pass = planted_alpha_result.get("planted_pass")
    planted_alpha_status = _PASS if (planted_alpha_pass is True) else (_FAIL if (planted_alpha_pass is False) else _NOT_RUN)
    adversarial_pass = adversarial_result.get("adversarial_pass")
    adversarial_status = _PASS if (adversarial_pass is True) else (_FAIL if (adversarial_pass is False) else _NOT_RUN)
    param_perturb_pass = param_perturb_result.get("parameter_perturbation_pass")
    param_perturb_status = _PASS if (param_perturb_pass is True) else (_FAIL if (param_perturb_pass is False) else _NOT_RUN)
    # Derive cscv_status independently from the CSCV/PBO producer result, using
    # whether the CSCV partition/config analysis actually ran (n_partitions
    # and n_configs present and > 0).  This is a separate criterion from pbo_status
    # (which reflects the PBO probability test) so that a CSCV that ran with valid
    # structure counts as "pass" even if the PBO probability itself fails.
    cscv_n_partitions = pbo_result.get("n_partitions")
    cscv_n_configs = pbo_result.get("n_configs")
    cscv_status = (
        _PASS
        if (
            isinstance(cscv_n_partitions, (int, float))
            and cscv_n_partitions > 0
            and isinstance(cscv_n_configs, (int, float))
            and cscv_n_configs > 0
        )
        else _NOT_RUN
    )
    wfc_status = wfc_result.get("wfc_status", _NOT_RUN)

    # If a producer wasn't run (no input), set status to not_run.
    if not has_expectancies:
        dsr_status = _NOT_RUN
        fee_stress_status = _NOT_RUN
        slippage_stress_status = _NOT_RUN
        latency_stress_status = _NOT_RUN
        null_battery_status = _NOT_RUN
        planted_alpha_status = _NOT_RUN
        adversarial_status = _NOT_RUN
        param_perturb_status = _NOT_RUN
    if not has_stress_decomposition:
        fee_stress_status = _NOT_RUN
        slippage_stress_status = _NOT_RUN
        latency_stress_status = _NOT_RUN
    if not has_p_values:
        holm_bh_status = _NOT_RUN
    if not has_matrix:
        pbo_status = _NOT_RUN
        cscv_status = _NOT_RUN
    if not has_wfc:
        wfc_status = _NOT_RUN

    # Determine staleness: all gates must pass.
    # Per Codex P1-1 + P2-2 + P2-5 + round-3 P1: When expectancies are provided,
    # ALL §10 producers must produce valid evidence. Fee/slippage/latency
    # require stress decomposition — if decomposition is missing, that's a
    # missing required check → stale. Holm/BH requires p_values. Bootstrap,
    # null, planted, adversarial, parameter all require expectancies.
    # No §10 check is optional when its input is present; missing inputs
    # for required checks also make the artifact stale.
    all_pass = (
        wfc_status == _PASS
        and dsr_status == _PASS
        and pbo_status == _PASS
        and cscv_status == _PASS
        # Fee/slippage/latency: required when expectancies present.
        # If decomposition missing → not_run → stale (not fresh).
        and (
            fee_stress_status == _PASS
            if has_expectancies
            else fee_stress_status in (_PASS, _NOT_RUN)
        )
        and (
            slippage_stress_status == _PASS
            if has_expectancies
            else slippage_stress_status in (_PASS, _NOT_RUN)
        )
        and (
            latency_stress_status == _PASS
            if has_expectancies
            else latency_stress_status in (_PASS, _NOT_RUN)
        )
        and (
            holm_bh_status == _PASS
            if has_p_values
            else holm_bh_status in (_PASS, _NOT_RUN)
        )
        # Bootstrap CI: required §10 check when expectancies present.
        and (
            bootstrap_result.get("status") == _PASS
            if has_expectancies
            else True
        )
        # Null/planted/adversarial/parameter: required when expectancies present.
        and (
            null_battery_status == _PASS
            if has_expectancies
            else null_battery_status in (_PASS, _NOT_RUN)
        )
        and (
            planted_alpha_status == _PASS
            if has_expectancies
            else planted_alpha_status in (_PASS, _NOT_RUN)
        )
        and (
            adversarial_status == _PASS
            if has_expectancies
            else adversarial_status in (_PASS, _NOT_RUN)
        )
        and (
            param_perturb_status == _PASS
            if has_expectancies
            else param_perturb_status in (_PASS, _NOT_RUN)
        )
    )
    staleness = _FRESH if all_pass else _STALE

    # The cscv_count_or_not_run uses the PBO/CSCV result for partition/config counts.
    cscv_count_result = dict(pbo_result) if has_matrix else dict(_not_run_sentinel())

    return {
        "wfc_status": wfc_status,
        "dsr_status": dsr_status,
        "pbo_status": pbo_status,
        "cscv_status": cscv_status,
        "robustness_artifact_staleness": staleness,
        "bootstrap_ci_or_not_run": bootstrap_result,
        "dsr_or_not_run": dsr_result,
        "pbo_or_not_run": pbo_result,
        "cscv_count_or_not_run": cscv_count_result,
        "fee_stress_or_not_run": fee_stress_result,
        "slippage_stress_or_not_run": slippage_stress_result,
        "latency_stress_or_not_run": latency_stress_result,
        "holm_bh_or_not_run": holm_bh_result,
        "null_battery_or_not_run": null_battery_result,
        "planted_alpha_or_not_run": planted_alpha_result,
        "adversarial_or_not_run": adversarial_result,
        "parameter_perturbation_or_not_run": param_perturb_result,
        "walk_forward_metrics": walk_forward_metrics_dict,
        "wfc_metrics": wfc_metrics_dict,
        "candidate_id": candidate_id,
    }