"""Tests for the ES options fixing-window study harness.

Conventions:
- sys.path setup mirrors test_parity_backtest.py / test_parity_engine.py
- Synthetic NPZ files are built with a minimal structured array that satisfies
  the field requirements of npz_feed.py: {ev, local_ts, px, qty, order_id}
- HFT3_NPZ_ROOT is pointed to tmp_path so tests never touch data/npz/
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
# sys.path bootstrap — same pattern as test_parity_*.py
# ---------------------------------------------------------------------------
_REPO = Path(__file__).resolve().parents[2]
_PACKAGES = _REPO / "packages"
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_PACKAGES))

# Import the module under test (must come after path setup)
from options_lane.studies.fixing_window_study import (
    _compute_imbalance,
    _compute_vwap,
    _ct_hms_to_utc_ns,
    _is_buy_aggressor,
    _is_sell_aggressor,
    _is_trade,
    _last_trade_price,
    _window_bounds_ns,
    load_expiry_oi,
    measure_file,
    run_inventory,
    run_measure,
)

# ---------------------------------------------------------------------------
# Synthetic NPZ helpers
# ---------------------------------------------------------------------------

# Field layout that matches npz_feed.py required fields + order_id
_NPZ_DTYPE = np.dtype(
    [
        ("ev", np.int64),
        ("local_ts", np.int64),
        ("px", np.float64),
        ("qty", np.int64),
        ("order_id", np.int64),
    ]
)

# hftbacktest bit-flag values (from scripts/verify_cpp_parity.py, authoritative source)
# TRADE_EVENT=2, BUY_EVENT=1<<29 (resting bid=sell aggressor), SELL_EVENT=1<<28 (resting ask=buy aggressor)
_TRADE_BASE = 2
_FILL_BASE = 13
_BUY_FLAG = 1 << 29   # resting bid = sell aggressor
_SELL_FLAG = 1 << 28  # resting ask = buy aggressor
_ADD_BASE = 10        # ADD_ORDER_EVENT — non-trade; used to test _is_trade returns False


def _trade_ev(buy_aggressor: bool) -> int:
    """Compose a trade event value with the appropriate aggressor flag.

    buy_aggressor=True  → buyer hit the ask (resting ask side) → SELL_FLAG set
    buy_aggressor=False → seller hit the bid (resting bid side) → BUY_FLAG set
    """
    if buy_aggressor:
        return _TRADE_BASE | _SELL_FLAG  # resting ask → buyer lifted it
    else:
        return _TRADE_BASE | _BUY_FLAG   # resting bid → seller hit it


def _make_npz(
    rows: list[dict[str, Any]],
    tmp_path: Path,
    filename: str = "ES.v.0_TEST_EVENT_mbo.npz",
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


def _ns_on_date(date_str: str, h: int, m: int, s: int) -> int:
    """Return UTC ns for America/Chicago wall-clock time on the given date (YYYY-MM-DD)."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("America/Chicago")
    y, mo, d = map(int, date_str.split("-"))
    from datetime import datetime as _dt
    naive = _dt(y, mo, d, h, m, s)
    aware = naive.replace(tzinfo=tz)
    return int(aware.timestamp() * 1_000_000_000)


# ---------------------------------------------------------------------------
# Unit tests: event-flag helpers
# ---------------------------------------------------------------------------

def test_is_trade_trade_event() -> None:
    ev = _trade_ev(True)  # TRADE_BASE | SELL_FLAG
    assert _is_trade(ev)


def test_is_trade_non_trade_event() -> None:
    assert not _is_trade(_ADD_BASE)


def test_buy_aggressor_flag() -> None:
    ev = _trade_ev(True)   # buyer hit the ask: SELL_FLAG set
    assert _is_buy_aggressor(ev)
    assert not _is_sell_aggressor(ev)


def test_sell_aggressor_flag() -> None:
    ev = _trade_ev(False)  # seller hit the bid: BUY_FLAG set
    assert _is_sell_aggressor(ev)
    assert not _is_buy_aggressor(ev)


