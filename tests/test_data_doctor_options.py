"""Tests for the options-lane checks in scripts/data_doctor.py."""
from __future__ import annotations

import importlib
import json
import sys
from datetime import date
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Ensure repo root + packages on sys.path (same pattern as test_build_event_lake).
# ---------------------------------------------------------------------------
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
if str(_REPO / "packages") not in sys.path:
    sys.path.insert(0, str(_REPO / "packages"))

_SCRIPTS = _REPO / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------
from options_data.src.expiry_calendar import expiries_between  # noqa: E402


def _load_dd():
    """Import data_doctor fresh (or return cached module)."""
    import data_doctor as dd  # noqa: E402
    return dd


# ---------------------------------------------------------------------------
# Per-test fixture: reset the module-level checks list so tests are isolated.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _reset_checks():
    """Reset data_doctor.checks before every test."""
    dd = _load_dd()
    original = dd.checks[:]
    dd.checks.clear()
    yield
    dd.checks.clear()
    dd.checks.extend(original)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SHORT_START = date(2026, 1, 5)
_SHORT_TODAY = date(2026, 2, 2)


def _expected_dates(start: date = _SHORT_START, today: date = _SHORT_TODAY) -> list[str]:
    return sorted({d.isoformat() for d, _ in expiries_between(start, today)})


def _build_lake(tmp_path: Path, *, dates: list[str] | None = None,
                include_trades: bool = False,
                ohlcv: bool = True,
                definitions: bool = True,
                statistics: bool = False) -> Path:
    """Build a synthetic lake under tmp_path/lake with options/ sub-tree."""
    lroot = tmp_path / "lake"
    opt = lroot / "options"

    fixing = opt / "fixing_mbo"
    fixing.mkdir(parents=True, exist_ok=True)
    if dates is not None:
        for d in dates:
            (fixing / f"ES_fixing_{d}.dbn.zst").write_bytes(b"dummy")
            if include_trades:
                (fixing / f"ES_fixing_trades_{d}.dbn.zst").write_bytes(b"dummy")

    if ohlcv:
        ohlcv_dir = opt / "ohlcv"
        ohlcv_dir.mkdir(parents=True, exist_ok=True)
        (ohlcv_dir / "ES_ohlcv_2026.dbn.zst").write_bytes(b"dummy")

    if definitions:
        defs = opt / "definitions" / "JOBX"
        defs.mkdir(parents=True, exist_ok=True)
        (defs / "file.dbn.zst").write_bytes(b"dummy")

    if statistics:
        stats = opt / "statistics"
        stats.mkdir(parents=True, exist_ok=True)
        (stats / "ES_stats.dbn.zst").write_bytes(b"dummy")

    return lroot


# ---------------------------------------------------------------------------
# Test 1: lake without options/ -> WARN, returns None, zero FAILs
# ---------------------------------------------------------------------------

def test_no_options_dir(tmp_path: Path) -> None:
    dd = _load_dd()
    lroot = tmp_path / "lake"
    lroot.mkdir()
    result = dd.options_lane_checks(lroot, today=_SHORT_TODAY, start=_SHORT_START)
    assert result is None
    assert len(dd.checks) == 1
    c = dd.checks[0]
    assert c["name"] == "options-datasets"
    assert c["status"] == "WARN"
    # No FAILs
    fails = [x for x in dd.checks if x["status"] == "FAIL"]
    assert fails == []


# ---------------------------------------------------------------------------
# Test 2: complete synthetic lake -> all OK except options-statistics WARN
# ---------------------------------------------------------------------------

def test_complete_lake_all_ok_except_statistics(tmp_path: Path) -> None:
    dd = _load_dd()
    dates = _expected_dates()
    # Remove dates that are covered elsewhere so coverage is satisfied
    covered = dd.OPTIONS_FIXING_COVERED_ELSEWHERE
    effective_dates = [d for d in dates if d not in covered]
    lroot = _build_lake(tmp_path, dates=effective_dates, ohlcv=True, definitions=True, statistics=False)
    result = dd.options_lane_checks(lroot, today=_SHORT_TODAY, start=_SHORT_START)
    assert result is not None

    by_name = {c["name"]: c for c in dd.checks}

    assert by_name["options-datasets"]["status"] == "OK"
    assert by_name["options-fixing-mbo"]["status"] == "OK"
    assert by_name["options-fixing-coverage"]["status"] == "OK"
    assert by_name["options-ohlcv"]["status"] == "OK"
    assert by_name["options-definitions"]["status"] == "OK"
    assert by_name["options-statistics"]["status"] == "WARN"

    fails = [c for c in dd.checks if c["status"] == "FAIL"]
    assert fails == []


