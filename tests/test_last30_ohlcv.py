"""Tests for OHLCV path of last-30-min momentum study (WS-1.3 measure-ohlcv).

New test classes only — existing test_last30_momentum_study.py tests are untouched.

Conventions:
- Pure helpers tested with synthetic numpy arrays (no file I/O).
- Integration tests skip cleanly when the real purchased file is absent.
- DST transition day included in day-splitting tests.
- All golden values derived by hand and documented inline.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# sys.path bootstrap
# ---------------------------------------------------------------------------
_REPO = Path(__file__).resolve().parents[2]
_PACKAGES = _REPO / "packages"
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_PACKAGES))

from options_lane.studies.last30_momentum_study import (
    _aggregate,
    _split_bars_by_day,
    _find_bar_close,
    measure_day_ohlcv,
    run_measure_ohlcv,
    _SIGNAL_OPEN_BAR_H,
    _SIGNAL_OPEN_BAR_M,
    _SIGNAL_END_BAR_H,
    _SIGNAL_END_BAR_M,
    _TARGET_END_BAR_H,
    _TARGET_END_BAR_M,
)

# ---------------------------------------------------------------------------
# Integration skip sentinel
# ---------------------------------------------------------------------------
_OHLCV_PATH = Path(
    r"C:\hft3-lake\options\ohlcv\ES_v0_ohlcv1m_20210101_20260612.dbn.zst"
)
_OHLCV_ABSENT = not _OHLCV_PATH.exists()

# ---------------------------------------------------------------------------
# Helpers for building synthetic bar arrays
# ---------------------------------------------------------------------------

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

_TZ_CT = ZoneInfo("America/Chicago")


def _ct_ns(date_str: str, h: int, m: int) -> int:
    """Return UTC ns for a CT wall-clock hour:minute on date_str."""
    y, mo, d = map(int, date_str.split("-"))
    naive = datetime(y, mo, d, h, m, 0)
    aware = naive.replace(tzinfo=_TZ_CT)
    return int(aware.timestamp() * 1_000_000_000)


def _make_bars(
    specs: list[tuple[str, int, int, float]],
) -> tuple[np.ndarray, np.ndarray]:
    """Build (ts_ns, close) arrays from (date_str, h, m, close_price) tuples."""
    ts_list = [_ct_ns(d, h, m) for d, h, m, _ in specs]
    cl_list = [c for _, _, _, c in specs]
    return np.array(ts_list, dtype=np.int64), np.array(cl_list, dtype=np.float64)


# ===========================================================================
# Class 1: _split_bars_by_day
# ===========================================================================

class TestSplitBarsByDay:
    """Unit tests for _split_bars_by_day day-grouping logic."""

    def test_single_day_all_bars_grouped(self) -> None:
        """All bars on the same CT date land in one group."""
        date = "2024-06-12"
        specs = [
            (date, 8, 29, 4800.0),
            (date, 9, 0, 4810.0),
            (date, 14, 29, 4820.0),
            (date, 14, 59, 4830.0),
        ]
        ts_ns, close = _make_bars(specs)
        result = _split_bars_by_day(ts_ns)
        assert date in result
        lo, hi = result[date]
        assert lo == 0
        assert hi == 4

    def test_two_consecutive_trading_days(self) -> None:
        """Bars for two adjacent days produce two separate keys."""
        specs = [
            ("2024-06-12", 8, 29, 4800.0),
            ("2024-06-12", 14, 29, 4820.0),
            ("2024-06-13", 8, 29, 4810.0),
            ("2024-06-13", 14, 29, 4825.0),
        ]
        ts_ns, close = _make_bars(specs)
        result = _split_bars_by_day(ts_ns)
        assert "2024-06-12" in result
        assert "2024-06-13" in result
        lo12, hi12 = result["2024-06-12"]
        lo13, hi13 = result["2024-06-13"]
        assert hi12 == lo13
        assert hi13 == 4

    def test_date_range_filter_start(self) -> None:
        """start_date filter excludes days before it."""
        specs = [
            ("2024-01-02", 8, 29, 4700.0),
            ("2024-01-03", 8, 29, 4710.0),
            ("2024-01-04", 8, 29, 4720.0),
        ]
        ts_ns, close = _make_bars(specs)
        result = _split_bars_by_day(ts_ns, start_date_str="2024-01-03")
        assert "2024-01-02" not in result
        assert "2024-01-03" in result
        assert "2024-01-04" in result

    def test_date_range_filter_end(self) -> None:
        """end_date filter excludes days after it."""
        specs = [
            ("2024-01-02", 8, 29, 4700.0),
            ("2024-01-03", 8, 29, 4710.0),
            ("2024-01-04", 8, 29, 4720.0),
        ]
        ts_ns, close = _make_bars(specs)
        result = _split_bars_by_day(ts_ns, end_date_str="2024-01-03")
        assert "2024-01-02" in result
        assert "2024-01-03" in result
        assert "2024-01-04" not in result

    def test_dst_transition_day_grouped_correctly(self) -> None:
        """Bars on and around DST spring-forward (2024-03-10) group by CT date.

        2024-03-10: clocks spring forward 02:00 CST -> 03:00 CDT.
        Bars before 02:00 are CST (UTC-6); bars from 03:00 are CDT (UTC-5).
        All bars on this calendar day should land in '2024-03-10'.
        """
        # Bars on the DST transition day (early morning and market hours)
        specs = [
            ("2024-03-10", 8, 29, 5100.0),  # CDT after spring forward
            ("2024-03-10", 14, 29, 5110.0),
            ("2024-03-10", 14, 59, 5120.0),
            ("2024-03-11", 8, 29, 5130.0),
        ]
        ts_ns, close = _make_bars(specs)
        result = _split_bars_by_day(ts_ns)
        assert "2024-03-10" in result
        assert "2024-03-11" in result
        lo10, hi10 = result["2024-03-10"]
        assert hi10 - lo10 == 3

    def test_empty_array_returns_empty_dict(self) -> None:
        ts_ns = np.empty(0, dtype=np.int64)
        result = _split_bars_by_day(ts_ns)
        assert result == {}


# ===========================================================================
# Class 2: _find_bar_close
# ===========================================================================

class TestFindBarClose:
    """Unit tests for boundary bar lookup by CT hour/minute."""

    def test_finds_exact_bar(self) -> None:
        """Bar at exact target h:m is found and its close returned."""
        date = "2024-06-12"
        specs = [
            (date, 8, 28, 4799.0),
            (date, 8, 29, 4800.0),   # target
            (date, 8, 30, 4801.0),
        ]
        ts_ns, close = _make_bars(specs)
        px = _find_bar_close(ts_ns, close, 0, 3, 8, 29, date)
        assert px == 4800.0

    def test_returns_nan_when_bar_absent(self) -> None:
        """When the target minute is missing (holiday gap), nan is returned."""
        date = "2024-06-12"
        specs = [
            (date, 8, 28, 4799.0),
            (date, 8, 30, 4801.0),  # 08:29 is absent
        ]
        ts_ns, close = _make_bars(specs)
        px = _find_bar_close(ts_ns, close, 0, 2, 8, 29, date)
        assert math.isnan(px)

    def test_respects_slice_bounds(self) -> None:
        """lo_idx:hi_idx slice prevents finding bars from other days."""
        date1 = "2024-06-12"
        date2 = "2024-06-13"
        specs = [
            (date1, 8, 29, 1111.0),   # idx 0
            (date2, 8, 29, 2222.0),   # idx 1
        ]
        ts_ns, close = _make_bars(specs)
        # Only search date2's slice (lo=1, hi=2)
        px = _find_bar_close(ts_ns, close, 1, 2, 8, 29, date2)
        assert px == 2222.0
        # date1's slice (lo=0, hi=1) should NOT find date2's bar
        px2 = _find_bar_close(ts_ns, close, 0, 1, 8, 29, date2)
        assert math.isnan(px2)

    def test_dst_transition_bar_found_correctly(self) -> None:
        """On DST spring-forward day (2024-03-10), 08:29 CDT bar found correctly.

        After spring forward, 08:29 CT is CDT (UTC-5), so UTC = 13:29.
        The bar must be found by its CT local time, not by UTC hour.
        """
        date = "2024-03-10"
        specs = [(date, 8, 29, 5100.0)]
        ts_ns, close = _make_bars(specs)
        px = _find_bar_close(ts_ns, close, 0, 1, 8, 29, date)
        assert px == 5100.0


# ===========================================================================
# Class 3: measure_day_ohlcv — arithmetic goldens
# ===========================================================================

class TestMeasureDayOhlcv:
    """Unit tests for per-day momentum computation on bar arrays."""

    def _day_bars(
        self,
        date: str,
        open_close: float,
        signal_end_close: float,
        target_end_close: float,
    ) -> tuple[np.ndarray, np.ndarray, int, int]:
        """Build minimal 3-bar array for one day and return (ts_ns, close, lo, hi)."""
        specs = [
            (date, _SIGNAL_OPEN_BAR_H, _SIGNAL_OPEN_BAR_M, open_close),
            (date, _SIGNAL_END_BAR_H, _SIGNAL_END_BAR_M, signal_end_close),
            (date, _TARGET_END_BAR_H, _TARGET_END_BAR_M, target_end_close),
        ]
        ts_ns, close = _make_bars(specs)
        return ts_ns, close, 0, 3

    def test_positive_signal_golden(self) -> None:
        """Long position: positive signal return -> long -> profit when target rises.

        signal_open_px = 5000.0  (08:29 bar close = price at 08:30)
        signal_end_px  = 5050.0  (14:29 bar close = price at 14:30)
        target_end_px  = 5075.0  (14:59 bar close = price at 15:00)

        signal_ret = log(5050/5000) = log(1.01) > 0  -> direction = +1
        gross_ticks = +1 * (5075 - 5050) / 0.25 = 25/0.25 = 100 ticks
        net_ticks   = 100 - 1.3 = 98.7 ticks
        """
        date = "2024-06-12"
        ts_ns, close, lo, hi = self._day_bars(date, 5000.0, 5050.0, 5075.0)
        rec = measure_day_ohlcv(ts_ns, close, date, lo, hi, cost_ticks=1.3)

        assert rec["has_signal_coverage"] is True
        assert rec["has_target_coverage"] is True
        assert abs(rec["signal_ret"] - math.log(5050.0 / 5000.0)) < 1e-9
        assert abs(rec["gross_ticks"] - 100.0) < 1e-9
        assert abs(rec["net_ticks"] - 98.7) < 1e-9

    def test_negative_signal_golden(self) -> None:
        """Short position: negative signal -> short -> profit when target falls.

        signal_open_px = 5100.0
        signal_end_px  = 5000.0  -> signal_ret < 0  -> direction = -1
        target_end_px  = 4980.0  -> target falls

        gross_ticks = -1 * (4980 - 5000) / 0.25 = -1 * (-80) = +80
        net_ticks   = 80 - 1.3 = 78.7
        """
        date = "2024-06-12"
        ts_ns, close, lo, hi = self._day_bars(date, 5100.0, 5000.0, 4980.0)
        rec = measure_day_ohlcv(ts_ns, close, date, lo, hi, cost_ticks=1.3)

        assert rec["signal_ret"] < 0
        assert abs(rec["gross_ticks"] - 80.0) < 1e-9
        assert abs(rec["net_ticks"] - 78.7) < 1e-9

    def test_zero_signal_no_cost(self) -> None:
        """Flat signal (ret==0) -> no position -> net_ticks must be 0.0 (no cost)."""
        date = "2024-06-12"
        ts_ns, close, lo, hi = self._day_bars(date, 5000.0, 5000.0, 5010.0)
        rec = measure_day_ohlcv(ts_ns, close, date, lo, hi, cost_ticks=1.3)

        assert rec["signal_ret"] == 0.0
        assert rec["gross_ticks"] == 0.0
        assert rec["net_ticks"] == 0.0

    def test_missing_signal_open_bar_skipped(self) -> None:
        """When 08:29 bar absent -> has_signal_coverage=False -> net_ticks=None."""
        date = "2024-06-12"
        # Only 14:29 and 14:59 bars; no 08:29 bar
        specs = [
            (date, _SIGNAL_END_BAR_H, _SIGNAL_END_BAR_M, 5050.0),
            (date, _TARGET_END_BAR_H, _TARGET_END_BAR_M, 5060.0),
        ]
        ts_ns, close = _make_bars(specs)
        rec = measure_day_ohlcv(ts_ns, close, date, 0, 2, cost_ticks=1.3)

        assert rec["has_signal_coverage"] is False
        assert rec["net_ticks"] is None

    def test_missing_signal_end_bar_skipped(self) -> None:
        """When 14:29 bar absent -> has_signal_coverage=False -> net_ticks=None."""
        date = "2024-06-12"
        # Only 08:29 and 14:59 bars; no 14:29 bar
        specs = [
            (date, _SIGNAL_OPEN_BAR_H, _SIGNAL_OPEN_BAR_M, 5000.0),
            (date, _TARGET_END_BAR_H, _TARGET_END_BAR_M, 5060.0),
        ]
        ts_ns, close = _make_bars(specs)
        rec = measure_day_ohlcv(ts_ns, close, date, 0, 2, cost_ticks=1.3)

        assert rec["has_signal_coverage"] is False
        assert rec["net_ticks"] is None

    def test_missing_target_bar_skipped(self) -> None:
        """When 14:59 bar absent -> has_target_coverage=False -> net_ticks=None."""
        date = "2024-06-12"
        specs = [
            (date, _SIGNAL_OPEN_BAR_H, _SIGNAL_OPEN_BAR_M, 5000.0),
            (date, _SIGNAL_END_BAR_H, _SIGNAL_END_BAR_M, 5050.0),
            # no 14:59 bar
        ]
        ts_ns, close = _make_bars(specs)
        rec = measure_day_ohlcv(ts_ns, close, date, 0, 2, cost_ticks=1.3)

        assert rec["has_signal_coverage"] is True
        assert rec["has_target_coverage"] is False
        assert rec["net_ticks"] is None

    def test_dst_spring_forward_day(self) -> None:
        """On 2024-03-10 (DST spring forward), boundary bars found by CT time.

        After spring forward, CT = CDT = UTC-5.
        08:29 CDT = 13:29 UTC; 14:29 CDT = 19:29 UTC; 14:59 CDT = 19:59 UTC.
        Verify that measure_day_ohlcv produces valid results for this day.
        """
        date = "2024-03-10"
        ts_ns, close, lo, hi = self._day_bars(date, 5100.0, 5120.0, 5130.0)
        rec = measure_day_ohlcv(ts_ns, close, date, lo, hi, cost_ticks=1.3)

        assert rec["has_signal_coverage"] is True
        assert rec["has_target_coverage"] is True
        assert rec["signal_ret"] > 0  # 5120 > 5100
        # gross_ticks = +1 * (5130-5120)/0.25 = 40; net = 38.7
        assert abs(rec["gross_ticks"] - 40.0) < 1e-9
        assert abs(rec["net_ticks"] - 38.7) < 1e-9

    def test_output_fields_present(self) -> None:
        """Record dict must carry all fields needed by _aggregate."""
        date = "2024-06-12"
        ts_ns, close, lo, hi = self._day_bars(date, 5000.0, 5010.0, 5015.0)
        rec = measure_day_ohlcv(ts_ns, close, date, lo, hi)

        required = {
            "date", "symbol", "signal_ret", "gross_ticks", "net_ticks",
            "has_signal_coverage", "has_target_coverage",
            "signal_open_px", "signal_end_px", "target_end_px",
        }
        assert required <= set(rec.keys())
        assert rec["date"] == date


# ===========================================================================
# Class 4: dbn_ohlcv — load_ohlcv_from_dbn
# ===========================================================================

class TestDbnOhlcv:
    """Tests for dbn_ohlcv.load_ohlcv_from_dbn decode layer."""

    def test_missing_file_raises(self) -> None:
        from options_lane.studies.dbn_ohlcv import load_ohlcv_from_dbn
        with pytest.raises(FileNotFoundError):
            load_ohlcv_from_dbn(Path("/nonexistent/path.dbn.zst"))

    @pytest.mark.skipif(_OHLCV_ABSENT, reason="Real OHLCV file not present")
    def test_real_file_bar_count_and_price_range(self) -> None:
        """Real file must have >1e5 bars with ES prices in plausible range."""
        from options_lane.studies.dbn_ohlcv import load_ohlcv_from_dbn
        arrays = load_ohlcv_from_dbn(_OHLCV_PATH)

        assert "ts_ns" in arrays
        assert "open" in arrays
        assert "high" in arrays
        assert "low" in arrays
        assert "close" in arrays
        assert "volume" in arrays

        n = len(arrays["ts_ns"])
        assert n > 100_000, f"Expected >100k bars, got {n}"

        cl = arrays["close"]
        assert cl.dtype == np.float64
        # ES futures price range 2021-2026: 3500 < price < 8000
        assert float(cl.min()) > 3000.0
        assert float(cl.max()) < 9000.0

        vol = arrays["volume"]
        assert vol.dtype == np.float64
        assert float(vol.min()) >= 0.0

    @pytest.mark.skipif(_OHLCV_ABSENT, reason="Real OHLCV file not present")
    def test_ts_ns_monotonically_increasing(self) -> None:
        """ts_ns array must be non-decreasing (bars are chronologically ordered)."""
        from options_lane.studies.dbn_ohlcv import load_ohlcv_from_dbn
        arrays = load_ohlcv_from_dbn(_OHLCV_PATH)
        ts = arrays["ts_ns"]
        assert bool(np.all(np.diff(ts) >= 0)), "ts_ns must be non-decreasing"


# ===========================================================================
# Class 5: run_measure_ohlcv — integration (skips when file absent)
# ===========================================================================

class TestRunMeasureOhlcvIntegration:
    """Integration tests for run_measure_ohlcv with real purchased data."""

    @pytest.mark.skipif(_OHLCV_ABSENT, reason="Real OHLCV file not present")
    def test_2024q1_end_to_end(self, tmp_path: Path) -> None:
        """Run measure-ohlcv over 2024-Q1 and assert summary properties.

        Asserts:
        - >30 trading days processed
        - skipped_day count reported (non-negative integer)
        - summary written to out_path
        - aggregate dict has n_days, hit_rate, mean_net_ticks, t_stat, by_year
        """
        out_path = tmp_path / "mom_ohlcv_2024q1.json"
        summary = run_measure_ohlcv(
            dbn_file=_OHLCV_PATH,
            out_path=out_path,
            cost_ticks=1.3,
            start_date="2024-01-01",
            end_date="2024-03-31",
        )

        assert summary["n_days_total"] > 30, (
            f"Expected >30 trading days, got {summary['n_days_total']}"
        )
        assert isinstance(summary["skipped_days"], int)
        assert summary["skipped_days"] >= 0
        assert summary["skipped_days"] < summary["n_days_total"]

        agg = summary["aggregate"]
        assert agg["n_days"] > 30

        # hit_rate in [0, 1]
        assert 0.0 <= agg["hit_rate"] <= 1.0

        # t_stat is a finite float
        assert agg["t_stat"] is not None
        assert math.isfinite(agg["t_stat"])

        # by_year must contain 2024 (the only year in 2024-Q1)
        assert "2024" in agg["by_year"]
        assert agg["by_year"]["2024"]["n_days"] == agg["n_days"]

        # Output file written
        assert out_path.exists()

        # Print the actual summary for the orchestrator notes
        print("\n=== WS-1.3 OHLCV 2024-Q1 AGGREGATE SUMMARY ===")
        print(json.dumps(summary, indent=2))