# ---------------------------------------------------------------------------
# Unit tests: VWAP math
# ---------------------------------------------------------------------------

def test_vwap_simple(tmp_path: Path) -> None:
    # Two trades: 100@5000, 200@5010 → VWAP = (100*5000 + 200*5010) / 300 = 5006.666...
    date = "2024-03-15"
    fix_start = _ns_on_date(date, 14, 59, 30)
    rows = [
        {"ev": _trade_ev(True), "local_ts": fix_start + 0, "px": 5000.0, "qty": 100},
        {"ev": _trade_ev(True), "local_ts": fix_start + 1_000_000_000, "px": 5010.0, "qty": 200},
    ]
    arr = np.zeros(len(rows), dtype=_NPZ_DTYPE)
    for i, r in enumerate(rows):
        arr[i]["ev"] = r["ev"]
        arr[i]["local_ts"] = r["local_ts"]
        arr[i]["px"] = r["px"]
        arr[i]["qty"] = r["qty"]

    fix_end = _ns_on_date(date, 15, 0, 0)
    vwap = _compute_vwap(arr, fix_start, fix_end)
    expected = (100 * 5000 + 200 * 5010) / 300
    assert abs(vwap - expected) < 1e-6


def test_vwap_no_trades_is_nan(tmp_path: Path) -> None:
    # Non-trade rows only → VWAP is nan
    rows = [
        {"ev": _ADD_BASE, "local_ts": 1_000_000, "px": 5000.0, "qty": 10},
    ]
    arr = np.zeros(len(rows), dtype=_NPZ_DTYPE)
    arr[0]["ev"] = _ADD_BASE
    arr[0]["local_ts"] = 1_000_000
    arr[0]["px"] = 5000.0
    arr[0]["qty"] = 10
    vwap = _compute_vwap(arr, 0, 10_000_000_000)
    assert math.isnan(vwap)


def test_vwap_outside_window_excluded(tmp_path: Path) -> None:
    date = "2024-03-15"
    fix_start = _ns_on_date(date, 14, 59, 30)
    fix_end = _ns_on_date(date, 15, 0, 0)
    # One trade inside, one outside
    rows_data = np.zeros(2, dtype=_NPZ_DTYPE)
    rows_data[0]["ev"] = _trade_ev(True)
    rows_data[0]["local_ts"] = fix_start + 1  # inside
    rows_data[0]["px"] = 5100.0
    rows_data[0]["qty"] = 1
    rows_data[1]["ev"] = _trade_ev(True)
    rows_data[1]["local_ts"] = fix_end + 1    # outside (after)
    rows_data[1]["px"] = 9999.0
    rows_data[1]["qty"] = 1

    vwap = _compute_vwap(rows_data, fix_start, fix_end)
    assert abs(vwap - 5100.0) < 1e-9


# ---------------------------------------------------------------------------
# Unit tests: imbalance sign correctness
# ---------------------------------------------------------------------------

def test_imbalance_all_buy() -> None:
    """All buy aggressors → imbalance = +1.0."""
    date = "2024-06-12"
    scan_start = _ns_on_date(date, 14, 55, 0)
    scan_end = _ns_on_date(date, 15, 5, 0)
    arr = np.zeros(3, dtype=_NPZ_DTYPE)
    for i in range(3):
        arr[i]["ev"] = _trade_ev(True)   # buy aggressor
        arr[i]["local_ts"] = scan_start + i * 1_000_000_000
        arr[i]["px"] = 5000.0
        arr[i]["qty"] = 10

    imb, count, vol = _compute_imbalance(arr, scan_start, scan_end)
    assert abs(imb - 1.0) < 1e-9
    assert count == 3
    assert vol == 30.0