# ---------------------------------------------------------------------------
# Test 3: date present only as trades file -> counts as covered
# ---------------------------------------------------------------------------

def test_trades_only_counts_as_covered(tmp_path: Path) -> None:
    dd = _load_dd()
    dates = _expected_dates()
    covered = dd.OPTIONS_FIXING_COVERED_ELSEWHERE
    effective_dates = [d for d in dates if d not in covered]

    # Build lake with one date only as trades (no quote file for it)
    if not effective_dates:
        pytest.skip("no effective dates in short range")

    trade_only_date = effective_dates[0]
    other_dates = effective_dates[1:]

    lroot = tmp_path / "lake"
    opt = lroot / "options"
    fixing = opt / "fixing_mbo"
    fixing.mkdir(parents=True, exist_ok=True)
    # Write the first date as trades-only
    (fixing / f"ES_fixing_trades_{trade_only_date}.dbn.zst").write_bytes(b"dummy")
    # Write rest as quotes
    for d in other_dates:
        (fixing / f"ES_fixing_{d}.dbn.zst").write_bytes(b"dummy")
    # Add ohlcv and definitions
    ohlcv_dir = opt / "ohlcv"
    ohlcv_dir.mkdir(parents=True, exist_ok=True)
    (ohlcv_dir / "ES_ohlcv_2026.dbn.zst").write_bytes(b"dummy")
    defs = opt / "definitions" / "JOBX"
    defs.mkdir(parents=True, exist_ok=True)
    (defs / "file.dbn.zst").write_bytes(b"dummy")

    result = dd.options_lane_checks(lroot, today=_SHORT_TODAY, start=_SHORT_START)
    assert result is not None

    by_name = {c["name"]: c for c in dd.checks}
    assert by_name["options-fixing-coverage"]["status"] == "OK"

    # Verify the trades-only date appears in dates_covered
    assert result["fixing_mbo"]["dates_covered"] == len(effective_dates)


# ---------------------------------------------------------------------------
# Test 4: remove most recent expected date (within 5-day grace) -> WARN not FAIL
# ---------------------------------------------------------------------------

def test_recent_gap_is_warn_not_fail(tmp_path: Path) -> None:
    dd = _load_dd()
    # Use today=_SHORT_TODAY, drop the most recent date that is within grace
    dates = _expected_dates()
    covered = dd.OPTIONS_FIXING_COVERED_ELSEWHERE
    effective_dates = sorted(d for d in dates if d not in covered)
    if not effective_dates:
        pytest.skip("no effective dates in short range")

    # Find a date within grace_days of _SHORT_TODAY
    grace = dd.OPTIONS_VENDOR_LAG_GRACE_DAYS
    within_grace = [
        d for d in effective_dates
        if (_SHORT_TODAY - date.fromisoformat(d)).days <= grace
    ]
    if not within_grace:
        pytest.skip("no within-grace dates in short range")

    drop_date = within_grace[-1]
    present_dates = [d for d in effective_dates if d != drop_date]

    lroot = _build_lake(tmp_path, dates=present_dates, ohlcv=True, definitions=True)
    result = dd.options_lane_checks(lroot, today=_SHORT_TODAY, start=_SHORT_START)
    assert result is not None

    by_name = {c["name"]: c for c in dd.checks}
    cov = by_name["options-fixing-coverage"]
    assert cov["status"] == "WARN", f"expected WARN but got {cov['status']}: {cov['detail']}"

    # The gap must appear in summary
    assert result["expiry_coverage"]["gap_count"] >= 1
    assert result["expiry_coverage"]["stale_gap_count"] == 0


# ---------------------------------------------------------------------------
# Test 5: remove an old date (> grace) -> FAIL
# ---------------------------------------------------------------------------

