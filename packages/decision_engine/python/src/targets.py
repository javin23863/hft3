"""
Leakage-safe training targets per math model Section 13.
"""
from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

HORIZONS_MS = [100, 250, 500, 1000, 5000, 15000, 60000]


def build_forward_returns(
    mid_prices: np.ndarray,
    timestamps_ns: np.ndarray,
    decision_idx: int,
    tick_size: float = 0.25,
) -> Dict[str, float]:
    """Forward mid returns at decision_idx; labels use only future timestamps."""
    t0 = timestamps_ns[decision_idx]
    m0 = mid_prices[decision_idx]
    out: Dict[str, float] = {}
    for h in HORIZONS_MS:
        target_t = t0 + h * 1_000_000
        future_idx = np.searchsorted(timestamps_ns, target_t, side="left")
        if future_idx >= len(mid_prices):
            out[f"y_return_{h}ms"] = np.nan
        else:
            if future_idx <= decision_idx:
                raise ValueError(
                    f"Label horizon {h}ms at idx {decision_idx} would use non-future data"
                )
            out[f"y_return_{h}ms"] = (mid_prices[future_idx] - m0) / tick_size
    return out


def build_labels_frame(
    events_df: pd.DataFrame,
    tick_size: float = 0.25,
) -> pd.DataFrame:
    """
    Expects columns: timestamp_ns, mid_price, filled, pnl_ticks, as_ticks, action_id.
    Adds forward return columns with leakage audit.

    Vectorized: one np.searchsorted call per horizon, O(N log N) total instead of
    O(N * H) per-row Python loops. Semantics are identical to the previous
    row-by-row implementation.
    """
    ts = events_df["timestamp_ns"].values
    mid = events_df["mid_price"].values
    n = len(events_df)

    # Monotonicity check must not be silently dropped under python -O.
    if n > 1 and not (np.diff(ts) >= 0).all():
        raise ValueError("Timestamps must be monotonic non-decreasing")

    labels = events_df.copy()

    for h in HORIZONS_MS:
        col = f"y_return_{h}ms"
        target_ts = ts + h * 1_000_000  # target wall-time for each row

        # future_idxs[i] is the first index with timestamp >= target_ts[i].
        future_idxs = np.searchsorted(ts, target_ts, side="left")

        # Rows where the horizon extends past the end of the frame → NaN.
        out_of_bounds = future_idxs >= n

        # Clip to valid range for array gather; out_of_bounds rows will be
        # overwritten with NaN afterward.
        gather_idxs = np.where(out_of_bounds, 0, future_idxs)

        values = (mid[gather_idxs] - mid) / tick_size
        values = values.astype(float)
        values[out_of_bounds] = np.nan
        labels[col] = values

    if not leakage_audit(labels):
        raise ValueError("Leakage audit failed on label frame")
    return labels


def leakage_audit(labels: pd.DataFrame, feature_ts_col: str = "timestamp_ns") -> bool:
    """Returns True if no label column uses data at or before feature time.

    Vectorized: one np.searchsorted call per label column instead of a
    per-row Python loop.
    """
    if feature_ts_col not in labels.columns:
        return False
    ts = labels[feature_ts_col].values
    n = len(labels)
    for col in labels.columns:
        if not col.startswith("y_return_"):
            continue
        horizon_ms = int(col.replace("y_return_", "").replace("ms", ""))
        values = labels[col].to_numpy(dtype=float, na_value=np.nan)
        valid_mask = ~np.isnan(values)
        if not valid_mask.any():
            continue
        # For each valid row, the label must come from a strictly future index.
        valid_indices = np.where(valid_mask)[0]
        target_ts = ts[valid_indices] + horizon_ms * 1_000_000
        future_idxs = np.searchsorted(ts, target_ts, side="left")
        # future_idx must be strictly greater than the row index; >= n is fine
        # (that means out-of-bounds, but valid_mask excludes NaN rows so those
        # won't appear here).
        if np.any(future_idxs <= valid_indices):
            return False
    return True