def test_imbalance_all_sell() -> None:
    """All sell aggressors → imbalance = -1.0."""
    date = "2024-06-12"
    scan_start = _ns_on_date(date, 14, 55, 0)
    scan_end = _ns_on_date(date, 15, 5, 0)
    arr = np.zeros(3, dtype=_NPZ_DTYPE)
    for i in range(3):
        arr[i]["ev"] = _trade_ev(False)  # sell aggressor
        arr[i]["local_ts"] = scan_start + i * 1_000_000_000
        arr[i]["px"] = 5000.0
        arr[i]["qty"] = 10

    imb, count, vol = _compute_imbalance(arr, scan_start, scan_end)
    assert abs(imb - (-1.0)) < 1e-9


def test_imbalance_balanced() -> None:
    """Equal buy and sell → imbalance ≈ 0."""
    date = "2024-06-12"
    scan_start = _ns_on_date(date, 14, 55, 0)
    scan_end = _ns_on_date(date, 15, 5, 0)
    arr = np.zeros(4, dtype=_NPZ_DTYPE)
    for i in range(4):
        buy = i % 2 == 0
        arr[i]["ev"] = _trade_ev(buy)
        arr[i]["local_ts"] = scan_start + i * 1_000_000_000
        arr[i]["px"] = 5000.0
        arr[i]["qty"] = 10

    imb, count, vol = _compute_imbalance(arr, scan_start, scan_end)
    assert abs(imb) < 1e-9


# ---------------------------------------------------------------------------
# Unit tests: markout nan-truncation
# ---------------------------------------------------------------------------

def test_markout_nan_when_no_post_window_data(tmp_path: Path) -> None:
    """When no trades exist after fix_end, all markouts are nan."""
    date = "2024-06-12"
    fix_start = _ns_on_date(date, 14, 59, 30)
    fix_end = _ns_on_date(date, 15, 0, 0)
    scan_start = _ns_on_date(date, 14, 55, 0)

    # Only trades inside the 30-second window, nothing after
    arr = np.zeros(2, dtype=_NPZ_DTYPE)
    arr[0]["ev"] = _trade_ev(True)
    arr[0]["local_ts"] = fix_start + 5_000_000_000  # inside window
    arr[0]["px"] = 5050.0
    arr[0]["qty"] = 1
    arr[1]["ev"] = _trade_ev(True)
    arr[1]["local_ts"] = fix_start + 10_000_000_000  # also inside window
    arr[1]["px"] = 5060.0
    arr[1]["qty"] = 1

    file_date_utc = datetime(2024, 6, 12, 20, 0, 0, tzinfo=timezone.utc)
    rec = measure_file(arr, "ES.v.0", "TEST_EVENT", file_date_utc)

    # VWAP should be computed from the trades inside the fix window
    assert rec["vwap_30s"] is not None

    # All markouts should be None (no data after fix_end)
    assert rec["markout_30s"] is None
    assert rec["markout_2m"] is None
    assert rec["markout_5m"] is None


def test_markout_computed_when_post_window_trade_exists() -> None:
    """Markout_30s is non-None when a trade exists after fix_end."""
    date = "2024-06-12"
    fix_start = _ns_on_date(date, 14, 59, 30)
    fix_end = _ns_on_date(date, 15, 0, 0)

    arr = np.zeros(3, dtype=_NPZ_DTYPE)
    # Inside fix window
    arr[0]["ev"] = _trade_ev(True)
    arr[0]["local_ts"] = fix_start + 1_000_000_000
    arr[0]["px"] = 5000.0
    arr[0]["qty"] = 1
    # Inside fix window
    arr[1]["ev"] = _trade_ev(True)
    arr[1]["local_ts"] = fix_start + 15_000_000_000  # 15s into window
    arr[1]["px"] = 5002.0
    arr[1]["qty"] = 1
    # Post-window +15 seconds (within 30s markout)
    arr[2]["ev"] = _trade_ev(True)
    arr[2]["local_ts"] = fix_end + 15_000_000_000  # 15s after fix_end
    arr[2]["px"] = 5005.0
    arr[2]["qty"] = 1

    file_date_utc = datetime(2024, 6, 12, 20, 0, 0, tzinfo=timezone.utc)
    rec = measure_file(arr, "ES.v.0", "TEST_EVENT", file_date_utc)

    expected_vwap = (1 * 5000 + 1 * 5002) / 2
    assert rec["vwap_30s"] is not None
    assert abs(rec["vwap_30s"] - expected_vwap) < 1e-6

    # markout_30s = last trade price in [fix_end, fix_end+30s] - vwap
    assert rec["markout_30s"] is not None
    assert abs(rec["markout_30s"] - (5005.0 - expected_vwap)) < 1e-6


