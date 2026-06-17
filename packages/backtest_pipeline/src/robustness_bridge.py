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
    bootstrap_ci,
    cscv_pbo,
    deflated_sharpe_for_cell,
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
    """Return the full fail-closed output when robustness input is missing."""
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
    - per_event_expectancies: list[float]  (for DSR + bootstrap)
    - n_trials: int  (DSR multiplicity denominator)
    - cscv_matrix: np.ndarray  (n_blocks x n_configs, for PBO/CSCV)
    - wfc_rows: list[dict]  (fold rows for WFC gate)
    - wfc_cfg: dict  (WFC gate config, optional)

    Returns dict with keys matching the screening artifact fields:
    - wfc_status, dsr_status, pbo_status, cscv_status
    - robustness_artifact_staleness (str: "fresh" or "stale")
    - bootstrap_ci_or_not_run, dsr_or_not_run, pbo_or_not_run, cscv_count_or_not_run
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

    has_expectancies = (
        isinstance(per_event_expectancies, list) and len(per_event_expectancies) > 0
    )
    has_matrix = cscv_matrix is not None
    has_wfc = isinstance(wfc_rows, list) and len(wfc_rows) > 0

    # If absolutely no input data, return all not_run.
    if not has_expectancies and not has_matrix and not has_wfc:
        return _all_not_run_output(candidate_id)

    # Run each producer in isolation.
    dsr_result = _not_run_sentinel()
    if has_expectancies:
        dsr_result = _run_dsr(per_event_expectancies, int(n_trials) if n_trials else 1)

    bootstrap_result = _not_run_sentinel()
    if has_expectancies:
        bootstrap_result = _run_bootstrap_ci(per_event_expectancies)

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
    if not has_matrix:
        pbo_status = _NOT_RUN
        cscv_status = _NOT_RUN
    if not has_wfc:
        wfc_status = _NOT_RUN

    # Determine staleness: all four gates must pass.
    all_pass = (
        wfc_status == _PASS
        and dsr_status == _PASS
        and pbo_status == _PASS
        and cscv_status == _PASS
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
        "walk_forward_metrics": walk_forward_metrics_dict,
        "wfc_metrics": wfc_metrics_dict,
        "candidate_id": candidate_id,
    }