def test_old_gap_is_fail(tmp_path: Path) -> None:
    dd = _load_dd()
    # Use a range where we can easily have an old gap
    start = date(2026, 1, 5)
    today = date(2026, 2, 15)  # 10+ days after _SHORT_TODAY

    all_dates = sorted({d.isoformat() for d, _ in expiries_between(start, today)})
    covered = dd.OPTIONS_FIXING_COVERED_ELSEWHERE

    effective_dates = sorted(d for d in all_dates if d not in covered)
    if not effective_dates:
        pytest.skip("no effective dates")

    grace = dd.OPTIONS_VENDOR_LAG_GRACE_DAYS
    # Find a date more than grace_days before today
    old_gaps = [
        d for d in effective_dates
        if (today - date.fromisoformat(d)).days > grace
    ]
    if not old_gaps:
        pytest.skip("no stale dates available in range")

    drop_date = old_gaps[0]
    present_dates = [d for d in effective_dates if d != drop_date]

    lroot = _build_lake(tmp_path, dates=present_dates, ohlcv=True, definitions=True)
    result = dd.options_lane_checks(lroot, today=today, start=start)
    assert result is not None

    by_name = {c["name"]: c for c in dd.checks}
    cov = by_name["options-fixing-coverage"]
    assert cov["status"] == "FAIL", f"expected FAIL but got {cov['status']}: {cov['detail']}"
    assert result["expiry_coverage"]["stale_gap_count"] >= 1


# ---------------------------------------------------------------------------
# Test 6: monkeypatch OPTIONS_FIXING_COVERED_ELSEWHERE to cover a missing date -> OK
# ---------------------------------------------------------------------------

def test_covered_elsewhere_removes_gap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dd = _load_dd()
    dates = _expected_dates()
    covered_orig = dd.OPTIONS_FIXING_COVERED_ELSEWHERE
    effective_dates = sorted(d for d in dates if d not in covered_orig)
    if not effective_dates:
        pytest.skip("no effective dates in short range")

    missing_date = effective_dates[0]
    present_dates = [d for d in effective_dates if d != missing_date]

    # Monkeypatch to include missing_date in covered set
    new_covered = covered_orig | {missing_date}
    monkeypatch.setattr(dd, "OPTIONS_FIXING_COVERED_ELSEWHERE", new_covered)

    lroot = _build_lake(tmp_path, dates=present_dates, ohlcv=True, definitions=True)
    result = dd.options_lane_checks(lroot, today=_SHORT_TODAY, start=_SHORT_START)
    assert result is not None

    by_name = {c["name"]: c for c in dd.checks}
    assert by_name["options-fixing-coverage"]["status"] == "OK"
    assert result["expiry_coverage"]["gap_count"] == 0


# ---------------------------------------------------------------------------
# Test 7: summary block shape — all keys present, json.dumps round-trips
# ---------------------------------------------------------------------------

def test_summary_shape_and_json(tmp_path: Path) -> None:
    dd = _load_dd()
    dates = _expected_dates()
    covered = dd.OPTIONS_FIXING_COVERED_ELSEWHERE
    effective_dates = [d for d in dates if d not in covered]

    lroot = _build_lake(tmp_path, dates=effective_dates, ohlcv=True, definitions=True, statistics=True)
    result = dd.options_lane_checks(lroot, today=_SHORT_TODAY, start=_SHORT_START)
    assert result is not None

    # Top-level keys
    assert "as_of_utc" in result
    assert "fixing_mbo" in result
    assert "expiry_coverage" in result
    assert "ohlcv" in result
    assert "definitions" in result
    assert "statistics" in result

    # fixing_mbo sub-keys
    fm = result["fixing_mbo"]
    for k in ("quote_files", "trades_files", "dates_covered", "first_date", "last_date"):
        assert k in fm, f"missing key fixing_mbo.{k}"

    # expiry_coverage sub-keys
    ec = result["expiry_coverage"]
    for k in ("expected_dates", "covered_elsewhere", "gaps", "gap_count",
              "stale_gap_count", "grace_days", "calendar"):
        assert k in ec, f"missing key expiry_coverage.{k}"

    # ohlcv sub-keys
    for k in ("files", "names"):
        assert k in result["ohlcv"], f"missing key ohlcv.{k}"

    # definitions sub-keys
    for k in ("files", "batches"):
        assert k in result["definitions"], f"missing key definitions.{k}"

    # statistics sub-keys
    for k in ("files", "state"):
        assert k in result["statistics"], f"missing key statistics.{k}"

    # json round-trip
    dumped = json.dumps(result)
    loaded = json.loads(dumped)
    assert loaded["fixing_mbo"]["dates_covered"] == result["fixing_mbo"]["dates_covered"]
    assert loaded["statistics"]["state"] == "present"