# ---------------------------------------------------------------------------
# Integration tests: inventory classifies covering vs non-covering files
# ---------------------------------------------------------------------------

def _make_manifest(entries: list[dict[str, Any]], root: Path) -> None:
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(entries), encoding="utf-8")


def test_inventory_classifies_covering_vs_non_covering(tmp_path: Path) -> None:
    """Inventory correctly separates files that overlap 14:55-15:05 CT from those that don't."""
    date = "2024-06-12"
    symbol = "ES.v.0"

    # --- Covering file: has events spanning 14:55:00 → 15:05:00 CT ---
    cover_start_ns = _ns_on_date(date, 14, 55, 0)
    cover_end_ns = _ns_on_date(date, 15, 5, 0)
    cover_rows = [
        {"ev": _trade_ev(True), "local_ts": cover_start_ns, "px": 5000.0, "qty": 1},
        {"ev": _trade_ev(False), "local_ts": cover_end_ns, "px": 5001.0, "qty": 1},
    ]
    cover_path = _make_npz(cover_rows, tmp_path, f"{symbol}_CPI_COVER_mbo.npz")

    # --- Non-covering file: events only around 08:30 CT (macro event window) ---
    macro_ns = _ns_on_date(date, 8, 30, 0)
    non_cover_rows = [
        {"ev": _trade_ev(True), "local_ts": macro_ns, "px": 4990.0, "qty": 1},
        {"ev": _trade_ev(True), "local_ts": macro_ns + 10_000_000_000, "px": 4995.0, "qty": 1},
    ]
    non_cover_path = _make_npz(non_cover_rows, tmp_path, f"{symbol}_CPI_MACRO_mbo.npz")

    # Build manifest pointing to both files
    entries = [
        {
            "event_id": "CPI_COVER",
            "symbol": symbol,
            "npz_path": str(cover_path),
            "event_count": 2,
            "sha256": "aa",
            "created_utc": f"{date}T20:00:00+00:00",
        },
        {
            "event_id": "CPI_MACRO",
            "symbol": symbol,
            "npz_path": str(non_cover_path),
            "event_count": 2,
            "sha256": "bb",
            "created_utc": f"{date}T13:30:00+00:00",
        },
    ]
    _make_manifest(entries, tmp_path)

    # Point the repo_root to tmp_path so manifest_path resolves there
    # We need to fake a repo_root such that npz_root(repo_root) = tmp_path
    # The easiest approach: set HFT3_NPZ_ROOT
    old_env = os.environ.get("HFT3_NPZ_ROOT")
    os.environ["HFT3_NPZ_ROOT"] = str(tmp_path)
    try:
        # Pass a dummy repo_root — with HFT3_NPZ_ROOT set, npz_root() ignores it
        report = run_inventory(tmp_path)
    finally:
        if old_env is None:
            del os.environ["HFT3_NPZ_ROOT"]
        else:
            os.environ["HFT3_NPZ_ROOT"] = old_env

    assert report["files_total"] == 2
    assert report["files_covering"] == 1
    covering_event_ids = {e["event_id"] for e in report["covering_entries"]}
    assert "CPI_COVER" in covering_event_ids
    assert "CPI_MACRO" not in covering_event_ids


def test_inventory_empty_manifest(tmp_path: Path) -> None:
    """Inventory on empty manifest returns zero counts."""
    (tmp_path / "manifest.json").write_text("[]", encoding="utf-8")
    old_env = os.environ.get("HFT3_NPZ_ROOT")
    os.environ["HFT3_NPZ_ROOT"] = str(tmp_path)
    try:
        report = run_inventory(tmp_path)
    finally:
        if old_env is None:
            del os.environ["HFT3_NPZ_ROOT"]
        else:
            os.environ["HFT3_NPZ_ROOT"] = old_env

    assert report["files_total"] == 0
    assert report["files_covering"] == 0
    assert report["files_missing"] == 0


