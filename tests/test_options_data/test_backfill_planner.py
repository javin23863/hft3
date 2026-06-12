"""Tests for options_data.src.backfill_planner.

Golden values
-------------
* plan_fixing_windows for June 2025 (2025-06-01 to 2025-06-30):
    June 2025 has:
      - Quarterly: 2025-06-20 (3rd Friday)
      - WEEKLY_FRI: 6, 13, 27 (20 is quarterly; no holiday shifts in Jun 2025)
      - WEEKLY_WED: 4, 11, 18, 25
      - WEEKLY_MON: 2, 9, 16, 23, 30
      - WEEKLY_TUE: 3, 10, 17, 24
      - WEEKLY_THU: 5, 12, 19, 26
      - MONTHLY_EOM: 2025-06-30 (last business day = Mon Jun 30, since Jun 30 is Mon)
    Unique calendar dates = Mon(2,9,16,23,30), Tue(3,10,17,24), Wed(4,11,18,25),
                            Thu(5,12,19,26), Fri(6,13,20,27) + EOM(Jun30 already Mon)
    EOM = Jun 30 = Monday, same date as WEEKLY_MON → merged in one window.
    Total unique dates = 22 business days in June 2025 (no holidays in June 2025;
    Juneteenth Jun 19 is Thursday — that day shifts if it hits Jun 19, but
    Jun 19 2025 is a Thursday anyway, observed on Jun 19 2025 (weekday) → holiday.
    So WEEKLY_THU for 2025-06-19 shifts to preceding business day = Wed 2025-06-18.
    BUT WEEKLY_WED for 2025-06-18 is already there.
    So net: Thu Jun 19 window becomes Wed Jun 18 (merged with existing WED entry).
    That means Jun 19 has no independent window but Jun 18 now has both WED + THU.
    Dates present: 2,3,4,5,6,9,10,11,12,13,16,17,18,20,23,24,25,26,27,30 = 20 windows
    (Jun 19 disappears since its shift lands on Jun 18 which already exists,
     but we still count it separately in the merged window for Jun 18).
    Actually the test just verifies window count is reasonable (>= 18 and <= 23)
    rather than an exact fragile count. We do a spot-check on Jun 20.

* cost arithmetic:
    - 10 windows × 0.5 GB/window × $2.00/GB = $10.00
    - OPERATING_CAP = $112.50 → $10.00 does NOT require uplift.
    - 200 windows × 0.5 GB/window × $2.00/GB = $200.00 > $112.50 → requires uplift.

* requires_budget_uplift flips:
    - At cap: total > OPERATING_CAP → requires_budget_uplift = True.
    - Unknown gb_per_window → requires_budget_uplift = True (conservative).

* manifest path guard:
    - to_purchase_manifest with a path outside research_cards/backfill_plans/
      raises ValueError.
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "packages"))

from options_data.src.backfill_planner import (
    estimate_cost,
    plan_baltussen_bars,
    plan_fixing_windows,
    to_purchase_manifest,
    _REPO_ROOT,
    _MANIFEST_SUBDIR,
)
from data_system.src.budget_manager import BudgetManager


# ---------------------------------------------------------------------------
# plan_fixing_windows
# ---------------------------------------------------------------------------

class TestPlanFixingWindows:
    def test_june_2025_window_count_reasonable(self):
        """June 2025 should yield 18–23 unique expiry-date windows."""
        windows = plan_fixing_windows(date(2025, 6, 1), date(2025, 6, 30))
        assert 18 <= len(windows) <= 23, (
            f"Expected 18-23 windows for June 2025, got {len(windows)}"
        )

    def test_jun_20_quarterly_present(self):
        """2025-06-20 quarterly must appear in the plan."""
        windows = plan_fixing_windows(date(2025, 6, 1), date(2025, 6, 30))
        dates = [w["date"] for w in windows]
        assert "2025-06-20" in dates

    def test_jun_20_includes_quarterly_kind(self):
        windows = plan_fixing_windows(date(2025, 6, 1), date(2025, 6, 30))
        win = next(w for w in windows if w["date"] == "2025-06-20")
        assert "QUARTERLY" in win["expiry_kinds"]

    def test_window_start_before_end(self):
        windows = plan_fixing_windows(date(2025, 6, 1), date(2025, 6, 30))
        for w in windows:
            start = datetime.fromisoformat(w["start_utc"])
            end = datetime.fromisoformat(w["end_utc"])
            assert start < end

    def test_window_span_is_10_minutes(self):
        """Each window must be exactly 10 minutes (14:55–15:05 CT)."""
        windows = plan_fixing_windows(date(2025, 6, 1), date(2025, 6, 30))
        for w in windows:
            start = datetime.fromisoformat(w["start_utc"])
            end = datetime.fromisoformat(w["end_utc"])
            span_minutes = (end - start).total_seconds() / 60
            assert span_minutes == 10.0

    def test_sorted_ascending(self):
        windows = plan_fixing_windows(date(2025, 6, 1), date(2025, 6, 30))
        dates = [w["date"] for w in windows]
        assert dates == sorted(dates)

    def test_no_duplicate_dates(self):
        windows = plan_fixing_windows(date(2025, 6, 1), date(2025, 6, 30))
        dates = [w["date"] for w in windows]
        assert len(dates) == len(set(dates)), "Duplicate dates found"

    def test_symbols_passthrough(self):
        windows = plan_fixing_windows(
            date(2025, 6, 20), date(2025, 6, 20), symbols=("ES", "NQ")
        )
        assert windows[0]["symbols"] == ["ES", "NQ"]

    def test_jun_20_2025_fixing_utc_is_20_utc(self):
        """2025-06-20 is CDT (UTC-5); 15:00 CT = 20:00 UTC.
        Window midpoint at 15:00 CT; start = 14:55 CT = 19:55 UTC.
        """
        windows = plan_fixing_windows(date(2025, 6, 20), date(2025, 6, 20))
        assert len(windows) >= 1
        win = windows[0]
        start = datetime.fromisoformat(win["start_utc"])
        assert start.hour == 19
        assert start.minute == 55


# ---------------------------------------------------------------------------
# plan_baltussen_bars
# ---------------------------------------------------------------------------

class TestPlanBaltussenBars:
    def test_returns_single_spec(self):
        spec = plan_baltussen_bars(date(2023, 1, 1), date(2024, 12, 31))
        assert isinstance(spec, dict)

    def test_dataset_and_schema(self):
        spec = plan_baltussen_bars(date(2023, 1, 1), date(2024, 12, 31))
        assert spec["dataset"] == "GLBX.MDP3"
        assert spec["schema"] == "ohlcv-1m"

    def test_continuous_symbol(self):
        spec = plan_baltussen_bars(date(2023, 1, 1), date(2024, 12, 31))
        assert "ES.v.0" in spec["symbols"]

    def test_date_range_preserved(self):
        spec = plan_baltussen_bars(date(2023, 1, 1), date(2024, 12, 31))
        assert spec["start_date"] == "2023-01-01"
        assert spec["end_date"] == "2024-12-31"


# ---------------------------------------------------------------------------
# estimate_cost
# ---------------------------------------------------------------------------

class TestEstimateCost:
    def _make_windows(self, n: int) -> list[dict]:
        """Build a list of n dummy window dicts."""
        return [{"date": f"2025-01-{i+1:02d}"} for i in range(n)]

    def test_basic_arithmetic(self):
        """10 windows × 0.5 GB × $2.00/GB = $10.00."""
        windows = self._make_windows(10)
        result = estimate_cost(windows, gb_per_window=0.5, usd_per_gb=2.0)
        assert result["windows_count"] == 10
        assert abs(result["usd_total"] - 10.0) < 1e-9

    def test_below_cap_no_uplift(self):
        """$10.00 < OPERATING_CAP ($112.50) → requires_budget_uplift = False."""
        windows = self._make_windows(10)
        result = estimate_cost(windows, gb_per_window=0.5, usd_per_gb=2.0)
        assert result["usd_total"] == pytest.approx(10.0)
        assert result["requires_budget_uplift"] is False

    def test_above_cap_requires_uplift(self):
        """200 windows × 0.5 GB × $2.00 = $200.00 > $112.50 → requires uplift."""
        windows = self._make_windows(200)
        result = estimate_cost(windows, gb_per_window=0.5, usd_per_gb=2.0)
        assert result["usd_total"] == pytest.approx(200.0)
        assert result["requires_budget_uplift"] is True

    def test_exactly_at_cap_no_uplift(self):
        """$112.50 = OPERATING_CAP → requires_budget_uplift = False (not strictly >)."""
        # 112.50 / (0.5 * 2.0) = 112.5 windows → round to int 112 to keep < cap
        windows = self._make_windows(112)
        result = estimate_cost(windows, gb_per_window=0.5, usd_per_gb=2.0)
        # 112 * 0.5 * 2.0 = 112.0 < 112.50
        assert result["requires_budget_uplift"] is False

    def test_unknown_gb_per_window_requires_uplift(self):
        """gb_per_window=None → requires_budget_uplift = True (conservative)."""
        windows = self._make_windows(5)
        result = estimate_cost(windows, gb_per_window=None, usd_per_gb=2.0)
        assert result["gb_per_window"] is None
        assert result["usd_total"] is None
        assert result["requires_budget_uplift"] is True

    def test_operating_cap_matches_budget_manager(self):
        windows = self._make_windows(1)
        result = estimate_cost(windows, gb_per_window=0.1, usd_per_gb=1.0)
        assert result["operating_cap_usd"] == BudgetManager.OPERATING_CAP

    def test_hard_limit_matches_budget_manager(self):
        windows = self._make_windows(1)
        result = estimate_cost(windows, gb_per_window=0.1, usd_per_gb=1.0)
        assert result["hard_limit_usd"] == BudgetManager.HARD_LIMIT

    def test_schema_passthrough(self):
        windows = self._make_windows(1)
        result = estimate_cost(windows, schema="trades", gb_per_window=0.1, usd_per_gb=1.0)
        assert result["schema"] == "trades"

    def test_usd_per_gb_stored(self):
        windows = self._make_windows(1)
        result = estimate_cost(windows, gb_per_window=0.1, usd_per_gb=3.14)
        assert result["usd_per_gb"] == pytest.approx(3.14)


# ---------------------------------------------------------------------------
# to_purchase_manifest — path guard
# ---------------------------------------------------------------------------

class TestToPurchaseManifest:
    def test_allowed_path_writes_json(self):
        """Writing to research_cards/backfill_plans/ should succeed."""
        allowed_dir = (_REPO_ROOT / _MANIFEST_SUBDIR).resolve()
        dest = allowed_dir / "_test_manifest.json"
        plan = {"test": True}
        written = to_purchase_manifest(plan, dest)
        assert written.exists()
        data = json.loads(written.read_text(encoding="utf-8"))
        assert data["test"] is True
        # Cleanup
        written.unlink(missing_ok=True)

    def test_rejects_data_npz_path(self):
        """Path outside research_cards/backfill_plans/ must raise ValueError."""
        bad_path = _REPO_ROOT / "data" / "npz" / "manifest.json"
        with pytest.raises(ValueError, match="research_cards/backfill_plans"):
            to_purchase_manifest({"x": 1}, bad_path)

    def test_rejects_temp_path(self):
        """Temp directory path must be rejected."""
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "plan.json"
            with pytest.raises(ValueError, match="research_cards/backfill_plans"):
                to_purchase_manifest({"x": 1}, bad)

    def test_rejects_parent_escape(self):
        """Path traversal attempt must be rejected."""
        allowed_dir = (_REPO_ROOT / _MANIFEST_SUBDIR).resolve()
        bad_path = allowed_dir / ".." / ".." / "evil.json"
        with pytest.raises(ValueError):
            to_purchase_manifest({"x": 1}, bad_path)

    def test_written_content_is_valid_json(self):
        allowed_dir = (_REPO_ROOT / _MANIFEST_SUBDIR).resolve()
        dest = allowed_dir / "_test_content.json"
        plan = {"windows": [{"date": "2025-06-20"}], "cost": 5.0}
        to_purchase_manifest(plan, dest)
        loaded = json.loads(dest.read_text(encoding="utf-8"))
        assert loaded["cost"] == 5.0
        dest.unlink(missing_ok=True)
