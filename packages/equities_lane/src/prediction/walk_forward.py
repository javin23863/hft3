"""Walk-forward validation with rare-event metrics for runner prediction.

Implements purged expanding-window walk-forward with embargo, computing
precision@K, PR-AUC, Brier score, calibration error, and expected utility
metrics specific to rare-event forecasting.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

from .features import FEATURE_NAMES
from .hazard_model import HazardModel
from .payoff_heads import PayoffModel
from .risk_heads import RiskModel
from .types import (
    ModelConfig,
    TimingPolicy,
    TimingPolicyReport,
    ValidationMetrics,
)
from .utility import compute_expected_utility

logger = logging.getLogger(__name__)


@dataclass
class WalkForwardFold:
    fold_id: int
    train_indices: np.ndarray
    val_indices: np.ndarray
    test_indices: np.ndarray


def generate_walk_forward_folds(
    n_samples: int,
    dates: np.ndarray,
    config: ModelConfig,
) -> list[WalkForwardFold]:
    unique_dates = np.unique(dates)
    n_dates = len(unique_dates)
    n_folds = config.walk_forward_n_folds
    embargo = config.walk_forward_embargo_days

    fold_size = n_dates // (n_folds + 2)
    folds: list[WalkForwardFold] = []

    for fold_id in range(n_folds):
        train_end_idx = fold_size * (fold_id + 1)
        val_start_idx = train_end_idx + embargo
        val_end_idx = val_start_idx + fold_size
        test_start_idx = val_end_idx + embargo
        test_end_idx = min(test_start_idx + fold_size, n_dates)

        if test_end_idx <= test_start_idx:
            break

        train_dates = unique_dates[:train_end_idx]
        val_dates = unique_dates[val_start_idx:val_end_idx]
        test_dates = unique_dates[test_start_idx:test_end_idx]

        train_mask = np.isin(dates, train_dates)
        val_mask = np.isin(dates, val_dates)
        test_mask = np.isin(dates, test_dates)

        folds.append(WalkForwardFold(
            fold_id=fold_id,
            train_indices=np.where(train_mask)[0],
            val_indices=np.where(val_mask)[0],
            test_indices=np.where(test_mask)[0],
        ))

    return folds


def _precision_at_k(
    y_true: np.ndarray,
    y_score: np.ndarray,
    k: int,
) -> float:
    if k <= 0 or len(y_true) == 0:
        return 0.0
    top_k = min(k, len(y_true))
    top_idx = np.argsort(-y_score)[:top_k]
    return float(np.sum(y_true[top_idx])) / top_k


def _brier_score(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    return float(np.mean((y_prob - y_true) ** 2))


def _calibration_error(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    n_bins: int = 10,
) -> float:
    bin_edges = np.linspace(0, 1, n_bins + 1)
    errors: list[float] = []
    for i in range(n_bins):
        mask = (y_prob >= bin_edges[i]) & (y_prob < bin_edges[i + 1])
        if np.sum(mask) > 0:
            avg_pred = float(np.mean(y_prob[mask]))
            avg_true = float(np.mean(y_true[mask]))
            errors.append(abs(avg_pred - avg_true))
    return float(np.mean(errors)) if errors else 0.0


def _pr_auc(y_true: np.ndarray, y_score: np.ndarray) -> float:
    try:
        from sklearn.metrics import average_precision_score
        return float(average_precision_score(y_true, y_score))
    except ImportError:
        if np.sum(y_true) == 0:
            return 0.0
        desc_idx = np.argsort(-y_score)
        y_sorted = y_true[desc_idx]
        tp = np.cumsum(y_sorted)
        fp = np.cumsum(1 - y_sorted)
        precision = tp / (tp + fp)
        recall = tp / max(tp[-1], 1)
        return float(np.trapz(precision, recall))


def evaluate_fold(
    fold: WalkForwardFold,
    X: np.ndarray,
    runner_labels: np.ndarray,
    mfe_5d: np.ndarray,
    mae_5d: np.ndarray,
    mfe_before_mae: np.ndarray,
    dilution_labels: np.ndarray,
    halt_labels: np.ndarray,
    slippage_labels: np.ndarray,
    config: ModelConfig,
) -> ValidationMetrics:
    X_train = X[fold.train_indices]
    X_test = X[fold.test_indices]

    y_train = runner_labels[fold.train_indices]
    y_test = runner_labels[fold.test_indices]

    if len(X_test) == 0 or np.sum(y_test) == 0:
        return ValidationMetrics(
            fold_id=fold.fold_id,
            precision_at_5=0.0,
            precision_at_10=0.0,
            precision_at_20=0.0,
            pr_auc=0.0,
            brier_score=1.0,
            calibration_error=1.0,
            expected_utility_per_alert=0.0,
            avg_mfe_top_10=0.0,
            avg_mae_top_10=0.0,
            mfe_before_mae_rate=0.0,
            slippage_adj_expectancy=0.0,
            dilution_adj_expectancy=0.0,
            halt_adj_expectancy=0.0,
            capacity_adj_expectancy=0.0,
            n_positive_events=int(np.sum(y_test)),
            n_total_predictions=len(y_test),
            base_rate=float(np.mean(y_test)) if len(y_test) > 0 else 0.0,
        )

    labels_by_horizon = {
        h: runner_labels[fold.train_indices] for h in config.horizons
    }

    hazard_model = HazardModel(config)
    hazard_model.train(X_train, labels_by_horizon)

    payoff_model = PayoffModel(config)
    payoff_model.train(
        X_train,
        mfe_by_horizon={5: mfe_5d[fold.train_indices]},
        mae_by_horizon={5: mae_5d[fold.train_indices]},
        mfe_before_mae=mfe_before_mae[fold.train_indices],
    )

    risk_model = RiskModel(config)
    risk_model.train(
        X_train,
        dilution_labels=dilution_labels[fold.train_indices],
        halt_labels=halt_labels[fold.train_indices],
        slippage_labels=slippage_labels[fold.train_indices],
    )

    hazard_est = hazard_model.predict(X_test)
    payoff_est = payoff_model.predict(X_test, horizon=5)
    risk_est = risk_model.predict(X_test)

    p_run_5d = np.array([h.p_run_5d for h in hazard_est])

    p5 = _precision_at_k(y_test, p_run_5d, 5)
    p10 = _precision_at_k(y_test, p_run_5d, 10)
    p20 = _precision_at_k(y_test, p_run_5d, 20)
    pr_auc_val = _pr_auc(y_test, p_run_5d)
    brier = _brier_score(y_test, p_run_5d)
    cal_err = _calibration_error(y_test, p_run_5d)

    top_10_idx = np.argsort(-p_run_5d)[:min(10, len(y_test))]
    avg_mfe = float(np.mean(mfe_5d[fold.test_indices][top_10_idx]))
    avg_mae = float(np.mean(mae_5d[fold.test_indices][top_10_idx]))

    mfe_b4_mae_arr = mfe_before_mae[fold.test_indices]
    mfe_b4_rate = float(np.mean(mfe_b4_mae_arr[top_10_idx]))

    eu_list: list[float] = []
    slip_adj: list[float] = []
    dil_adj: list[float] = []
    halt_adj: list[float] = []
    cap_adj: list[float] = []

    for i, idx in enumerate(top_10_idx):
        eu = compute_expected_utility(
            hazard_est[idx], payoff_est[idx], risk_est[idx],
            TimingPolicy.ENTER_T1_CLOSE, config,
        )
        eu_list.append(eu.eu)
        slip_adj.append(eu.e_mfe - eu.e_slippage * config.utility_slippage_penalty)
        dil_adj.append(eu.e_mfe - eu.e_dilution * config.utility_dilution_penalty)
        halt_adj.append(eu.e_mfe - eu.e_halt)
        cap_adj.append(eu.e_mfe - eu.e_capacity_penalty)

    return ValidationMetrics(
        fold_id=fold.fold_id,
        precision_at_5=p5,
        precision_at_10=p10,
        precision_at_20=p20,
        pr_auc=pr_auc_val,
        brier_score=brier,
        calibration_error=cal_err,
        expected_utility_per_alert=float(np.mean(eu_list)) if eu_list else 0.0,
        avg_mfe_top_10=avg_mfe,
        avg_mae_top_10=avg_mae,
        mfe_before_mae_rate=mfe_b4_rate,
        slippage_adj_expectancy=float(np.mean(slip_adj)) if slip_adj else 0.0,
        dilution_adj_expectancy=float(np.mean(dil_adj)) if dil_adj else 0.0,
        halt_adj_expectancy=float(np.mean(halt_adj)) if halt_adj else 0.0,
        capacity_adj_expectancy=float(np.mean(cap_adj)) if cap_adj else 0.0,
        n_positive_events=int(np.sum(y_test)),
        n_total_predictions=len(y_test),
        base_rate=float(np.mean(y_test)),
    )


def evaluate_timing_policies(
    X_test: np.ndarray,
    y_test: np.ndarray,
    mfe_5d: np.ndarray,
    mae_5d: np.ndarray,
    mfe_before_mae: np.ndarray,
    slippage_labels: np.ndarray,
    dilution_labels: np.ndarray,
    halt_labels: np.ndarray,
    hazard_model: HazardModel,
    payoff_model: PayoffModel,
    risk_model: RiskModel,
    config: ModelConfig,
) -> list[TimingPolicyReport]:
    hazard_est = hazard_model.predict(X_test)
    payoff_est = payoff_model.predict(X_test, horizon=5)
    risk_est = risk_model.predict(X_test)

    reports: list[TimingPolicyReport] = []
    for policy in TimingPolicy:
        if policy == TimingPolicy.REJECT_RISK_ADJUSTED:
            continue

        eu_values: list[float] = []
        hits: list[bool] = []
        mfe_vals: list[float] = []
        mae_vals: list[float] = []
        mfe_first: list[bool] = []
        slip_vals: list[float] = []
        dil_vals: list[float] = []
        halt_vals: list[float] = []
        cap_vals: list[float] = []

        threshold = 0.05
        for i in range(len(X_test)):
            eu = compute_expected_utility(
                hazard_est[i], payoff_est[i], risk_est[i], policy, config
            )
            if eu.p_event < threshold:
                continue

            eu_values.append(eu.eu)
            hits.append(bool(y_test[i]))
            mfe_vals.append(mfe_5d[i])
            mae_vals.append(mae_5d[i])
            mfe_first.append(bool(mfe_before_mae[i]))
            slip_vals.append(eu.e_slippage)
            dil_vals.append(eu.e_dilution)
            halt_vals.append(eu.e_halt)
            cap_vals.append(eu.e_capacity_penalty)

        n = len(hits)
        if n == 0:
            reports.append(TimingPolicyReport(
                policy=policy,
                hit_rate=0.0,
                mean_mfe=0.0,
                mean_mae=0.0,
                p_mfe_before_mae=0.0,
                slippage_adj_return=0.0,
                halt_adj_return=0.0,
                dilution_adj_return=0.0,
                capacity_adj_return=0.0,
                expected_utility=0.0,
                false_positive_cost=0.0,
                false_negative_cost=0.0,
                n_signals=0,
            ))
            continue

        hit_rate = sum(hits) / n
        mean_mfe = float(np.mean(mfe_vals))
        mean_mae = float(np.mean(mae_vals))
        p_mfe_first = sum(mfe_first) / n
        slip_adj = mean_mfe - float(np.mean(slip_vals)) * config.utility_slippage_penalty
        halt_adj = mean_mfe - float(np.mean(halt_vals))
        dil_adj = mean_mfe - float(np.mean(dil_vals)) * config.utility_dilution_penalty
        cap_adj = mean_mfe - float(np.mean(cap_vals)) * config.utility_capacity_penalty
        avg_eu = float(np.mean(eu_values))

        fp_cost = sum(1 for h, m in zip(hits, mae_vals) if not h) * abs(float(np.mean(mae_vals))) / max(n, 1)
        fn_cost = sum(1 for h in hits if not h) * mean_mfe / max(n, 1)

        reports.append(TimingPolicyReport(
            policy=policy,
            hit_rate=hit_rate,
            mean_mfe=mean_mfe,
            mean_mae=mean_mae,
            p_mfe_before_mae=p_mfe_first,
            slippage_adj_return=slip_adj,
            halt_adj_return=halt_adj,
            dilution_adj_return=dil_adj,
            capacity_adj_return=cap_adj,
            expected_utility=avg_eu,
            false_positive_cost=fp_cost,
            false_negative_cost=fn_cost,
            n_signals=n,
        ))

    return reports