def test_inventory_missing_npz_counted_as_missing(tmp_path: Path) -> None:
    """Manifest entries whose NPZ file does not exist are counted as missing."""
    entries = [
        {
            "event_id": "GHOST",
            "symbol": "ES.v.0",
            "npz_path": str(tmp_path / "nonexistent.npz"),
            "event_count": 0,
            "sha256": "",
            "created_utc": "2024-06-12T20:00:00+00:00",
        },
    ]
    _make_manifest(entries, tmp_path)
    old_env = os.environ.get("HFT3_NPZ_ROOT")
    os.environ["HFT3_NPZ_ROOT"] = str(tmp_path)
    try:
        report = run_inventory(tmp_path)
    finally:
        if old_env is None:
            del os.environ["HFT3_NPZ_ROOT"]
        else:
            os.environ["HFT3_NPZ_ROOT"] = old_env

    assert report["files_missing"] == 1
    assert report["files_covering"] == 0


# ---------------------------------------------------------------------------
# Integration: measure subcommand end-to-end
# ---------------------------------------------------------------------------

def test_measure_produces_oi_none_field(tmp_path: Path) -> None:
    """measure_file always sets oi=None (WS-0.4a stub)."""
    date = "2024-06-12"
    fix_start = _ns_on_date(date, 14, 59, 30)
    arr = np.zeros(1, dtype=_NPZ_DTYPE)
    arr[0]["ev"] = _trade_ev(True)
    arr[0]["local_ts"] = fix_start + 5_000_000_000
    arr[0]["px"] = 5000.0
    arr[0]["qty"] = 1

    file_date_utc = datetime(2024, 6, 12, 20, 0, 0, tzinfo=timezone.utc)
    rec = measure_file(arr, "ES.v.0", "TEST", file_date_utc)
    assert "oi" in rec
    assert rec["oi"] is None


def test_measure_end_to_end_covering_file(tmp_path: Path) -> None:
    """run_measure returns a result record for a covering file."""
    date = "2024-06-12"
    symbol = "ES.v.0"
    event_id = "TEST_FIX"

    scan_start = _ns_on_date(date, 14, 55, 0)
    fix_start = _ns_on_date(date, 14, 59, 30)
    fix_end = _ns_on_date(date, 15, 0, 0)

    rows_data = np.zeros(4, dtype=_NPZ_DTYPE)
    # Pre-window trade
    rows_data[0]["ev"] = _trade_ev(True)
    rows_data[0]["local_ts"] = scan_start + 1_000_000_000
    rows_data[0]["px"] = 4995.0
    rows_data[0]["qty"] = 1
    # Fix-window trades
    rows_data[1]["ev"] = _trade_ev(True)
    rows_data[1]["local_ts"] = fix_start + 5_000_000_000
    rows_data[1]["px"] = 5000.0
    rows_data[1]["qty"] = 2
    rows_data[2]["ev"] = _trade_ev(False)   # sell aggressor
    rows_data[2]["local_ts"] = fix_start + 10_000_000_000
    rows_data[2]["px"] = 5002.0
    rows_data[2]["qty"] = 1
    # Post-window trade (within 5-min markout window)
    rows_data[3]["ev"] = _trade_ev(True)
    rows_data[3]["local_ts"] = fix_end + 60_000_000_000   # +1min
    rows_data[3]["px"] = 5004.0
    rows_data[3]["qty"] = 1

    npz_path = tmp_path / f"{symbol}_{event_id}_mbo.npz"
    np.savez(str(npz_path), data=rows_data)

    entries = [
        {
            "event_id": event_id,
            "symbol": symbol,
            "npz_path": str(npz_path),
            "event_count": 4,
            "sha256": "cc",
            "created_utc": f"{date}T20:00:00+00:00",
        }
    ]
    _make_manifest(entries, tmp_path)

    old_env = os.environ.get("HFT3_NPZ_ROOT")
    os.environ["HFT3_NPZ_ROOT"] = str(tmp_path)
    try:
        results = run_measure(tmp_path)
    finally:
        if old_env is None:
            del os.environ["HFT3_NPZ_ROOT"]
        else:
            os.environ["HFT3_NPZ_ROOT"] = old_env

    assert len(results) == 1
    rec = results[0]
    assert rec["symbol"] == symbol
    assert rec["event_id"] == event_id

    # VWAP of fix-window trades: 2@5000 + 1@5002 → (2*5000 + 1*5002) / 3 = 5000.666...
    expected_vwap = (2 * 5000 + 1 * 5002) / 3
    assert rec["vwap_30s"] is not None
    assert abs(rec["vwap_30s"] - expected_vwap) < 1e-6

    # Imbalance: scan window has buy(1)+sell(0)=1 pre + buy(2)@5000 + sell(1)@5002 + buy(1) post
    # signed = +1 + 2 - 1 + 1 = 3, total = 1+2+1+1 = 5 → imbalance = 0.6
    assert rec["imbalance_signed"] is not None

    # markout_2m: post-window trade at fix_end+60s is within 2-min window
    assert rec["markout_2m"] is not None
    assert abs(rec["markout_2m"] - (5004.0 - expected_vwap)) < 1e-6

    # OI stub
    assert rec["oi"] is None


