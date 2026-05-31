"""Walk-forward ML runner for crypto hypothesis candidates."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from sklearn.linear_model import ElasticNet, Ridge
from sklearn.metrics import r2_score

from crypto_lane.src.config_loader import load_hypotheses, load_yaml
from crypto_lane.src.features.feature_matrix import build_labeled_frame
from crypto_lane.src.labels.event_study_labels import randomized_event_control_ic
from crypto_lane.src.labels.forward_labels import LABEL_COLUMNS, information_coefficient
from crypto_lane.src.ml.baselines import predict_baseline, train_baseline
from crypto_lane.src.ml.candidate_registry import candidate_by_id
from crypto_lane.src.ml.embargo import horizon_steps_from_ms, resolve_embargo_steps, resolve_label_horizon_ms
from crypto_lane.src.ml.holdout_gate import run_holdout_gate
from crypto_lane.src.ml.walk_forward import purged_expanding_folds
from crypto_lane.src.types import repo_root_from_lane


def _all_label_names() -> frozenset[str]:
    names = set(LABEL_COLUMNS)
    for h in load_hypotheses():
        names.update(h.get("labels") or [])
    return frozenset(names)


def feature_columns(df: pl.DataFrame) -> list[str]:
    exclude = _all_label_names() | {
        "exchange_timestamp", "event_time", "node_observation_time",
        "validation_period",
        "feature_source", "T_avail_ns", "staleness_delta_ms", "is_pit_safe",
    }
    numeric = (pl.Float64, pl.Int64, pl.Int32)
    return [c for c in df.columns if c not in exclude and df[c].dtype in numeric]


def _control_feature_columns(hypothesis_id: str | None, df: pl.DataFrame, feat_cols: list[str]) -> list[str]:
    if hypothesis_id == "CRYPTO_H7":
        leak_free = [
            c for c in ("btc_fee_spike_zscore", "btc_blockspace_stress_score", "jump_intensity_lambda")
            if c in df.columns
        ]
        return leak_free or feat_cols
    return feat_cols


def _prepare_xy(df: pl.DataFrame, target: str, feat_cols: list[str]) -> tuple[np.ndarray, np.ndarray, pl.DataFrame]:
    clean = df.drop_nulls(subset=feat_cols + [target])
    if clean.height == 0:
        raise ValueError("no rows after drop_nulls on features and target")
    X = clean.select(feat_cols).to_numpy()
    y = clean[target].to_numpy()
    finite = np.isfinite(y) & np.all(np.isfinite(X), axis=1)
    idx = np.where(finite)[0]
    if idx.size == 0:
        raise ValueError("no finite rows after NaN filter")
    return X[finite], y[finite], clean[idx.tolist()]


def _maybe_challenger(name: str, X: np.ndarray, y: np.ndarray, columns: list[str]):
    if name == "lightgbm":
        try:
            import lightgbm as lgb
            model = lgb.LGBMRegressor(n_estimators=30, max_depth=4, verbose=-1)
            model.fit(X, y)
            return model, "regression"
        except ImportError:
            pass
    if name == "xgboost":
        try:
            import xgboost as xgb
            model = xgb.XGBRegressor(n_estimators=30, max_depth=4, verbosity=0)
            model.fit(X, y)
            return model, "regression"
        except ImportError:
            pass
    if name == "elastic_net":
        model = ElasticNet(max_iter=3000)
        model.fit(X, y)
        return model, "regression"
    model = Ridge()
    model.fit(X, y)
    return model, "regression"


def _fixture_fold_sizes(n: int, backtest: dict | None) -> tuple[int, int]:
    if (backtest or {}).get("validation_mode") != "fixture":
        min_train = 6 if n >= 50 else max(4, n // 3)
        test_size = 2 if n >= 50 else max(1, n // 5)
        return min_train, test_size
    if n < 40:
        min_train = max(4, n // 5)
        test_size = max(2, n // 6)
    else:
        min_train = max(12, int(n * 0.2))
        test_size = max(6, int(n * 0.1))
    return min_train, test_size


def _evaluate_folds(
    df: pl.DataFrame,
    target: str,
    feat_cols: list[str],
    baseline_name: str,
    challenger_name: str,
    validation: dict,
    *,
    backtest: dict | None = None,
    label_horizon_steps: int = 1,
) -> dict[str, Any]:
    X_all, y_all, clean = _prepare_xy(df, target, feat_cols)
    n = len(y_all)
    embargo = resolve_embargo_steps(backtest, validation, clean, label_horizon_steps=label_horizon_steps, n=n)
    min_folds = int(validation.get("min_folds", 3))
    min_train, test_size = _fixture_fold_sizes(n, backtest)
    folds = purged_expanding_folds(
        n,
        min_train=min_train,
        test_size=test_size,
        embargo=embargo,
        label_horizon=label_horizon_steps,
        min_folds=min_folds,
    )

    oos_ic_b: list[float] = []
    oos_ic_c: list[float] = []
    oos_r2_b: list[float] = []

    for fold in folds:
        X_tr, y_tr = X_all[fold.train_idx], y_all[fold.train_idx]
        X_te, y_te = X_all[fold.test_idx], y_all[fold.test_idx]
        if X_te.size == 0:
            continue
        bm, kind, idx = train_baseline(baseline_name, X_tr, y_tr, feat_cols)
        pred_b = predict_baseline(bm, kind, idx, X_te)
        oos_ic_b.append(information_coefficient(y_te, pred_b))
        oos_r2_b.append(float(r2_score(y_te, pred_b)) if len(y_te) > 1 else 0.0)

        cm, _ = _maybe_challenger(challenger_name, X_tr, y_tr, feat_cols)
        pred_c = cm.predict(X_te)
        oos_ic_c.append(information_coefficient(y_te, pred_c))

    return {
        "n_folds": len(folds),
        "min_folds_required": min_folds,
        "min_folds_met": len(folds) >= min_folds,
        "embargo_steps": embargo,
        "label_horizon_steps": label_horizon_steps,
        "oos_ic_baseline_mean": float(np.mean(oos_ic_b)) if oos_ic_b else 0.0,
        "oos_ic_challenger_mean": float(np.mean(oos_ic_c)) if oos_ic_c else 0.0,
        "oos_r2_baseline_mean": float(np.mean(oos_r2_b)) if oos_r2_b else 0.0,
    }


def _evaluate_purged_cv(
    df: pl.DataFrame,
    target: str,
    feat_cols: list[str],
    baseline_name: str,
    validation: dict,
    *,
    backtest: dict | None = None,
    label_horizon_steps: int = 1,
) -> dict[str, Any]:
    X_all, y_all, clean = _prepare_xy(df, target, feat_cols)
    n = len(y_all)
    embargo = resolve_embargo_steps(backtest, validation, clean, label_horizon_steps=label_horizon_steps, n=n)
    n_splits = int(validation.get("min_folds", 3))
    min_train, test_size = _fixture_fold_sizes(n, backtest)
    ics: list[float] = []
    folds = purged_expanding_folds(
        n,
        min_train=min_train,
        test_size=test_size,
        embargo=embargo,
        label_horizon=label_horizon_steps,
        min_folds=n_splits,
    )
    for fold in folds:
        X_tr, y_tr = X_all[fold.train_idx], y_all[fold.train_idx]
        X_te, y_te = X_all[fold.test_idx], y_all[fold.test_idx]
        if X_te.size == 0:
            continue
        bm, kind, idx = train_baseline(baseline_name, X_tr, y_tr, feat_cols)
        pred = predict_baseline(bm, kind, idx, X_te)
        ics.append(information_coefficient(y_te, pred))
    return {
        "n_splits": len(ics),
        "purged_cv_ic_mean": float(np.mean(ics)) if ics else 0.0,
        "embargo_steps": embargo,
        "label_horizon_steps": label_horizon_steps,
    }


def _negative_controls(
    df: pl.DataFrame,
    target: str,
    feat_cols: list[str],
    *,
    validation: dict,
    backtest: dict | None = None,
    label_horizon_steps: int = 1,
    randomized_event_times: bool = False,
    horizons: list[str] | None = None,
    horizon_ms: int = 1000,
    hypothesis_id: str | None = None,
) -> dict[str, Any]:
    """Negative controls pooled across all purged WF OOS folds."""
    rng = np.random.default_rng(42)
    event_study = hypothesis_id == "CRYPTO_H7"
    clean = df.drop_nulls(subset=feat_cols)
    if clean.height == 0:
        return {
            "real_oos_ic": 0.0,
            "shuffled_labels_ic": 0.0,
            "shifted_features_ic": 0.0,
            "shuffled_degraded": False,
            "shifted_degraded": False,
            "controls_skipped_low_signal": False,
            "insufficient_label_variance": True,
        }

    X = clean.select(feat_cols).to_numpy()
    y_all = clean[target].to_numpy()
    if event_study:
        labeled = np.isfinite(y_all)
        if labeled.sum() < 8:
            return {
                "real_oos_ic": 0.0,
                "shuffled_labels_ic": 0.0,
                "shifted_features_ic": 0.0,
                "shuffled_degraded": False,
                "shifted_degraded": False,
                "controls_skipped_low_signal": False,
                "insufficient_label_variance": True,
            }
        y = y_all
        n = clean.height
    else:
        labeled = np.isfinite(y_all)
        if labeled.sum() < 8 or np.std(y_all[labeled]) < 1e-9:
            return {
                "real_oos_ic": 0.0,
                "shuffled_labels_ic": 0.0,
                "shifted_features_ic": 0.0,
                "shuffled_degraded": False,
                "shifted_degraded": False,
                "controls_skipped_low_signal": False,
                "insufficient_label_variance": True,
            }
        X, y_all, clean = _prepare_xy(clean, target, feat_cols)
        y = y_all
        n = len(y)

    embargo = resolve_embargo_steps(backtest, validation, clean, label_horizon_steps=label_horizon_steps, n=n)
    min_folds_req = int(validation.get("min_folds", 3))
    min_train, test_size = _fixture_fold_sizes(n, backtest)
    folds = purged_expanding_folds(
        n, min_train=min_train, test_size=test_size,
        embargo=embargo, label_horizon=label_horizon_steps,
        min_folds=min_folds_req if hypothesis_id != "CRYPTO_H7" else min(2, min_folds_req),
    )
    if not folds:
        return {
            "real_oos_ic": 0.0,
            "shuffled_labels_ic": 0.0,
            "shifted_features_ic": 0.0,
            "shuffled_degraded": False,
            "shifted_degraded": False,
            "controls_skipped_low_signal": False,
            "no_wf_folds_for_controls": True,
        }

    y_parts: list[np.ndarray] = []
    pred_real_parts: list[np.ndarray] = []
    pred_shuf_parts: list[np.ndarray] = []
    pred_shift_parts: list[np.ndarray] = []

    for fold in folds:
        if event_study:
            train_idx = np.array([i for i in range(fold.train_idx.start, fold.train_idx.stop) if labeled[i]], dtype=int)
            test_idx = np.array([i for i in range(fold.test_idx.start, fold.test_idx.stop) if labeled[i]], dtype=int)
            if test_idx.size < 2 or train_idx.size < 3:
                continue
            X_tr, y_tr = X[train_idx], y_all[train_idx]
            X_te, y_te = X[test_idx], y_all[test_idx]
        else:
            X_tr, y_tr = X[fold.train_idx], y[fold.train_idx]
            X_te, y_te = X[fold.test_idx], y[fold.test_idx]
        if X_te.size == 0:
            continue
        y_parts.append(y_te)

        bm, kind, idx = train_baseline("ridge", X_tr, y_tr, feat_cols)
        pred_real_parts.append(predict_baseline(bm, kind, idx, X_te))

        y_shuf = rng.permutation(y_tr)
        bm_s, kind_s, idx_s = train_baseline("ridge", X_tr, y_shuf, feat_cols)
        pred_shuf_parts.append(predict_baseline(bm_s, kind_s, idx_s, X_te))

        perm_rows = rng.permutation(len(X_tr))
        bm_x, kind_x, idx_x = train_baseline("ridge", X_tr[perm_rows], y_tr, feat_cols)
        pred_shift_parts.append(predict_baseline(bm_x, kind_x, idx_x, X_te))

    y_oos = np.concatenate(y_parts)
    pred_real = np.concatenate(pred_real_parts)
    pred_shuf = np.concatenate(pred_shuf_parts)
    pred_shift = np.concatenate(pred_shift_parts)

    ic_real = information_coefficient(y_oos, pred_real)
    ic_shuf = information_coefficient(y_oos, pred_shuf)
    ic_shift = information_coefficient(y_oos, pred_shift)
    ic_delta_shuf = abs(ic_real - ic_shuf)
    ic_delta_shift = abs(ic_real - ic_shift)

    out: dict[str, Any] = {
        "shuffled_labels_ic": ic_shuf,
        "shifted_features_ic": ic_shift,
        "real_oos_ic": ic_real,
        "shuffled_ic_delta": ic_delta_shuf,
        "shifted_ic_delta": ic_delta_shift,
        "controls_skipped_low_signal": False,
        "control_fold": "pooled_purged_wf",
        "shuffled_degraded": (
            ic_delta_shuf > 0.08
            or abs(ic_shuf) < max(0.05, abs(ic_real) * 0.5)
            or abs(ic_real) > abs(ic_shuf) + 0.03
        ),
        "shifted_degraded": (
            ic_delta_shift > 0.05
            or abs(ic_shift) < max(0.05, abs(ic_real) * 0.5)
            or abs(ic_real) > abs(ic_shift) + 0.03
        ),
    }
    if randomized_event_times and hypothesis_id == "CRYPTO_H7":
        ev = randomized_event_control_ic(
            clean, target, feat_cols, horizons=horizons, horizon_ms=horizon_ms
        )
        out["randomized_event_ic"] = ev.get("randomized_event_ic")
        out["randomized_degraded"] = ev.get("randomized_degraded")
        out["randomized_control_real_ic"] = ev.get("real_oos_ic")
    return out


def run_smoke(candidate_id: str, output_dir: str | Path | None = None) -> dict[str, Any]:
    cand = candidate_by_id(candidate_id)
    bt_path = repo_root_from_lane() / "backtests" / "configs" / "crypto_hypotheses"
    bt_file = bt_path / f"{candidate_id.replace('crypto_', '')}.yaml"
    backtest = load_yaml(bt_file) if bt_file.exists() else {}

    target = cand["target"]
    hypothesis_id = cand.get("hypothesis_id")
    label_set = _all_label_names()
    validation = cand.get("validation") or {}
    ab = cand.get("ablation") or {}
    nc = cand.get("negative_controls") or {}
    horizon_ms = resolve_label_horizon_ms(cand.get("horizons"), backtest)
    cand_feats = cand.get("features") or []
    validation_eff = dict(validation)
    if hypothesis_id == "CRYPTO_H7":
        validation_eff["min_folds"] = min(2, int(validation.get("min_folds", 3)))

    runs: dict[str, Any] = {}
    for run_name, include_node in (
        ("with_btc_node", True),
        ("without_btc_node", False),
    ):
        if run_name == "without_btc_node" and not ab.get("run_without_btc_node_features"):
            continue
        df = build_labeled_frame(
            include_btc_node=include_node,
            horizon_ms=horizon_ms,
            backtest_config=backtest,
            hypothesis_id=hypothesis_id,
            horizons=cand.get("horizons"),
            required_features=cand_feats,
        )
        if target not in df.columns:
            raise ValueError(f"target {target!r} missing after label attach")
        df = df.filter(pl.col(target).is_finite())
        if df.height < 8:
            if run_name == "without_btc_node":
                continue
            raise ValueError(f"insufficient labeled rows for {candidate_id}: {df.height}")
        feats = [c for c in (cand_feats or feature_columns(df)) if c in df.columns and c not in label_set and c != target]
        if not feats:
            feats = feature_columns(df)
        label_h = horizon_steps_from_ms(df, horizon_ms)
        metrics = _evaluate_folds(
            df,
            target,
            feats,
            (cand.get("baseline") or ["ridge"])[0],
            (cand.get("challengers") or ["ridge"])[0],
            validation_eff,
            backtest=backtest,
            label_horizon_steps=label_h,
        )
        purged = _evaluate_purged_cv(
            df, target, feats, (cand.get("baseline") or ["ridge"])[0], validation_eff,
            backtest=backtest, label_horizon_steps=label_h,
        )
        runs[run_name] = {**metrics, **purged, "n_features": len(feats), "n_rows": df.height}

    ic_with = runs.get("with_btc_node", {}).get("oos_ic_baseline_mean", 0.0)
    ic_without = runs.get("without_btc_node", {}).get("oos_ic_baseline_mean", ic_with)
    ablation_delta = ic_with - ic_without if "without_btc_node" in runs else None

    df0 = build_labeled_frame(
        include_btc_node=True,
        horizon_ms=horizon_ms,
        backtest_config=backtest,
        hypothesis_id=hypothesis_id,
        horizons=cand.get("horizons"),
        required_features=cand_feats,
    )
    df_labeled = df0.filter(pl.col(target).is_finite())
    feat0 = [c for c in (cand_feats or feature_columns(df_labeled)) if c in df_labeled.columns and c not in label_set and c != target]
    feats0 = feat0 or feature_columns(df_labeled)

    holdout_feats = _control_feature_columns(hypothesis_id, df_labeled, feats0)
    holdout = run_holdout_gate(
        df_labeled, target, holdout_feats, (cand.get("baseline") or ["ridge"])[0],
        min_ic=0.0,
    )

    label_h0 = horizon_steps_from_ms(df_labeled, horizon_ms)
    neg_df = df0 if hypothesis_id == "CRYPTO_H7" else df_labeled
    control_feats = _control_feature_columns(hypothesis_id, neg_df, feats0)
    neg = _negative_controls(
        neg_df,
        target,
        control_feats,
        validation=validation_eff,
        backtest=backtest,
        label_horizon_steps=label_h0,
        randomized_event_times=bool(nc.get("randomized_event_times")),
        horizons=cand.get("horizons"),
        horizon_ms=horizon_ms,
        hypothesis_id=hypothesis_id,
    )

    primary = runs.get("with_btc_node") or runs.get("without_btc_node") or {}
    min_folds = int(validation_eff.get("min_folds", 3))
    reject_reasons: list[str] = []
    if hypothesis_id == "CRYPTO_H7" and primary.get("n_folds", 0) >= validation_eff.get("min_folds", 2):
        pass
    elif not primary.get("min_folds_met"):
        reject_reasons.append("min_folds not met")
    min_purged = validation_eff.get("min_folds", 3) if hypothesis_id != "CRYPTO_H7" else min(2, validation_eff.get("min_folds", 2))
    if primary.get("n_splits", 0) < min_purged:
        reject_reasons.append("purged_cv splits below min_folds")
    if neg.get("insufficient_label_variance"):
        reject_reasons.append("insufficient label variance for controls")
    if neg.get("controls_skipped_low_signal"):
        reject_reasons.append("negative controls skipped")
    if not neg.get("shuffled_degraded"):
        reject_reasons.append("shuffled labels did not degrade")
    if not neg.get("shifted_degraded"):
        reject_reasons.append("shifted features did not degrade")
    if holdout.get("status") != "PASS":
        reject_reasons.append("holdout gate failed")
    if nc.get("randomized_event_times") and not neg.get("randomized_degraded", False):
        reject_reasons.append("randomized event times did not degrade")

    abs_ic = abs(primary.get("oos_ic_baseline_mean", 0.0))
    if abs_ic > 0.995:
        reject_reasons.append("suspiciously perfect OOS IC on fixture")

    report: dict[str, Any] = {
        "candidate_id": candidate_id,
        "hypothesis_id": hypothesis_id,
        "target": target,
        "label_horizon_ms": horizon_ms,
        "runs": runs,
        "ablation_ic_delta": ablation_delta,
        "holdout_gate": holdout,
        "negative_controls": neg,
        "walk_forward": True,
        "smoke_mode": backtest.get("validation_mode") == "fixture",
        "purged_cv_implemented": primary.get("n_splits", 0) >= (
            validation_eff.get("min_folds", 3)
            if hypothesis_id != "CRYPTO_H7"
            else min(2, validation_eff.get("min_folds", 2))
        ),
        "purged_cv_yaml_intent": bool(validation.get("purged_cv")),
        "embargo": backtest.get("embargo") or validation.get("embargo"),
        "embargo_steps": primary.get("embargo_steps"),
        "pass_fail": "pass" if not reject_reasons else "fail",
        "rejection_reason": "; ".join(reject_reasons) if reject_reasons else None,
    }

    out = Path(output_dir or repo_root_from_lane() / "research_cards" / "crypto" / candidate_id)
    out.mkdir(parents=True, exist_ok=True)
    report_path = out / "smoke_report.json"
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    report["output_report_path"] = str(report_path)
    report["backtest_config"] = backtest.get("config_id")
    return report


def run_all_smokes() -> list[dict[str, Any]]:
    from crypto_lane.src.ml.candidate_registry import discover_candidates
    return [run_smoke(c["candidate_id"]) for c in discover_candidates()]
