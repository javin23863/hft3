"""Training pipeline orchestrator for the runner prediction system.

Coordinates feature extraction, labeling, training, validation, and
report generation across the full prediction stack.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

from ..models import DailyBar, FloatRecord
from .features import FEATURE_NAMES, compute_all_features
from .hazard_model import HazardModel
from .payoff_heads import PayoffModel
from .risk_heads import RiskModel
from .runner_labeler import label_runner
from .types import (
    FeatureVector,
    ModelConfig,
    RunnerLabel,
    SnapshotType,
    TimingPolicyReport,
    ValidationMetrics,
)
from .utility import assemble_prediction
from .walk_forward import (
    WalkForwardFold,
    evaluate_fold,
    evaluate_timing_policies,
    generate_walk_forward_folds,
)

logger = logging.getLogger(__name__)


def build_training_dataset(
    symbol_bars: dict[str, list[DailyBar]],
    float_records: dict[str, list[FloatRecord]],
    config: ModelConfig,
) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray], list[RunnerLabel]]:
    all_features: list[FeatureVector] = []
    all_labels: list[RunnerLabel] = []

    for symbol, bars in symbol_bars.items():
        float_recs = float_records.get(symbol, [])

        for idx in range(config.lookback_days, len(bars)):
            fv = compute_all_features(
                symbol, bars, idx, _lookup_float(float_recs, bars[idx].date), config
            )
            if fv is None:
                continue

            lbl = label_runner(symbol, bars, idx, config)
            if lbl is None:
                continue

            all_features.append(fv)
            all_labels.append(lbl)

    if not all_features:
        return np.array([]), np.array([]), {}, []

    X = np.array(
        [fv.to_array(FEATURE_NAMES) for fv in all_features],
        dtype=np.float64,
    )

    dates = np.array([fv.date for fv in all_features])

    runner_labels = np.array([1 if l.is_runner else 0 for l in all_labels], dtype=np.float64)

    label_dict: dict[str, np.ndarray] = {
        "runner_labels": runner_labels,
        "dates": dates,
        "mfe_1d": np.array([l.mfe_1d for l in all_labels]),
        "mae_1d": np.array([l.mae_1d for l in all_labels]),
        "mfe_2d": np.array([l.mfe_2d for l in all_labels]),
        "mae_2d": np.array([l.mae_2d for l in all_labels]),
        "mfe_5d": np.array([l.mfe_5d for l in all_labels]),
        "mae_5d": np.array([l.mae_5d for l in all_labels]),
        "mfe_before_mae": np.array([1 if l.mfe_before_mae else 0 for l in all_labels], dtype=np.float64),
        "dilution_gap": np.array([1 if l.dilution_gap else 0 for l in all_labels], dtype=np.float64),
        "halt_event": np.array([1 if l.halt_event else 0 for l in all_labels], dtype=np.float64),
        "realized_slippage": np.array([l.realized_slippage for l in all_labels]),
    }

    return X, dates, label_dict, all_labels


def train_full_model(
    X: np.ndarray,
    label_dict: dict[str, np.ndarray],
    config: ModelConfig,
) -> tuple[HazardModel, PayoffModel, RiskModel, dict[str, float]]:
    metrics: dict[str, float] = {}

    labels_by_horizon: dict[int, np.ndarray] = {}
    if 1 in config.horizons:
        labels_by_horizon[1] = (label_dict["mfe_1d"] >= config.runner_threshold_pct / 100.0).astype(np.float64)
    if 2 in config.horizons:
        labels_by_horizon[2] = (label_dict["mfe_2d"] >= config.runner_threshold_pct / 100.0).astype(np.float64)
    if 5 in config.horizons:
        labels_by_horizon[5] = label_dict["runner_labels"]

    hazard_model = HazardModel(config)
    h_metrics = hazard_model.train(X, labels_by_horizon)
    metrics.update({f"hazard_{k}": v for k, v in h_metrics.items()})

    payoff_model = PayoffModel(config)
    mfe_by_h: dict[int, np.ndarray] = {}
    mae_by_h: dict[int, np.ndarray] = {}
    if 1 in config.horizons:
        mfe_by_h[1] = label_dict["mfe_1d"]
        mae_by_h[1] = label_dict["mae_1d"]
    if 2 in config.horizons:
        mfe_by_h[2] = label_dict["mfe_2d"]
        mae_by_h[2] = label_dict["mae_2d"]
    if 5 in config.horizons:
        mfe_by_h[5] = label_dict["mfe_5d"]
        mae_by_h[5] = label_dict["mae_5d"]

    p_metrics = payoff_model.train(
        X, mfe_by_h, mae_by_h, label_dict["mfe_before_mae"]
    )
    metrics.update({f"payoff_{k}": v for k, v in p_metrics.items()})

    risk_model = RiskModel(config)
    r_metrics = risk_model.train(
        X,
        dilution_labels=label_dict["dilution_gap"],
        halt_labels=label_dict["halt_event"],
        slippage_labels=label_dict["realized_slippage"],
    )
    metrics.update({f"risk_{k}": v for k, v in r_metrics.items()})

    return hazard_model, payoff_model, risk_model, metrics


def run_walk_forward_validation(
    X: np.ndarray,
    dates: np.ndarray,
    label_dict: dict[str, np.ndarray],
    config: ModelConfig,
) -> list[ValidationMetrics]:
    folds = generate_walk_forward_folds(len(X), dates, config)
    results: list[ValidationMetrics] = []

    for fold in folds:
        metrics = evaluate_fold(
            fold, X,
            runner_labels=label_dict["runner_labels"],
            mfe_5d=label_dict["mfe_5d"],
            mae_5d=label_dict["mae_5d"],
            mfe_before_mae=label_dict["mfe_before_mae"],
            dilution_labels=label_dict["dilution_gap"],
            halt_labels=label_dict["halt_event"],
            slippage_labels=label_dict["realized_slippage"],
            config=config,
        )
        results.append(metrics)
        logger.info(
            "Fold %d: P@5=%.3f P@10=%.3f PR-AUC=%.3f Brier=%.4f EU=%.4f",
            fold.fold_id,
            metrics.precision_at_5,
            metrics.precision_at_10,
            metrics.pr_auc,
            metrics.brier_score,
            metrics.expected_utility_per_alert,
        )

    return results


def run_timing_policy_analysis(
    X: np.ndarray,
    dates: np.ndarray,
    label_dict: dict[str, np.ndarray],
    config: ModelConfig,
) -> list[TimingPolicyReport]:
    folds = generate_walk_forward_folds(len(X), dates, config)
    if not folds:
        return []

    last_fold = folds[-1]
    train_idx = last_fold.train_indices
    test_idx = last_fold.test_indices

    X_train = X[train_idx]
    X_test = X[test_idx]

    labels_by_horizon: dict[int, np.ndarray] = {}
    for h in config.horizons:
        if h == 1:
            labels_by_horizon[1] = (label_dict["mfe_1d"][train_idx] >= config.runner_threshold_pct / 100.0).astype(np.float64)
        elif h == 2:
            labels_by_horizon[2] = (label_dict["mfe_2d"][train_idx] >= config.runner_threshold_pct / 100.0).astype(np.float64)
        elif h == 5:
            labels_by_horizon[5] = label_dict["runner_labels"][train_idx]

    hazard_model = HazardModel(config)
    hazard_model.train(X_train, labels_by_horizon)

    payoff_model = PayoffModel(config)
    payoff_model.train(
        X_train,
        mfe_by_horizon={5: label_dict["mfe_5d"][train_idx]},
        mae_by_horizon={5: label_dict["mae_5d"][train_idx]},
        mfe_before_mae=label_dict["mfe_before_mae"][train_idx],
    )

    risk_model = RiskModel(config)
    risk_model.train(
        X_train,
        dilution_labels=label_dict["dilution_gap"][train_idx],
        halt_labels=label_dict["halt_event"][train_idx],
        slippage_labels=label_dict["realized_slippage"][train_idx],
    )

    return evaluate_timing_policies(
        X_test,
        label_dict["runner_labels"][test_idx],
        label_dict["mfe_5d"][test_idx],
        label_dict["mae_5d"][test_idx],
        label_dict["mfe_before_mae"][test_idx],
        label_dict["realized_slippage"][test_idx],
        label_dict["dilution_gap"][test_idx],
        label_dict["halt_event"][test_idx],
        hazard_model,
        payoff_model,
        risk_model,
        config,
    )


def generate_predictions(
    symbol_bars: dict[str, list[DailyBar]],
    float_records: dict[str, list[FloatRecord]],
    hazard_model: HazardModel,
    payoff_model: PayoffModel,
    risk_model: RiskModel,
    config: ModelConfig,
) -> list[dict[str, Any]]:
    predictions: list[dict[str, Any]] = []

    for symbol, bars in symbol_bars.items():
        float_recs = float_records.get(symbol, [])
        idx = len(bars) - 1

        fv = compute_all_features(
            symbol, bars, idx, _lookup_float(float_recs, bars[idx].date), config
        )
        if fv is None:
            continue

        x = fv.to_array(FEATURE_NAMES).reshape(1, -1)
        hazard = hazard_model.predict_single(x[0])
        payoff = payoff_model.predict_single(x[0], horizon=5)
        risk = risk_model.predict_single(x[0])

        pred = assemble_prediction(
            ticker=symbol,
            timestamp=bars[idx].date,
            snapshot_type=SnapshotType.DAILY_CLOSE,
            hazard=hazard,
            payoff=payoff,
            risk=risk,
            config=config,
        )
        predictions.append(pred.to_dict())

    predictions.sort(key=lambda p: p["hazard"]["p_run_5d"], reverse=True)
    return predictions


def save_models(
    hazard_model: HazardModel,
    payoff_model: PayoffModel,
    risk_model: RiskModel,
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    hazard_model.save(output_dir / "hazard")
    payoff_model.save(output_dir / "payoff")
    risk_model.save(output_dir / "risk")


def load_models(
    model_dir: Path,
    config: ModelConfig,
) -> tuple[HazardModel, PayoffModel, RiskModel]:
    hazard_model = HazardModel(config)
    hazard_model.load(model_dir / "hazard")
    payoff_model = PayoffModel(config)
    payoff_model.load(model_dir / "payoff")
    risk_model = RiskModel(config)
    risk_model.load(model_dir / "risk")
    return hazard_model, payoff_model, risk_model


def write_report(
    train_metrics: dict[str, float],
    wf_metrics: list[ValidationMetrics],
    timing_reports: list[TimingPolicyReport],
    feature_importance: list[tuple[str, float]],
    output_dir: Path,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "train_metrics": train_metrics,
        "walk_forward": [m.to_dict() for m in wf_metrics],
        "timing_policies": [t.to_dict() for t in timing_reports],
        "feature_importance": [
            {"feature": f, "importance": float(i)} for f, i in feature_importance[:20]
        ],
    }

    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2))

    md_lines = [
        "# Runner Prediction Model Report",
        "",
        "## Training Metrics",
        "",
    ]
    for k, v in sorted(train_metrics.items()):
        md_lines.append(f"- **{k}**: {v:.4f}")

    md_lines.extend(["", "## Walk-Forward Validation", ""])
    if wf_metrics:
        md_lines.append("| Fold | P@5 | P@10 | P@20 | PR-AUC | Brier | EU/Alert |")
        md_lines.append("|------|-----|------|------|--------|-------|----------|")
        for m in wf_metrics:
            md_lines.append(
                f"| {m.fold_id} | {m.precision_at_5:.3f} | {m.precision_at_10:.3f} "
                f"| {m.precision_at_20:.3f} | {m.pr_auc:.3f} | {m.brier_score:.4f} "
                f"| {m.expected_utility_per_alert:.4f} |"
            )

        avg_p5 = np.mean([m.precision_at_5 for m in wf_metrics])
        avg_p10 = np.mean([m.precision_at_10 for m in wf_metrics])
        avg_prauc = np.mean([m.pr_auc for m in wf_metrics])
        avg_eu = np.mean([m.expected_utility_per_alert for m in wf_metrics])
        md_lines.extend([
            "",
            f"**Average P@5**: {avg_p5:.3f}  ",
            f"**Average P@10**: {avg_p10:.3f}  ",
            f"**Average PR-AUC**: {avg_prauc:.3f}  ",
            f"**Average EU/Alert**: {avg_eu:.4f}  ",
        ])

    md_lines.extend(["", "## Timing Policy Comparison", ""])
    if timing_reports:
        md_lines.append("| Policy | Hit Rate | Mean MFE | EU | Signals |")
        md_lines.append("|--------|----------|----------|----|---------|")
        for t in timing_reports:
            md_lines.append(
                f"| {t.policy.value} | {t.hit_rate:.3f} | {t.mean_mfe:.3f} "
                f"| {t.expected_utility:.4f} | {t.n_signals} |"
            )

    md_lines.extend(["", "## Top Feature Importance (h=5)", ""])
    for f, i in feature_importance[:15]:
        md_lines.append(f"- **{f}**: {i:.1f}")

    md_lines.extend([
        "",
        "## Falsification Checks",
        "",
        "- [ ] T-1/T-2 features improve over random baseline",
        "- [ ] Predictive value exists before premarket",
        "- [ ] Top alerts have positive EU after slippage/dilution/halts",
        "- [ ] Feature importance not dominated by leakage-prone fields",
        "- [ ] Performance stable across folds",
        "- [ ] Model detects latent states, not just momentum",
        "- [ ] Alerts are in tradeable names",
    ])

    md_path = output_dir / "report.md"
    md_path.write_text("\n".join(md_lines))
    return report_path


def _lookup_float(
    records: list[FloatRecord], date: str
) -> FloatRecord | None:
    best: FloatRecord | None = None
    for rec in records:
        if rec.as_of_date <= date:
            if best is None or rec.as_of_date > best.as_of_date:
                best = rec
    return best
