"""Tests for the last-30-minutes ES momentum revalidation harness (WS-1.3).

Conventions:
- sys.path setup mirrors test_fixing_window_study.py
- Synthetic NPZ files are built with the same minimal structured array shape
  used in test_fixing_window_study.py: {ev, local_ts, px, qty, order_id}
- HFT3_NPZ_ROOT is pointed to tmp_path so tests never touch data/npz/
- All golden values derived by hand with formulas documented inline.
"""

from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# sys.path bootstrap — same pattern as test_fixing_window_study.py
# ---------------------------------------------------------------------------
_REPO = Path(__file__).resolve().parents[2]
_PACKAGES = _REPO / "packages"
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_PACKAGES))

from options_lane.studies.last30_momentum_study import (
    _aggregate,
    _count_trades,
    _ct_hms_to_utc_ns,
    _default_out,
    _is_buy_aggressor,
    _is_sell_aggressor,
    _is_trade,
    _last_trade_at_or_before,
    _momentum_bounds_ns,
    measure_file,
    run_inventory,
    run_measure,
)

# ---------------------------------------------------------------------------
# Shared dtype and helpers — identical to test_fixing_window_study.py
# ---------------------------------------------------------------------------

_NPZ_DTYPE = np.dtype(
    [
        ("ev", np.int64),
        ("local_ts", np.int64),
        ("px", np.float64),
        ("qty", np.int64),
        ("order_id", np.int64),
    ]
)

_TRADE_BASE = 2
_FILL_BASE = 13
_BUY_FLAG = 1 << 29   # resting bid = sell aggressor
_SELL_FLAG = 1 << 28  # resting ask = buy aggressor
_ADD_BASE = 10        # ADD_ORDER_EVENT — non-trade


def _trade_ev(buy_aggressor: bool) -> int:
    """Compose trade event with appropriate flag.

    buy_aggressor=True  → SELL_FLAG set (buyer lifted the ask)
    buy_aggressor=False → BUY_FLAG set  (seller hit the bid)
    """
    if buy_aggressor:
        return _TRADE_BASE | _SELL_FLAG
    else:
        return _TRADE_BASE | _BUY_FLAG


