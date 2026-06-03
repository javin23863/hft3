"""Prediction model types for low-float runner hazard forecasting."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np


class TimingPolicy(str, Enum):
    WATCH = "WATCH"
    SEED_T2 = "SEED_T2"
    ENTER_T1_CLOSE = "ENTER_T1_CLOSE"
    ENTER_AFTER_HOURS = "ENTER_AFTER_HOURS"
    ENTER_PREMARKET = "ENTER_PREMARKET"
    ENTER_OPEN_CONFIRMATION = "ENTER_OPEN_CONFIRMATION"
    ENTER_INTRADAY_CONTINUATION = "ENTER_INTRADAY_CONTINUATION"
    REJECT_RISK_ADJUSTED = "REJECT_RISK_ADJUSTED"


class SnapshotType(str, Enum):
    DAILY_CLOSE = "daily_close"
    AFTER_HOURS = "after_hours"
    PREMARKET = "premarket"
    INTRADAY = "intraday"


@dataclass
class HazardEstimate:
    p_run_5d: float
    p_run_2d: float
    p_run_1d: float
    p_afterhours_ignite: float
    p_premarket_ignite: float
    p_intraday_continuation: float

    def to_dict(self) -> dict[str, float]:
        return {
            "p_run_5d": self.p_run_5d,
            "p_run_2d": self.p_run_2d,
            "p_run_1d": self.p_run_1d,
            "p_afterhours_ignite": self.p_afterhours_ignite,
            "p_premarket_ignite": self.p_premarket_ignite,
            "p_intraday_continuation": self.p_intraday_continuation,
        }


@dataclass
class PayoffEstimate:
    expected_mfe: float
    expected_mae: float
    p_mfe_before_mae: float
    mfe_median: float
    mae_median: float

    def to_dict(self) -> dict[str, float]:
        return {
            "expected_mfe": self.expected_mfe,
            "expected_mae": self.expected_mae,
            "p_mfe_before_mae": self.p_mfe_before_mae,
            "mfe_median": self.mfe_median,
            "mae_median": self.mae_median,
        }


@dataclass
class RiskEstimate:
    p_dilution_gap: float
    expected_dilution_loss: float
    p_halt_event: float
    expected_slippage: float
    expected_capacity: float
    p_manipulation_risk: float

    def to_dict(self) -> dict[str, float]:
        return {
            "p_dilution_gap": self.p_dilution_gap,
            "expected_dilution_loss": self.expected_dilution_loss,
            "p_halt_event": self.p_halt_event,
            "expected_slippage": self.expected_slippage,
            "expected_capacity": self.expected_capacity,
            "p_manipulation_risk": self.p_manipulation_risk,
        }


@dataclass
class ExpectedUtility:
    policy: TimingPolicy
    eu: float
    p_event: float
    e_mfe: float
    e_mae: float
    e_slippage: float
    e_dilution: float
    e_halt: float
    e_capacity_penalty: float
    e_manipulation: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy.value,
            "eu": self.eu,
            "p_event": self.p_event,
            "e_mfe": self.e_mfe,
            "e_mae": self.e_mae,
            "e_slippage": self.e_slippage,
            "e_dilution": self.e_dilution,
            "e_halt": self.e_halt,
            "e_capacity_penalty": self.e_capacity_penalty,
            "e_manipulation": self.e_manipulation,
        }


@dataclass
class RunnerPrediction:
    ticker: str
    timestamp: str
    snapshot_type: SnapshotType
    hazard: HazardEstimate
    payoff: PayoffEstimate
    risk: RiskEstimate
    utility_by_policy: list[ExpectedUtility]
    recommended_policy: TimingPolicy
    positive_reason_codes: list[str]
    negative_reason_codes: list[str]
    confidence_score: float
    calibration_bucket: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "timestamp": self.timestamp,
            "snapshot_type": self.snapshot_type.value,
            "hazard": self.hazard.to_dict(),
            "payoff": self.payoff.to_dict(),
            "risk": self.risk.to_dict(),
            "utility_by_policy": [u.to_dict() for u in self.utility_by_policy],
            "recommended_policy": self.recommended_policy.value,
            "positive_reason_codes": self.positive_reason_codes,
            "negative_reason_codes": self.negative_reason_codes,
            "confidence_score": self.confidence_score,
            "calibration_bucket": self.calibration_bucket,
        }


@dataclass
class RunnerLabel:
    symbol: str
    event_date: str
    is_runner: bool
    mfe_1d: float
    mae_1d: float
    mfe_2d: float
    mae_2d: float
    mfe_5d: float
    mae_5d: float
    mfe_before_mae: bool
    dilution_gap: bool
    halt_event: bool
    realized_slippage: float
    runner_type: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "event_date": self.event_date,
            "is_runner": self.is_runner,
            "mfe_1d": self.mfe_1d,
            "mae_1d": self.mae_1d,
            "mfe_2d": self.mfe_2d,
            "mae_2d": self.mae_2d,
            "mfe_5d": self.mfe_5d,
            "mae_5d": self.mae_5d,
            "mfe_before_mae": self.mfe_before_mae,
            "dilution_gap": self.dilution_gap,
            "halt_event": self.halt_event,
            "realized_slippage": self.realized_slippage,
            "runner_type": self.runner_type,
        }


@dataclass
class FeatureVector:
    symbol: str
    date: str
    snapshot_type: SnapshotType
    features: dict[str, float]

    def to_array(self, feature_names: list[str]) -> np.ndarray:
        return np.array(
            [self.features.get(n, 0.0) for n in feature_names],
            dtype=np.float64,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "date": self.date,
            "snapshot_type": self.snapshot_type.value,
            "features": self.features,
        }


@dataclass
class ValidationMetrics:
    fold_id: int
    precision_at_5: float
    precision_at_10: float
    precision_at_20: float
    pr_auc: float
    brier_score: float
    calibration_error: float
    expected_utility_per_alert: float
    avg_mfe_top_10: float
    avg_mae_top_10: float
    mfe_before_mae_rate: float
    slippage_adj_expectancy: float
    dilution_adj_expectancy: float
    halt_adj_expectancy: float
    capacity_adj_expectancy: float
    n_positive_events: int
    n_total_predictions: int
    base_rate: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "fold_id": self.fold_id,
            "precision_at_5": self.precision_at_5,
            "precision_at_10": self.precision_at_10,
            "precision_at_20": self.precision_at_20,
            "pr_auc": self.pr_auc,
            "brier_score": self.brier_score,
            "calibration_error": self.calibration_error,
            "expected_utility_per_alert": self.expected_utility_per_alert,
            "avg_mfe_top_10": self.avg_mfe_top_10,
            "avg_mae_top_10": self.avg_mae_top_10,
            "mfe_before_mae_rate": self.mfe_before_mae_rate,
            "slippage_adj_expectancy": self.slippage_adj_expectancy,
            "dilution_adj_expectancy": self.dilution_adj_expectancy,
            "halt_adj_expectancy": self.halt_adj_expectancy,
            "capacity_adj_expectancy": self.capacity_adj_expectancy,
            "n_positive_events": self.n_positive_events,
            "n_total_predictions": self.n_total_predictions,
            "base_rate": self.base_rate,
        }


@dataclass
class TimingPolicyReport:
    policy: TimingPolicy
    hit_rate: float
    mean_mfe: float
    mean_mae: float
    p_mfe_before_mae: float
    slippage_adj_return: float
    halt_adj_return: float
    dilution_adj_return: float
    capacity_adj_return: float
    expected_utility: float
    false_positive_cost: float
    false_negative_cost: float
    n_signals: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy.value,
            "hit_rate": self.hit_rate,
            "mean_mfe": self.mean_mfe,
            "mean_mae": self.mean_mae,
            "p_mfe_before_mae": self.p_mfe_before_mae,
            "slippage_adj_return": self.slippage_adj_return,
            "halt_adj_return": self.halt_adj_return,
            "dilution_adj_return": self.dilution_adj_return,
            "capacity_adj_return": self.capacity_adj_return,
            "expected_utility": self.expected_utility,
            "false_positive_cost": self.false_positive_cost,
            "false_negative_cost": self.false_negative_cost,
            "n_signals": self.n_signals,
        }


@dataclass
class ModelConfig:
    runner_threshold_pct: float = 30.0
    extreme_runner_threshold_pct: float = 50.0
    max_float_shares: float = 20_000_000
    min_price: float = 0.50
    max_price: float = 20.0
    lookback_days: int = 60
    feature_window_days: int = 20
    horizons: list[int] = field(default_factory=lambda: [1, 2, 5])
    focal_loss_gamma: float = 2.0
    focal_loss_alpha: float = 0.75
    lgb_n_estimators: int = 300
    lgb_max_depth: int = 6
    lgb_learning_rate: float = 0.05
    lgb_min_child_samples: int = 20
    lgb_subsample: float = 0.8
    lgb_colsample_bytree: float = 0.8
    lgb_reg_alpha: float = 0.1
    lgb_reg_lambda: float = 1.0
    walk_forward_n_folds: int = 5
    walk_forward_embargo_days: int = 5
    utility_slippage_penalty: float = 0.02
    utility_dilution_penalty: float = 0.15
    utility_halt_penalty: float = 0.25
    utility_capacity_penalty: float = 0.01
    utility_manipulation_penalty: float = 0.10

    def to_dict(self) -> dict[str, Any]:
        return {
            "runner_threshold_pct": self.runner_threshold_pct,
            "extreme_runner_threshold_pct": self.extreme_runner_threshold_pct,
            "max_float_shares": self.max_float_shares,
            "min_price": self.min_price,
            "max_price": self.max_price,
            "lookback_days": self.lookback_days,
            "feature_window_days": self.feature_window_days,
            "horizons": self.horizons,
            "focal_loss_gamma": self.focal_loss_gamma,
            "focal_loss_alpha": self.focal_loss_alpha,
            "lgb_n_estimators": self.lgb_n_estimators,
            "lgb_max_depth": self.lgb_max_depth,
            "lgb_learning_rate": self.lgb_learning_rate,
            "lgb_min_child_samples": self.lgb_min_child_samples,
            "lgb_subsample": self.lgb_subsample,
            "lgb_colsample_bytree": self.lgb_colsample_bytree,
            "lgb_reg_alpha": self.lgb_reg_alpha,
            "lgb_reg_lambda": self.lgb_reg_lambda,
            "walk_forward_n_folds": self.walk_forward_n_folds,
            "walk_forward_embargo_days": self.walk_forward_embargo_days,
        }
