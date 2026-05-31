"""Discovery / Confirmation / Holdout stage gate (BLUEPRINT B4)."""
from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl

from crypto_lane.src.labels.forward_labels import information_coefficient
from crypto_lane.src.ml.baselines import predict_baseline, train_baseline

STAGE_ORDER = ("Discovery", "Confirmation", "Holdout", "Recent holdout")
TUNE_STAGES = frozenset({"Discovery"})
EVAL_ONLY_STAGES = frozenset({"Confirmation", "Holdout", "Recent holdout"})


def _stage_mask(df: pl.DataFrame, stage: str) -> np.ndarray:
    if "validation_period" not in df.columns:
        n = df.height
        third = n // 3
        if stage == "Discovery":
            return np.arange(n) < max(1, int(n * 0.4))
        if stage == "Confirmation":
            return (np.arange(n) >= int(n * 0.4)) & (np.arange(n) < int(n * 0.7))
        return np.arange(n) >= int(n * 0.7)
    return (df["validation_period"].to_numpy() == stage)


def run_holdout_gate(
    df: pl.DataFrame,
    target: str,
    feat_cols: list[str],
    baseline_name: str,
    *,
    min_ic: float = 0.0,
) -> dict[str, Any]:
    """
    Tune on Discovery; evaluate Confirmation and Holdout with frozen Discovery model.
    """
    clean = df.drop_nulls(subset=feat_cols + [target])
    if clean.height < 8:
        return {"status": "FAIL", "reason": "insufficient rows", "stages": {}}

    X_all = clean.select(feat_cols).to_numpy()
    y_all = clean[target].to_numpy()
    finite = np.isfinite(y_all) & np.all(np.isfinite(X_all), axis=1)
    idx = np.where(finite)[0]
    if idx.size < 8:
        return {"status": "FAIL", "reason": "insufficient finite rows", "stages": {}}
    clean = clean[idx.tolist()]
    X_all = X_all[finite]
    y_all = y_all[finite]
    stages: dict[str, Any] = {}
    frozen_model = None
    frozen_kind = "regression"
    frozen_idx: list[int] = []

    for stage in STAGE_ORDER:
        mask = _stage_mask(clean, stage)
        if not mask.any():
            continue
        X_s = X_all[mask]
        y_s = y_all[mask]
        if stage in TUNE_STAGES:
            frozen_model, frozen_kind, frozen_idx = train_baseline(
                baseline_name, X_s, y_s, feat_cols
            )
            pred = predict_baseline(frozen_model, frozen_kind, frozen_idx, X_s)
            ic = information_coefficient(y_s, pred)
            stages[stage] = {
                "mode": "tune",
                "n_rows": int(mask.sum()),
                "ic": ic,
                "status": "PASS" if ic >= min_ic else "FAIL",
            }
        elif stage in EVAL_ONLY_STAGES and frozen_model is not None:
            pred = predict_baseline(frozen_model, frozen_kind, frozen_idx, X_s)
            ic = information_coefficient(y_s, pred)
            stages[stage] = {
                "mode": "evaluate_only",
                "n_rows": int(mask.sum()),
                "ic": ic,
                "status": "PASS" if ic >= min_ic else "FAIL",
            }

    if not stages:
        return {"status": "FAIL", "reason": "no stages matched", "stages": {}}

    failed = [s for s, v in stages.items() if v.get("status") == "FAIL"]
    return {
        "status": "FAIL" if failed else "PASS",
        "failed_stages": failed,
        "stages": stages,
    }