# ---------------------------------------------------------------------------
# Unit: OI stub always returns None
# ---------------------------------------------------------------------------

def test_load_expiry_oi_returns_none() -> None:
    """OI stub returns None for any date (WS-0.4a pending)."""
    d = datetime(2024, 6, 12, 15, 0, tzinfo=timezone.utc)
    assert load_expiry_oi(d) is None


# ---------------------------------------------------------------------------
# Unit: DST-aware time conversion
# ---------------------------------------------------------------------------

def test_ct_to_utc_ns_cst_offset() -> None:
    """In winter (CST = UTC-6), 15:00 CT should be 21:00 UTC."""
    # January 15 — CST (UTC-6)
    date_utc = datetime(2024, 1, 15, tzinfo=timezone.utc)
    ns_15_00_ct = _ct_hms_to_utc_ns(date_utc, 15, 0, 0)
    # 15:00 CST = 21:00 UTC = 21*3600 seconds since midnight UTC
    midnight_utc_ns = int(datetime(2024, 1, 15, tzinfo=timezone.utc).timestamp() * 1e9)
    expected_ns = midnight_utc_ns + 21 * 3600 * 1_000_000_000
    assert ns_15_00_ct == expected_ns


def test_ct_to_utc_ns_cdt_offset() -> None:
    """In summer (CDT = UTC-5), 15:00 CT should be 20:00 UTC."""
    # June 12 — CDT (UTC-5)
    date_utc = datetime(2024, 6, 12, tzinfo=timezone.utc)
    ns_15_00_ct = _ct_hms_to_utc_ns(date_utc, 15, 0, 0)
    midnight_utc_ns = int(datetime(2024, 6, 12, tzinfo=timezone.utc).timestamp() * 1e9)
    expected_ns = midnight_utc_ns + 20 * 3600 * 1_000_000_000
    assert ns_15_00_ct == expected_ns


# ---------------------------------------------------------------------------
# Unit: output path never under data/npz
# ---------------------------------------------------------------------------

def test_output_not_under_data_npz(tmp_path: Path) -> None:
    """The default output path for inventory/measure must not be under data/npz/."""
    from options_lane.studies.fixing_window_study import _default_out
    inv_path = _default_out("inventory", tmp_path)
    meas_path = _default_out("measure", tmp_path)
    data_npz = tmp_path / "data" / "npz"
    # Ensure neither path is inside data/npz/
    assert not str(inv_path).startswith(str(data_npz))
    assert not str(meas_path).startswith(str(data_npz))
    # Both should be under research_cards/
    assert "research_cards" in str(inv_path)
    assert "research_cards" in str(meas_path)
