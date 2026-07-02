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


def build_triple_barrier_labels(
    mid_prices: np.ndarray,
    timestamps_ns: np.ndarray,
    *,
    pt_ticks: float,
    sl_ticks: float,
    max_holding_ms: int,
    tick_size: float = 0.25,
    sides: np.ndarray | None = None,
) -> pd.DataFrame:
    """Lopez de Prado triple-barrier labels; strictly-future exits only.

    For each row: profit-take barrier at ``pt_ticks`` in the trade direction,
    stop barrier at ``sl_ticks`` against it, vertical barrier at
    ``max_holding_ms``. ``sides`` is +1 long / -1 short per row (default all
    long). Returns a frame with:

    - ``y_tb_outcome``: +1 profit barrier, -1 stop barrier, 0 vertical
      (timeout), NaN when no future data exists inside the window.
    - ``y_tb_return_ticks``: signed exit return in ticks in trade direction.
    - ``y_tb_exit_idx`` / ``y_tb_holding_ms``: exit bookkeeping.

    Exits are always taken from indices strictly greater than the row index —
    same filtration discipline as ``build_forward_returns``.
    """
    if pt_ticks <= 0 or sl_ticks <= 0:
        raise ValueError("pt_ticks and sl_ticks must be positive")
    if max_holding_ms <= 0:
        raise ValueError("max_holding_ms must be positive")
    ts = np.asarray(timestamps_ns, dtype=np.int64)
    mid = np.asarray(mid_prices, dtype=np.float64)
    n = len(ts)
    if n > 1 and not (np.diff(ts) >= 0).all():
        raise ValueError("Timestamps must be monotonic non-decreasing")
    side_arr = np.ones(n, dtype=np.float64) if sides is None else np.asarray(sides, dtype=np.float64)
    if len(side_arr) != n:
        raise ValueError("sides must match mid_prices length")
    if not np.isin(side_arr, (-1.0, 1.0)).all():
        raise ValueError("sides must be +1 or -1")

    horizon_idx = np.searchsorted(ts, ts + int(max_holding_ms) * 1_000_000, side="left")
    outcome = np.full(n, np.nan)
    ret_ticks = np.full(n, np.nan)
    exit_idx = np.full(n, -1, dtype=np.int64)

    for i in range(n):
        start = i + 1  # strictly future
        end = int(horizon_idx[i])  # first index with ts >= t0 + max_holding_ms
        if start >= n:
            continue
        # The vertical-barrier row itself is INCLUDED in the scan: a barrier
        # first crossed exactly at the horizon timestamp is a barrier hit, and
        # the timeout exit executes at that row (first observation at/after
        # the horizon), not at the previous tick.
        end_incl = min(end, n - 1)
        window = mid[start : end_incl + 1]
        if len(window) == 0:
            continue
        move_ticks = side_arr[i] * (window - mid[i]) / tick_size
        hit_pt = np.flatnonzero(move_ticks >= pt_ticks)
        hit_sl = np.flatnonzero(move_ticks <= -sl_ticks)
        first_pt = hit_pt[0] if len(hit_pt) else np.inf
        first_sl = hit_sl[0] if len(hit_sl) else np.inf
        if first_pt == np.inf and first_sl == np.inf:
            if end > n - 1:
                # Horizon extends beyond recorded data and no barrier hit:
                # the timeout outcome is censored — leave NaN rather than
                # inventing an early exit.
                continue
            j = len(window) - 1
            outcome[i] = 0.0
        elif first_sl <= first_pt:
            j = int(first_sl)
            outcome[i] = -1.0
        else:
            j = int(first_pt)
            outcome[i] = 1.0
        exit_i = start + j
        exit_idx[i] = exit_i
        ret_ticks[i] = side_arr[i] * (mid[exit_i] - mid[i]) / tick_size

    holding_ms = np.where(
        exit_idx >= 0, (ts[np.clip(exit_idx, 0, n - 1)] - ts) / 1_000_000.0, np.nan
    )
    frame = pd.DataFrame(
        {
            "timestamp_ns": ts,
            "y_tb_outcome": outcome,
            "y_tb_return_ticks": ret_ticks,
            "y_tb_exit_idx": exit_idx,
            "y_tb_holding_ms": holding_ms,
        }
    )
    # Filtration check: every exit index is strictly future.
    valid = exit_idx >= 0
    if np.any(exit_idx[valid] <= np.flatnonzero(valid)):
        raise ValueError("triple-barrier exit would use non-future data")
    return frame


def build_meta_labels(
    primary_signal: np.ndarray,
    tb_outcome: np.ndarray,
) -> np.ndarray:
    """Meta-labels: should the primary signal's trade be taken?

    1.0 when the primary signal is nonzero and the triple-barrier outcome in
    the SIGNAL's direction hit the profit barrier; 0.0 when the signal fired
    but the barrier outcome was stop/timeout; NaN when no signal fired or the
    barrier outcome is unknown. ``tb_outcome`` must be computed with
    ``sides=np.sign(primary_signal)`` so directions agree.
    """
    signal = np.asarray(primary_signal, dtype=np.float64)
    outcome = np.asarray(tb_outcome, dtype=np.float64)
    if len(signal) != len(outcome):
        raise ValueError("primary_signal and tb_outcome must be the same length")
    meta = np.full(len(signal), np.nan)
    fired = (signal != 0) & ~np.isnan(outcome)
    meta[fired] = (outcome[fired] > 0).astype(np.float64)
    return meta


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
