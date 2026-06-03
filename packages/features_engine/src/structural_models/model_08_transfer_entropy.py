"""PDF_MODEL_8 — Transfer Entropy lead-lag (hft_framework_developer_prompt.pdf Module 1)."""

from __future__ import annotations

import math
from typing import Any, List, Optional, Sequence

import numpy as np

from .base import BaseStructuralModel, ModelOutput
from .types import TransferEntropyOutput


def shannon_entropy(values: Sequence[float], bins: int = 16) -> float:
    """Discrete Shannon entropy H(X) = -sum p log p."""
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return 0.0
    counts, _ = np.histogram(arr, bins=bins)
    total = float(counts.sum())
    if total <= 0:
        return 0.0
    probs = counts[counts > 0] / total
    return float(-np.sum(probs * np.log(probs)))


def conditional_entropy(y: Sequence[float], x: Sequence[float], bins: int = 16) -> float:
    """H(Y|X) via discrete joint/marginal histograms."""
    y_arr = np.asarray(y, dtype=float)
    x_arr = np.asarray(x, dtype=float)
    n = min(len(y_arr), len(x_arr))
    if n == 0:
        return 0.0
    y_arr = y_arr[:n]
    x_arr = x_arr[:n]
    joint, _, _ = np.histogram2d(x_arr, y_arr, bins=bins)
    joint = joint / max(joint.sum(), 1e-12)
    px = joint.sum(axis=1)
    h = 0.0
    for i in range(bins):
        if px[i] <= 0:
            continue
        for j in range(bins):
            pxy = joint[i, j]
            if pxy <= 0:
                continue
            h -= pxy * math.log(pxy / px[i])
    return max(h, 0.0)


def conditional_entropy_3d(
    y: Sequence[float],
    x1: Sequence[float],
    x2: Sequence[float],
    bins: int = 16,
) -> float:
    """H(Y | X1, X2) via 3D joint histogram."""
    y_arr = np.asarray(y, dtype=float)
    x1_arr = np.asarray(x1, dtype=float)
    x2_arr = np.asarray(x2, dtype=float)
    n = min(len(y_arr), len(x1_arr), len(x2_arr))
    if n == 0:
        return 0.0
    y_arr = y_arr[:n]
    x1_arr = x1_arr[:n]
    x2_arr = x2_arr[:n]
    joint_3d, _ = np.histogramdd(
        np.column_stack([x1_arr, x2_arr, y_arr]), bins=bins
    )
    total = joint_3d.sum()
    if total <= 0:
        return 0.0
    joint_3d = joint_3d / total
    p_x1x2 = joint_3d.sum(axis=2)
    h = 0.0
    for i in range(bins):
        for j in range(bins):
            px = p_x1x2[i, j]
            if px <= 0:
                continue
            for k in range(bins):
                pxyz = joint_3d[i, j, k]
                if pxyz <= 0:
                    continue
                h -= pxyz * math.log(pxyz / px)
    return max(h, 0.0)


def transfer_entropy(
    x: Sequence[float],
    y: Sequence[float],
    lag: int = 1,
    bins: int = 16,
) -> float:
    """
    TE_{X->Y}(k) = H(Y_t | Y_{t-k}) - H(Y_t | X_{t-k}, Y_{t-k}).
    """
    if lag < 1:
        lag = 1
    x_arr = np.asarray(x, dtype=float)
    y_arr = np.asarray(y, dtype=float)
    n = min(len(x_arr), len(y_arr))
    if n <= lag + 2:
        return 0.0
    y_t = y_arr[lag:n]
    y_lag = y_arr[: n - lag]
    x_lag = x_arr[: n - lag]
    h_y_given_ylag = conditional_entropy(y_t, y_lag, bins=bins)
    h_y_given_xylag = conditional_entropy_3d(y_t, x_lag, y_lag, bins=bins)
    return max(h_y_given_ylag - h_y_given_xylag, 0.0)


def te_matrix(
    series_by_asset: dict[str, Sequence[float]],
    lag: int = 1,
    bins: int = 16,
) -> dict[tuple[str, str], float]:
    assets = list(series_by_asset.keys())
    out: dict[tuple[str, str], float] = {}
    for leader in assets:
        for target in assets:
            if leader == target:
                continue
            out[(leader, target)] = transfer_entropy(
                series_by_asset[leader], series_by_asset[target], lag=lag, bins=bins
            )
    return out


class TransferEntropyModel(BaseStructuralModel[TransferEntropyOutput]):
    model_id = "TRANSFER_ENTROPY"

    def __init__(self, params: Optional[dict] = None):
        cfg = (params or {}).get("transfer_entropy", {})
        self.lag = int(cfg.get("lag", 1))
        self.bins = int(cfg.get("bins", 16))
        self.ucl_multiplier = float(cfg.get("ucl_multiplier", 2.0))
        self.baseline_window = int(cfg.get("baseline_window", 50))
        self._te_history: List[float] = []

    def evaluate(self, **kwargs: Any) -> ModelOutput[TransferEntropyOutput]:
        leader = str(kwargs.get("leader_asset", "ES"))
        target = str(kwargs.get("target_asset", "MES"))
        x_series: List[float] = list(kwargs.get("leader_returns", []))
        y_series: List[float] = list(kwargs.get("target_returns", []))
        series_by_asset = kwargs.get("returns_by_asset")
        if series_by_asset:
            te = te_matrix(series_by_asset, lag=self.lag, bins=self.bins)
            te_val = te.get((leader, target), 0.0)
        else:
            te_val = transfer_entropy(x_series, y_series, lag=self.lag, bins=self.bins)

        if te_val > 0:
            self._te_history.append(te_val)
            if len(self._te_history) > self.baseline_window:
                self._te_history = self._te_history[-self.baseline_window:]
        if len(self._te_history) >= 2:
            arr = np.array(self._te_history, dtype=float)
            ucl = float(arr.mean() + self.ucl_multiplier * arr.std())
        elif len(self._te_history) == 1:
            ucl = self._te_history[0] * self.ucl_multiplier
        else:
            ucl = te_val
        aggressive = te_val > ucl and te_val > 0

        payload = TransferEntropyOutput(
            leader_asset=leader,
            target_asset=target,
            lag=self.lag,
            transfer_entropy=te_val,
            te_upper_control_limit=ucl,
            aggressive_liquidity_signal=aggressive,
        )
        return ModelOutput(
            model_id=self.model_id,
            timestamp_ns=int(kwargs.get("timestamp_ns", 0)),
            payload=payload,
        )
