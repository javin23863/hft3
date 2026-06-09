"""Regression tests for the vectorized build_labels_frame and leakage_audit.

Verifies:
- build_labels_frame produces identical output to the old per-row semantics on a
  small synthetic frame (hardcoded expected values).
- NaN edge behaviour at the tail of the frame where a horizon runs past the data.
- leakage_audit returns True on a clean frame and False on a deliberately leaked
  label column.
- Monotonicity check raises ValueError (survives python -O; not a bare assert).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from decision_engine.python.src.targets import (
    HORIZONS_MS,
    build_labels_frame,
    leakage_audit,
)

TICK = 0.25

# ---------------------------------------------------------------------------
# Synthetic frame: 6 events spaced 200 ms apart (200_000_000 ns).
# With the 100 ms horizon all rows land inside the frame; with 500 ms and
# beyond, the last row(s) will be out-of-range → NaN.
# ---------------------------------------------------------------------------
_T0 = 1_700_000_000_000_000_000  # arbitrary epoch anchor
_SPACING_NS = 200_000_000        # 200 ms per tick
_N = 6
_TS = np.array([_T0 + i * _SPACING_NS for i in range(_N)], dtype=np.int64)
_MID = np.array([100.0, 100.5, 101.0, 100.75, 101.25, 101.5], dtype=float)


def _make_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp_ns": _TS,
            "mid_price": _MID,
            "filled": [False] * _N,
            "pnl_ticks": [0.0] * _N,
            "as_ticks": [0.0] * _N,
            "action_id": list(range(_N)),
        }
    )


def _old_build_labels_frame(events_df: pd.DataFrame, tick_size: float = TICK) -> pd.DataFrame:
    """Reference implementation that replicates the original per-row loop."""
    ts = events_df["timestamp_ns"].values
    mid = events_df["mid_price"].values
    rows = []
    for i in range(len(events_df)):
        row = events_df.iloc[i].to_dict()
        for h in HORIZONS_MS:
            target_t = ts[i] + h * 1_000_000
            future_idx = int(np.searchsorted(ts, target_t, side="left"))
            if future_idx >= len(mid):
                row[f"y_return_{h}ms"] = np.nan
            else:
                row[f"y_return_{h}ms"] = (mid[future_idx] - mid[i]) / tick_size
        rows.append(row)
    return pd.DataFrame(rows)


class TestBuildLabelsFrameSemantics:
    def test_output_columns_match_reference(self):
        df = _make_frame()
        result = build_labels_frame(df, tick_size=TICK)
        reference = _old_build_labels_frame(df, tick_size=TICK)
        for col in [f"y_return_{h}ms" for h in HORIZONS_MS]:
            assert col in result.columns, f"Missing column {col}"
            np.testing.assert_array_almost_equal(
                result[col].values,
                reference[col].values,
                decimal=10,
                err_msg=f"Mismatch in {col}",
            )

    def test_passthrough_columns_unchanged(self):
        df = _make_frame()
        result = build_labels_frame(df, tick_size=TICK)
        np.testing.assert_array_equal(result["timestamp_ns"].values, df["timestamp_ns"].values)
        np.testing.assert_array_equal(result["mid_price"].values, df["mid_price"].values)

    def test_tail_nan_edge_behavior(self):
        """Rows whose horizon exceeds the last timestamp must be NaN."""
        df = _make_frame()
        result = build_labels_frame(df, tick_size=TICK)
        # 60 s horizon: all rows are out of range (frame only spans ~1 s)
        h60 = "y_return_60000ms"
        assert result[h60].isna().all(), "Expected all NaN for 60 s horizon"

        # 100 ms horizon with 200 ms spacing: future_idx = row+1 for rows 0..4,
        # row 5 (last) maps to idx 6 >= N → NaN only for last row.
        h100 = "y_return_100ms"
        assert not result[h100].iloc[:-1].isna().any(), "Non-tail rows should not be NaN for 100 ms"
        assert pd.isna(result[h100].iloc[-1]), "Last row should be NaN for 100 ms (no future data)"

    def test_hardcoded_100ms_values(self):
        """Spot-check concrete values against manually computed expected results."""
        df = _make_frame()
        result = build_labels_frame(df, tick_size=TICK)
        # Row 0: mid=100.0, future at 100 ms (idx 1, mid=100.5) → (100.5-100.0)/0.25 = 2.0
        assert result["y_return_100ms"].iloc[0] == pytest.approx(2.0)
        # Row 1: mid=100.5, future at 100 ms (idx 2, mid=101.0) → 2.0
        assert result["y_return_100ms"].iloc[1] == pytest.approx(2.0)
        # Row 4: mid=101.25, future at 100 ms (idx 5, mid=101.5) → 1.0
        assert result["y_return_100ms"].iloc[4] == pytest.approx(1.0)

    def test_250ms_horizon_skips_one_row(self):
        """250 ms horizon with 200 ms spacing lands at row+2 for most rows."""
        df = _make_frame()
        result = build_labels_frame(df, tick_size=TICK)
        # Row 0: idx for ts[0]+250ms = T0+250ms; searchsorted → idx 2 (T0+400ms), mid=101.0
        # (100ms < 250ms < 400ms, so idx=2 is first ts >= T0+250ms)
        # Actually ts[1]=T0+200ms < T0+250ms, ts[2]=T0+400ms >= T0+250ms → idx=2
        expected_row0 = (101.0 - 100.0) / TICK  # = 4.0
        assert result["y_return_250ms"].iloc[0] == pytest.approx(expected_row0)


class TestMonotonicityCheck:
    def test_non_monotonic_raises_value_error(self):
        """Monotonicity check must raise ValueError, not assert (survives -O)."""
        df = _make_frame()
        # Reverse timestamps to break monotonicity
        df = df.iloc[::-1].reset_index(drop=True)
        with pytest.raises(ValueError, match="monotonic"):
            build_labels_frame(df)

    def test_equal_timestamps_allowed(self):
        """Equal timestamps (non-strictly-monotonic) must pass."""
        df = _make_frame()
        # Duplicate a row to create equal consecutive timestamps
        df.loc[1, "timestamp_ns"] = df.loc[0, "timestamp_ns"]
        # sort to keep monotonic
        df = df.sort_values("timestamp_ns").reset_index(drop=True)
        # Should not raise
        build_labels_frame(df)


class TestLeakageAudit:
    def test_clean_frame_passes(self):
        df = _make_frame()
        labels = build_labels_frame(df)
        assert leakage_audit(labels)

    def test_deliberately_leaked_label_detected(self):
        """A label set to the current-row mid (future_idx == i) must be caught."""
        df = _make_frame()
        labels = build_labels_frame(df.copy())
        # Inject a leaked column: y_return_100ms using same-row mid → return = 0
        # and the horizon would resolve to the same index.
        # Simulate by setting a label column that references ts[i] itself (not future).
        ts = labels["timestamp_ns"].values
        # y_return_fakeHz: for every row set target_t = ts[i] so future_idx == i.
        leaked_col = "y_return_0ms"  # horizon 0 → target_t == ts[i]
        labels[leaked_col] = 0.0  # non-NaN values at all rows
        assert not leakage_audit(labels), "Leaked label column must be detected"

    def test_missing_timestamp_col_returns_false(self):
        df = _make_frame().rename(columns={"timestamp_ns": "event_ts"})
        # leakage_audit with wrong feature_ts_col should return False
        assert not leakage_audit(df)

    def test_all_nan_column_passes(self):
        """A label column that is entirely NaN should not cause a false failure."""
        df = _make_frame()
        labels = build_labels_frame(df)
        # Overwrite the 60 s column (already all NaN) — should still pass.
        assert leakage_audit(labels)
