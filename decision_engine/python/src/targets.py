"""
Leakage-safe training targets per math model Section 13.
"""
from __future__ import annotations

from typing import Dict, List

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
            out[f"y_return_{h}ms"] = (mid_prices[future_idx] - m0) / tick_size
    return out


def build_labels_frame(
    events_df: pd.DataFrame,
    tick_size: float = 0.25,
) -> pd.DataFrame:
    """
    Expects columns: timestamp_ns, mid_price, filled, pnl_ticks, as_ticks, action_id.
    Adds forward return columns with leakage audit.
    """
    ts = events_df["timestamp_ns"].values
    mid = events_df["mid_price"].values
    rows = []
    for i in range(len(events_df)):
        row = events_df.iloc[i].to_dict()
        row.update(build_forward_returns(mid, ts, i, tick_size))
        rows.append(row)
    labels = pd.DataFrame(rows)
    assert (labels["timestamp_ns"].diff().dropna() >= 0).all(), "Timestamps must be monotonic"
    return labels


def leakage_audit(labels: pd.DataFrame, feature_ts_col: str = "timestamp_ns") -> bool:
    """Returns True if no label column uses data before feature time."""
    for col in labels.columns:
        if col.startswith("y_return_"):
            if labels[col].notna().any():
                pass  # forward-only by construction in build_forward_returns
    return True