def _ns(date_str: str, h: int, m: int, s: int) -> int:
    """Return UTC ns for America/Chicago wall-clock time on the given date."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("America/Chicago")
    y, mo, d = map(int, date_str.split("-"))
    naive = datetime(y, mo, d, h, m, s)
    aware = naive.replace(tzinfo=tz)
    return int(aware.timestamp() * 1_000_000_000)


def _make_npz(
    rows: list[dict[str, Any]],
    tmp_path: Path,
    filename: str = "ES.v.0_TEST_mbo.npz",
) -> Path:
    """Build a minimal structured-array NPZ and save it to tmp_path/filename."""
    arr = np.zeros(len(rows), dtype=_NPZ_DTYPE)
    for i, r in enumerate(rows):
        arr[i]["ev"] = r.get("ev", _ADD_BASE)
        arr[i]["local_ts"] = r.get("local_ts", 0)
        arr[i]["px"] = r.get("px", 0.0)
        arr[i]["qty"] = r.get("qty", 1)
        arr[i]["order_id"] = r.get("order_id", i)
    path = tmp_path / filename
    np.savez(str(path), data=arr)
    return path


def _make_manifest(entries: list[dict[str, Any]], root: Path) -> None:
    (root / "manifest.json").write_text(json.dumps(entries), encoding="utf-8")


# ---------------------------------------------------------------------------
# Unit: event-flag helpers — same conventions as fixing_window_study
# ---------------------------------------------------------------------------

def test_is_trade_returns_true_for_trade_event() -> None:
    assert _is_trade(_trade_ev(True))


def test_is_trade_returns_false_for_non_trade() -> None:
    assert not _is_trade(_ADD_BASE)


def test_buy_aggressor_sell_flag_set() -> None:
    ev = _trade_ev(True)  # buyer lifted the ask → SELL_FLAG
    assert _is_buy_aggressor(ev)
    assert not _is_sell_aggressor(ev)


def test_sell_aggressor_buy_flag_set() -> None:
    ev = _trade_ev(False)  # seller hit the bid → BUY_FLAG
    assert _is_sell_aggressor(ev)
    assert not _is_buy_aggressor(ev)


# ---------------------------------------------------------------------------
# Unit: _last_trade_at_or_before
# ---------------------------------------------------------------------------

def test_last_trade_at_or_before_boundary_inclusive() -> None:
    """A trade exactly AT the upper boundary belongs to the window."""
    date = "2024-06-12"
    signal_end = _ns(date, 14, 30, 0)

    arr = np.zeros(2, dtype=_NPZ_DTYPE)
    # Trade exactly at 14:30:00 — should be included
    arr[0]["ev"] = _trade_ev(True)
    arr[0]["local_ts"] = signal_end
    arr[0]["px"] = 5000.0
    arr[0]["qty"] = 1
    # Trade one nanosecond after — should NOT be included
    arr[1]["ev"] = _trade_ev(True)
    arr[1]["local_ts"] = signal_end + 1
    arr[1]["px"] = 9999.0
    arr[1]["qty"] = 1

    px = _last_trade_at_or_before(arr, signal_end)
    assert px == 5000.0, "Trade at boundary must be included"


def test_last_trade_at_or_before_forward_fill() -> None:
    """When no trade exists exactly at ts_hi, the most recent trade before it is returned."""
    date = "2024-06-12"
    signal_start = _ns(date, 8, 30, 0)

    arr = np.zeros(1, dtype=_NPZ_DTYPE)
    arr[0]["ev"] = _trade_ev(True)
    arr[0]["local_ts"] = signal_start - 60_000_000_000  # 1 min before
    arr[0]["px"] = 4950.0
    arr[0]["qty"] = 1

    # Call with ts_lo=0 to allow scanning before signal_start
    px = _last_trade_at_or_before(arr, signal_start)
    assert px == 4950.0, "Must forward-fill from last trade before boundary"


def test_last_trade_at_or_before_no_trades_returns_nan() -> None:
    arr = np.zeros(1, dtype=_NPZ_DTYPE)
    arr[0]["ev"] = _ADD_BASE  # non-trade
    arr[0]["local_ts"] = 1_000_000
    arr[0]["px"] = 5000.0
    assert math.isnan(_last_trade_at_or_before(arr, 10_000_000_000))


# ---------------------------------------------------------------------------
# Unit: no-lookahead — trade at EXACTLY 14:30 belongs to SIGNAL window, not target
# ---------------------------------------------------------------------------

def test_no_lookahead_boundary_trade_belongs_to_signal_window() -> None:
    """Trade at exactly 14:30:00 CT must be the signal_end price.

    The target window requires local_ts > signal_end (strictly after).
    A trade at signal_end nanosecond should NOT appear as target_end_px;
    the target window must have its own trade strictly after 14:30:00 CT.
    """
    date = "2024-06-12"
    signal_start_ns = _ns(date, 8, 30, 0)
    signal_end_ns = _ns(date, 14, 30, 0)  # boundary

    arr = np.zeros(3, dtype=_NPZ_DTYPE)
    # Trade at open
    arr[0]["ev"] = _trade_ev(True)
    arr[0]["local_ts"] = signal_start_ns
    arr[0]["px"] = 5000.0
    arr[0]["qty"] = 1
    # Trade exactly at boundary — belongs to signal window
    arr[1]["ev"] = _trade_ev(True)
    arr[1]["local_ts"] = signal_end_ns
    arr[1]["px"] = 5010.0
    arr[1]["qty"] = 1
    # NO trade after 14:30 — target window has no data

    file_date_utc = datetime(2024, 6, 12, 13, 0, 0, tzinfo=timezone.utc)
    rec = measure_file(arr, "ES.v.0", "TEST", file_date_utc)

    # Signal window covered (trades from 08:30 to 14:30 inclusive)
    assert rec["has_signal_coverage"] is True
    # Target window NOT covered (no trade strictly after 14:30)
    assert rec["has_target_coverage"] is False
    # No PnL computed
    assert rec["net_ticks"] is None
    assert rec["signal_ret"] is None


# ---------------------------------------------------------------------------
# Unit: signal/target return computation
# ---------------------------------------------------------------------------

def test_signal_and_target_return_hand_computed() -> None:
    """Golden-value test for log return computation.

    Setup:
      signal_start_px = 5000.0  (trade at 08:30)
      signal_end_px   = 5050.0  (trade at 14:30)
      target_end_px   = 5075.0  (trade at 14:45)

    signal_ret = ln(5050/5000)  = ln(1.010) ≈ 0.009950330853...
    target_ret = ln(5075/5050)  = ln(1.004950...) ≈ 0.004938271...

    direction = sign(signal_ret) = +1
    target_move_pts = 5075 - 5050 = 25 pts
    gross_ticks = +1 * 25 / 0.25 = 100 ticks
    net_ticks   = 100 - 1.3 = 98.7 ticks
    """
    date = "2024-06-12"
    signal_start_ns = _ns(date, 8, 30, 0)
    signal_end_ns = _ns(date, 14, 30, 0)
    target_ns = _ns(date, 14, 45, 0)

    arr = np.zeros(3, dtype=_NPZ_DTYPE)
    arr[0]["ev"] = _trade_ev(True)
    arr[0]["local_ts"] = signal_start_ns
    arr[0]["px"] = 5000.0
    arr[0]["qty"] = 1

    arr[1]["ev"] = _trade_ev(True)
    arr[1]["local_ts"] = signal_end_ns
    arr[1]["px"] = 5050.0
    arr[1]["qty"] = 1

    arr[2]["ev"] = _trade_ev(True)
    arr[2]["local_ts"] = target_ns
    arr[2]["px"] = 5075.0
    arr[2]["qty"] = 1

    file_date_utc = datetime(2024, 6, 12, 13, 0, 0, tzinfo=timezone.utc)
    rec = measure_file(arr, "ES.v.0", "TEST", file_date_utc, cost_ticks=1.3)

    expected_signal_ret = math.log(5050.0 / 5000.0)
    expected_target_ret = math.log(5075.0 / 5050.0)
    expected_gross = 100.0  # (5075 - 5050) / 0.25
    expected_net = 98.7

    assert rec["has_signal_coverage"] is True
    assert rec["has_target_coverage"] is True
    assert abs(rec["signal_ret"] - expected_signal_ret) < 1e-9
    assert abs(rec["target_ret"] - expected_target_ret) < 1e-9
    assert abs(rec["gross_ticks"] - expected_gross) < 1e-9
    assert abs(rec["net_ticks"] - expected_net) < 1e-9


def test_negative_signal_short_direction() -> None:
    """Negative signal → short position → gain when target falls.

    Setup:
      signal_start_px = 5100.0  (trade at 08:30)
      signal_end_px   = 5000.0  (trade at 14:30) → signal_ret < 0
      target_end_px   = 4980.0  (trade at 14:45) → target falls

    direction = -1
    target_move_pts = 4980 - 5000 = -20 pts
    gross_ticks = -1 * (-20) / 0.25 = +80 ticks
    net_ticks   = 80 - 1.3 = 78.7
    """
    date = "2024-06-12"
    signal_start_ns = _ns(date, 8, 30, 0)
    signal_end_ns = _ns(date, 14, 30, 0)
    target_ns = _ns(date, 14, 45, 0)

    arr = np.zeros(3, dtype=_NPZ_DTYPE)
    arr[0]["ev"] = _trade_ev(False)
    arr[0]["local_ts"] = signal_start_ns
    arr[0]["px"] = 5100.0
    arr[0]["qty"] = 1

    arr[1]["ev"] = _trade_ev(False)
    arr[1]["local_ts"] = signal_end_ns
    arr[1]["px"] = 5000.0
    arr[1]["qty"] = 1

    arr[2]["ev"] = _trade_ev(False)
    arr[2]["local_ts"] = target_ns
    arr[2]["px"] = 4980.0
    arr[2]["qty"] = 1

    file_date_utc = datetime(2024, 6, 12, 13, 0, 0, tzinfo=timezone.utc)
    rec = measure_file(arr, "ES.v.0", "TEST", file_date_utc, cost_ticks=1.3)

    assert rec["signal_ret"] < 0
    assert abs(rec["gross_ticks"] - 80.0) < 1e-9
    assert abs(rec["net_ticks"] - 78.7) < 1e-9


def test_zero_signal_no_cost_incurred() -> None:
    """When signal_ret == 0, direction == 0 → no position → net_ticks must be 0.0.

    Setup:
      signal_start_px = 5000.0  (08:30)
      signal_end_px   = 5000.0  (14:30) — exactly flat signal
      target_end_px   = 5010.0  (14:45)

    signal_ret = ln(5000/5000) = 0.0  → direction = 0
    gross_ticks = 0 * (5010 - 5000) / 0.25 = 0.0
    net_ticks   = 0.0  (no position taken, no cost incurred)
    """
    date = "2024-06-12"
    signal_start_ns = _ns(date, 8, 30, 0)
    signal_end_ns = _ns(date, 14, 30, 0)
    target_ns = _ns(date, 14, 45, 0)

    arr = np.zeros(3, dtype=_NPZ_DTYPE)
    arr[0]["ev"] = _trade_ev(True)
    arr[0]["local_ts"] = signal_start_ns
    arr[0]["px"] = 5000.0
    arr[0]["qty"] = 1
    arr[1]["ev"] = _trade_ev(True)
    arr[1]["local_ts"] = signal_end_ns
    arr[1]["px"] = 5000.0  # flat — signal_ret == 0
    arr[1]["qty"] = 1
    arr[2]["ev"] = _trade_ev(True)
    arr[2]["local_ts"] = target_ns
    arr[2]["px"] = 5010.0
    arr[2]["qty"] = 1

    file_date_utc = datetime(2024, 6, 12, 13, 0, 0, tzinfo=timezone.utc)
    rec = measure_file(arr, "ES.v.0", "TEST", file_date_utc, cost_ticks=1.3)

    assert rec["has_signal_coverage"] is True
    assert rec["has_target_coverage"] is True
    assert rec["signal_ret"] == 0.0
    assert rec["gross_ticks"] == 0.0
    # No position taken → no round-trip cost
    assert rec["net_ticks"] == 0.0


# ---------------------------------------------------------------------------
# Unit: cost arithmetic
# ---------------------------------------------------------------------------

def test_cost_ticks_parameter_applied() -> None:
    """net_ticks = gross_ticks - cost_ticks for custom cost value."""
    date = "2024-06-12"
    signal_start_ns = _ns(date, 8, 30, 0)
    signal_end_ns = _ns(date, 14, 30, 0)
    target_ns = _ns(date, 14, 45, 0)

    arr = np.zeros(3, dtype=_NPZ_DTYPE)
    arr[0]["ev"] = _trade_ev(True)
    arr[0]["local_ts"] = signal_start_ns
    arr[0]["px"] = 5000.0
    arr[0]["qty"] = 1
    arr[1]["ev"] = _trade_ev(True)
    arr[1]["local_ts"] = signal_end_ns
    arr[1]["px"] = 5010.0
    arr[1]["qty"] = 1
    arr[2]["ev"] = _trade_ev(True)
    arr[2]["local_ts"] = target_ns
    arr[2]["px"] = 5014.0
    arr[2]["qty"] = 1

    # gross = (5014 - 5010) / 0.25 = 16 ticks
    file_date_utc = datetime(2024, 6, 12, 13, 0, 0, tzinfo=timezone.utc)
    rec_default = measure_file(arr, "ES.v.0", "TEST", file_date_utc, cost_ticks=1.3)
    rec_custom = measure_file(arr, "ES.v.0", "TEST", file_date_utc, cost_ticks=2.5)

    assert abs(rec_default["gross_ticks"] - 16.0) < 1e-9
    assert abs(rec_default["net_ticks"] - 14.7) < 1e-9
    assert abs(rec_custom["net_ticks"] - 13.5) < 1e-9


# ---------------------------------------------------------------------------
# Unit: coverage classification
# ---------------------------------------------------------------------------

def test_coverage_both_windows_present() -> None:
    date = "2024-06-12"
    arr = np.zeros(3, dtype=_NPZ_DTYPE)
    arr[0]["ev"] = _trade_ev(True)
    arr[0]["local_ts"] = _ns(date, 8, 30, 0)
    arr[0]["px"] = 5000.0
    arr[0]["qty"] = 1
    arr[1]["ev"] = _trade_ev(True)
    arr[1]["local_ts"] = _ns(date, 14, 30, 0)
    arr[1]["px"] = 5010.0
    arr[1]["qty"] = 1
    arr[2]["ev"] = _trade_ev(True)
    arr[2]["local_ts"] = _ns(date, 14, 45, 0)
    arr[2]["px"] = 5015.0
    arr[2]["qty"] = 1

    file_date_utc = datetime(2024, 6, 12, 13, 0, 0, tzinfo=timezone.utc)
    rec = measure_file(arr, "ES.v.0", "TEST", file_date_utc)

    assert rec["has_signal_coverage"] is True
    assert rec["has_target_coverage"] is True


def test_coverage_missing_signal_window() -> None:
    """File with only target-window data → signal not covered."""
    date = "2024-06-12"
    arr = np.zeros(2, dtype=_NPZ_DTYPE)
    # Only trades in target window
    arr[0]["ev"] = _trade_ev(True)
    arr[0]["local_ts"] = _ns(date, 14, 35, 0)
    arr[0]["px"] = 5010.0
    arr[0]["qty"] = 1
    arr[1]["ev"] = _trade_ev(True)
    arr[1]["local_ts"] = _ns(date, 14, 55, 0)
    arr[1]["px"] = 5015.0
    arr[1]["qty"] = 1

    file_date_utc = datetime(2024, 6, 12, 13, 0, 0, tzinfo=timezone.utc)
    rec = measure_file(arr, "ES.v.0", "TEST", file_date_utc)

    # No signal_start_px available (no trade at or before 08:30)
    assert rec["has_signal_coverage"] is False
    assert rec["net_ticks"] is None


def test_coverage_missing_target_window() -> None:
    """File with only signal-window data → target not covered → no PnL."""
    date = "2024-06-12"
    arr = np.zeros(2, dtype=_NPZ_DTYPE)
    arr[0]["ev"] = _trade_ev(True)
    arr[0]["local_ts"] = _ns(date, 8, 30, 0)
    arr[0]["px"] = 5000.0
    arr[0]["qty"] = 1
    arr[1]["ev"] = _trade_ev(True)
    arr[1]["local_ts"] = _ns(date, 12, 0, 0)
    arr[1]["px"] = 5010.0
    arr[1]["qty"] = 1

    file_date_utc = datetime(2024, 6, 12, 13, 0, 0, tzinfo=timezone.utc)
    rec = measure_file(arr, "ES.v.0", "TEST", file_date_utc)

    assert rec["has_signal_coverage"] is True
    assert rec["has_target_coverage"] is False
    assert rec["net_ticks"] is None


# ---------------------------------------------------------------------------
# Unit: trade count fields
# ---------------------------------------------------------------------------

def test_trade_count_fields() -> None:
    """n_trades_signal_window and n_trades_target_window count correctly."""
    date = "2024-06-12"
    signal_start = _ns(date, 8, 30, 0)
    signal_end = _ns(date, 14, 30, 0)
    target_end = _ns(date, 15, 0, 0)

    rows = []
    # 3 trades in signal window
    for i in range(3):
        rows.append({"ev": _trade_ev(True), "local_ts": signal_start + i * 1_000_000_000, "px": 5000.0})
    # 2 trades in target window (strictly after signal_end)
    rows.append({"ev": _trade_ev(True), "local_ts": signal_end + 1, "px": 5010.0})
    rows.append({"ev": _trade_ev(True), "local_ts": signal_end + 2_000_000_000, "px": 5012.0})
    # 1 non-trade (should not be counted)
    rows.append({"ev": _ADD_BASE, "local_ts": signal_start + 500_000_000, "px": 5001.0})

    arr = np.zeros(len(rows), dtype=_NPZ_DTYPE)
    for i, r in enumerate(rows):
        arr[i]["ev"] = r["ev"]
        arr[i]["local_ts"] = r["local_ts"]
        arr[i]["px"] = r["px"]
        arr[i]["qty"] = 1

    file_date_utc = datetime(2024, 6, 12, 13, 0, 0, tzinfo=timezone.utc)
    rec = measure_file(arr, "ES.v.0", "TEST", file_date_utc)

    assert rec["n_trades_signal_window"] == 3
    assert rec["n_trades_target_window"] == 2


# ---------------------------------------------------------------------------
# Unit: aggregate statistics
# ---------------------------------------------------------------------------

def test_aggregate_hit_rate_and_t_stat() -> None:
    """_aggregate computes hit_rate and t_stat from a synthetic list of records.

    3 winning days (net=+4), 1 losing day (net=-2):
      hit_rate = 3/4 = 0.75
      mean_net = (4+4+4-2)/4 = 2.5
      std      = sqrt([(4-2.5)^2 + (4-2.5)^2 + (4-2.5)^2 + (-2-2.5)^2] / 3)
               = sqrt([2.25 + 2.25 + 2.25 + 20.25] / 3)
               = sqrt(27 / 3) = sqrt(9) = 3.0
      t_stat   = 2.5 / (3.0 / sqrt(4)) = 2.5 / 1.5 = 1.666...
    """
    records = [
        {
            "date": "2024-01-02", "symbol": "ES.v.0", "event_id": "A",
            "net_ticks": 4.0, "gross_ticks": 5.3, "signal_ret": 0.01, "target_ret": 0.005,
            "has_signal_coverage": True, "has_target_coverage": True,
        },
        {
            "date": "2024-01-03", "symbol": "ES.v.0", "event_id": "B",
            "net_ticks": 4.0, "gross_ticks": 5.3, "signal_ret": 0.01, "target_ret": 0.005,
            "has_signal_coverage": True, "has_target_coverage": True,
        },
        {
            "date": "2024-01-04", "symbol": "ES.v.0", "event_id": "C",
            "net_ticks": 4.0, "gross_ticks": 5.3, "signal_ret": 0.01, "target_ret": 0.005,
            "has_signal_coverage": True, "has_target_coverage": True,
        },
        {
            "date": "2024-01-05", "symbol": "ES.v.0", "event_id": "D",
            "net_ticks": -2.0, "gross_ticks": -0.7, "signal_ret": -0.005, "target_ret": -0.002,
            "has_signal_coverage": True, "has_target_coverage": True,
        },
    ]
    agg = _aggregate(records)

    assert agg["n_days"] == 4
    assert abs(agg["hit_rate"] - 0.75) < 1e-9
    assert abs(agg["mean_net_ticks"] - 2.5) < 1e-9
    expected_t = 2.5 / (3.0 / math.sqrt(4))  # = 5/3 ≈ 1.6667
    assert abs(agg["t_stat"] - expected_t) < 1e-9


def test_aggregate_excludes_uncovered_records() -> None:
    """Records where has_signal_coverage or has_target_coverage is False are excluded."""
    records = [
        {
            "date": "2024-01-02", "net_ticks": 10.0,
            "has_signal_coverage": True, "has_target_coverage": False,
        },
        {
            "date": "2024-01-03", "net_ticks": 5.0,
            "has_signal_coverage": True, "has_target_coverage": True,
        },
    ]
    agg = _aggregate(records)
    assert agg["n_days"] == 1
    assert abs(agg["mean_net_ticks"] - 5.0) < 1e-9


def test_aggregate_by_year_split() -> None:
    """by_year splits records by calendar year."""
    records = [
        {
            "date": "2023-06-01", "net_ticks": 2.0,
            "has_signal_coverage": True, "has_target_coverage": True,
        },
        {
            "date": "2024-06-01", "net_ticks": 4.0,
            "has_signal_coverage": True, "has_target_coverage": True,
        },
        {
            "date": "2024-06-02", "net_ticks": -1.0,
            "has_signal_coverage": True, "has_target_coverage": True,
        },
    ]
    agg = _aggregate(records)
    assert "2023" in agg["by_year"]
    assert "2024" in agg["by_year"]
    assert agg["by_year"]["2023"]["n_days"] == 1
    assert agg["by_year"]["2024"]["n_days"] == 2


def test_aggregate_empty_returns_none_fields() -> None:
    agg = _aggregate([])
    assert agg["n_days"] == 0
    assert agg["hit_rate"] is None
    assert agg["mean_net_ticks"] is None
    assert agg["t_stat"] is None


# ---------------------------------------------------------------------------
# Integration: inventory classifies dual-coverage correctly
# ---------------------------------------------------------------------------

def test_inventory_covers_both_windows(tmp_path: Path) -> None:
    """File spanning 08:30–15:00 CT is classified as covering both windows."""
    date = "2024-06-12"
    symbol = "ES.v.0"

    rows = [
        {"ev": _trade_ev(True), "local_ts": _ns(date, 8, 30, 0), "px": 5000.0},
        {"ev": _trade_ev(True), "local_ts": _ns(date, 14, 30, 0), "px": 5010.0},
        {"ev": _trade_ev(True), "local_ts": _ns(date, 15, 0, 0), "px": 5015.0},
    ]
    path = _make_npz(rows, tmp_path, f"{symbol}_FULL_mbo.npz")

    entries = [
        {
            "event_id": "FULL",
            "symbol": symbol,
            "npz_path": str(path),
            "event_count": 3,
            "sha256": "aa",
            "created_utc": f"{date}T19:00:00+00:00",
        }
    ]
    _make_manifest(entries, tmp_path)

    old = os.environ.get("HFT3_NPZ_ROOT")
    os.environ["HFT3_NPZ_ROOT"] = str(tmp_path)
    try:
        report = run_inventory(tmp_path)
    finally:
        if old is None:
            del os.environ["HFT3_NPZ_ROOT"]
        else:
            os.environ["HFT3_NPZ_ROOT"] = old

    assert report["files_covering_both"] == 1
    assert len(report["covering_entries"]) == 1
    assert report["covering_entries"][0]["event_id"] == "FULL"


def test_inventory_macro_only_file_not_covering(tmp_path: Path) -> None:
    """File around 08:30 CPI release but not covering 14:30–15:00 is non-covering."""
    date = "2024-06-12"
    symbol = "ES.v.0"

    rows = [
        {"ev": _trade_ev(True), "local_ts": _ns(date, 8, 28, 0), "px": 4990.0},
        {"ev": _trade_ev(True), "local_ts": _ns(date, 8, 45, 0), "px": 4995.0},
    ]
    path = _make_npz(rows, tmp_path, f"{symbol}_CPI_mbo.npz")

    entries = [
        {
            "event_id": "CPI",
            "symbol": symbol,
            "npz_path": str(path),
            "event_count": 2,
            "sha256": "bb",
            "created_utc": f"{date}T13:30:00+00:00",
        }
    ]
    _make_manifest(entries, tmp_path)

    old = os.environ.get("HFT3_NPZ_ROOT")
    os.environ["HFT3_NPZ_ROOT"] = str(tmp_path)
    try:
        report = run_inventory(tmp_path)
    finally:
        if old is None:
            del os.environ["HFT3_NPZ_ROOT"]
        else:
            os.environ["HFT3_NPZ_ROOT"] = old

    assert report["files_covering_both"] == 0
    assert len(report["non_covering_entries"]) == 1


def test_inventory_empty_manifest(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text("[]", encoding="utf-8")

    old = os.environ.get("HFT3_NPZ_ROOT")
    os.environ["HFT3_NPZ_ROOT"] = str(tmp_path)
    try:
        report = run_inventory(tmp_path)
    finally:
        if old is None:
            del os.environ["HFT3_NPZ_ROOT"]
        else:
            os.environ["HFT3_NPZ_ROOT"] = old

    assert report["files_total"] == 0
    assert report["files_covering_both"] == 0


def test_inventory_missing_npz_counted_as_missing(tmp_path: Path) -> None:
    entries = [
        {
            "event_id": "GHOST",
            "symbol": "ES.v.0",
            "npz_path": str(tmp_path / "nonexistent.npz"),
            "event_count": 0,
            "sha256": "",
            "created_utc": "2024-06-12T19:00:00+00:00",
        }
    ]
    _make_manifest(entries, tmp_path)

    old = os.environ.get("HFT3_NPZ_ROOT")
    os.environ["HFT3_NPZ_ROOT"] = str(tmp_path)
    try:
        report = run_inventory(tmp_path)
    finally:
        if old is None:
            del os.environ["HFT3_NPZ_ROOT"]
        else:
            os.environ["HFT3_NPZ_ROOT"] = old

    assert report["files_missing"] == 1
    assert report["files_covering_both"] == 0


# ---------------------------------------------------------------------------
# Integration: run_measure end-to-end
# ---------------------------------------------------------------------------

def test_measure_end_to_end(tmp_path: Path) -> None:
    """run_measure returns one record with correct net_ticks for a covering file.

    Setup:
      signal_start_px = 5000.0  (08:30 CT)
      signal_end_px   = 5020.0  (14:30 CT)  signal_ret > 0 → long
      target_end_px   = 5024.0  (14:45 CT)
      gross = (5024-5020)/0.25 = 16 ticks
      net   = 16 - 1.3 = 14.7 ticks
    """
    date = "2024-06-12"
    symbol = "ES.v.0"
    event_id = "TEST_MOM"

    rows = [
        {"ev": _trade_ev(True), "local_ts": _ns(date, 8, 30, 0), "px": 5000.0},
        {"ev": _trade_ev(True), "local_ts": _ns(date, 14, 30, 0), "px": 5020.0},
        {"ev": _trade_ev(True), "local_ts": _ns(date, 14, 45, 0), "px": 5024.0},
    ]
    path = _make_npz(rows, tmp_path, f"{symbol}_{event_id}_mbo.npz")
    entries = [
        {
            "event_id": event_id,
            "symbol": symbol,
            "npz_path": str(path),
            "event_count": 3,
            "sha256": "cc",
            "created_utc": f"{date}T19:00:00+00:00",
        }
    ]
    _make_manifest(entries, tmp_path)

    old = os.environ.get("HFT3_NPZ_ROOT")
    os.environ["HFT3_NPZ_ROOT"] = str(tmp_path)
    try:
        results = run_measure(tmp_path, cost_ticks=1.3)
    finally:
        if old is None:
            del os.environ["HFT3_NPZ_ROOT"]
        else:
            os.environ["HFT3_NPZ_ROOT"] = old

    assert len(results) == 1
    rec = results[0]
    assert rec["symbol"] == symbol
    assert rec["has_signal_coverage"] is True
    assert rec["has_target_coverage"] is True
    assert abs(rec["gross_ticks"] - 16.0) < 1e-9
    assert abs(rec["net_ticks"] - 14.7) < 1e-9


def test_measure_no_covering_files_returns_empty(tmp_path: Path) -> None:
    """When no files cover both windows, run_measure returns []."""
    (tmp_path / "manifest.json").write_text("[]", encoding="utf-8")

    old = os.environ.get("HFT3_NPZ_ROOT")
    os.environ["HFT3_NPZ_ROOT"] = str(tmp_path)
    try:
        results = run_measure(tmp_path)
    finally:
        if old is None:
            del os.environ["HFT3_NPZ_ROOT"]
        else:
            os.environ["HFT3_NPZ_ROOT"] = old

    assert results == []


# ---------------------------------------------------------------------------
# Unit: output path guard — must be under research_cards/last30_momentum/
# ---------------------------------------------------------------------------

def test_output_path_under_research_cards(tmp_path: Path) -> None:
    """Default output paths must be under research_cards/last30_momentum/."""
    inv_path = _default_out("inventory", tmp_path)
    meas_path = _default_out("measure", tmp_path)
    data_npz = tmp_path / "data" / "npz"

    # Must NOT be under data/npz/
    assert not str(inv_path).startswith(str(data_npz))
    assert not str(meas_path).startswith(str(data_npz))

    # Must be under research_cards/last30_momentum/
    assert "research_cards" in str(inv_path)
    assert "last30_momentum" in str(inv_path)
    assert "research_cards" in str(meas_path)
    assert "last30_momentum" in str(meas_path)


# ---------------------------------------------------------------------------
# Unit: DST-aware boundary — winter (CST) vs summer (CDT)
# ---------------------------------------------------------------------------

def test_momentum_bounds_cdt_summer() -> None:
    """In CDT (UTC-5), 08:30 CT = 13:30 UTC and 15:00 CT = 20:00 UTC."""
    # June 12 = CDT (UTC-5)
    file_date_utc = datetime(2024, 6, 12, tzinfo=timezone.utc)
    signal_start, signal_end, target_end, _ = _momentum_bounds_ns(file_date_utc)

    midnight_utc_ns = int(datetime(2024, 6, 12, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    expected_signal_start = midnight_utc_ns + (8 * 3600 + 30 * 60 + 5 * 3600) * 1_000_000_000
    expected_signal_end = midnight_utc_ns + (14 * 3600 + 30 * 60 + 5 * 3600) * 1_000_000_000
    expected_target_end = midnight_utc_ns + (15 * 3600 + 5 * 3600) * 1_000_000_000

    assert signal_start == expected_signal_start
    assert signal_end == expected_signal_end
    assert target_end == expected_target_end


def test_momentum_bounds_cst_winter() -> None:
    """In CST (UTC-6), 08:30 CT = 14:30 UTC and 15:00 CT = 21:00 UTC."""
    # January 15 = CST (UTC-6)
    file_date_utc = datetime(2024, 1, 15, tzinfo=timezone.utc)
    signal_start, signal_end, target_end, _ = _momentum_bounds_ns(file_date_utc)

    midnight_utc_ns = int(datetime(2024, 1, 15, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    expected_signal_start = midnight_utc_ns + (8 * 3600 + 30 * 60 + 6 * 3600) * 1_000_000_000
    expected_signal_end = midnight_utc_ns + (14 * 3600 + 30 * 60 + 6 * 3600) * 1_000_000_000
    expected_target_end = midnight_utc_ns + (15 * 3600 + 6 * 3600) * 1_000_000_000

    assert signal_start == expected_signal_start
    assert signal_end == expected_signal_end
    assert target_end == expected_target_end
