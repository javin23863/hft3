"""Predictive metrics for cross-asset MBO ablation (exploratory, not alpha)."""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

import numpy as np
import pandas as pd

MBO_PREDICTOR_COLS: tuple[str, ...] = (
    "liquidity_vacuum_score",
    "aggressor_volume_imbalance",
    "spread",
    "cancel_to_add_ratio",
)


def ols_r2(y: np.ndarray, x: np.ndarray) -> float:
    """R² for y ~ const + x columns; requires n > number of regressors."""
    n_params = x.shape[1] + 1
    if len(y) <= n_params:
        return float("nan")
    design = np.column_stack([np.ones(len(y)), x])
    beta, _, _, _ = np.linalg.lstsq(design, y, rcond=None)
    y_hat = design @ beta
    ss_res = float(np.sum((y - y_hat) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    if ss_tot == 0.0:
        return 0.0
    return float(1.0 - ss_res / ss_tot)


def mbo_predictive_r2(
    tensor_df: pd.DataFrame,
    targets: pd.DataFrame,
    *,
    group_canons: Optional[Sequence[str]] = None,
    horizon_sec: int = 30,
    anchor_offset_sec: int = 0,
    predictors: Iterable[str] = MBO_PREDICTOR_COLS,
) -> float:
    """
    OLS R² of forward returns on MBO snapshot predictors at anchor offset.
    One row per instrument with both snapshot and target label.
    """
    pred_cols = list(predictors)
    t0 = tensor_df[
        (tensor_df["offset_sec"] == anchor_offset_sec) & (~tensor_df["mbo_missing"])
    ]
    if group_canons:
        t0 = t0[t0["canonical_symbol"].isin(group_canons)]
    y_df = targets[targets["horizon_sec"] == horizon_sec]
    if group_canons:
        y_df = y_df[y_df["canonical_symbol"].isin(group_canons)]
    rows = []
    for _, tr in y_df.iterrows():
        canon = str(tr["canonical_symbol"])
        snap = t0[t0["canonical_symbol"] == canon]
        if snap.empty:
            continue
        r = snap.iloc[0]
        row = {c: float(r.get(c, 0.0)) for c in pred_cols}
        row["y"] = float(tr["forward_return"])
        rows.append(row)
    if len(rows) < 3:
        return float("nan")
    n = len(rows)
    frame = pd.DataFrame(rows)
    y = frame["y"].to_numpy(dtype=np.float64)
    use_cols = pred_cols[: min(len(pred_cols), max(1, n - 2))]
    x = frame[use_cols].to_numpy(dtype=np.float64)
    return ols_r2(y, x)
