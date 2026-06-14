"""Tests for the options-lane checks in scripts/data_doctor.py."""
from __future__ import annotations

import hashlib
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
def _reset_checks(monkeypatch: pytest.MonkeyPatch):
    """Reset data_doctor.checks before every test."""
    dd = _load_dd()
    real_sample_valid = dd._dbn_sample_valid

    def _fixture_sample_valid(path: Path, expected_schema: str | None = None, expected_record_count: int | None = None):
        if path.read_bytes() == b"valid-dbn-fixture":
            if expected_record_count is not None and int(expected_record_count) <= 0:
                return False, "record_count <= 0"
            return True, "fixture dbn sample ok"
        return real_sample_valid(path, expected_schema=expected_schema, expected_record_count=expected_record_count)

    monkeypatch.setattr(dd, "_dbn_sample_valid", _fixture_sample_valid)
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


def _write_dummy_dbn(path: Path, schema: str, *, record_count: int = 1) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"valid-dbn-fixture")
    blob = path.read_bytes()
    path.with_name(f"{path.name}.doctor.json").write_text(
        json.dumps(
            {
                "valid": True,
                "schema": schema,
                "record_count": record_count,
                "size_bytes": len(blob),
                "sha256": hashlib.sha256(blob).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    return path


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
            _write_dummy_dbn(fixing / f"ES_fixing_{d}.dbn.zst", "mbo")
            if include_trades:
                _write_dummy_dbn(fixing / f"ES_fixing_trades_{d}.dbn.zst", "trades")

    if ohlcv:
        ohlcv_dir = opt / "ohlcv"
        ohlcv_dir.mkdir(parents=True, exist_ok=True)
        _write_dummy_dbn(ohlcv_dir / "ES_ohlcv_2026.dbn.zst", "ohlcv-1m")

    if definitions:
        defs = opt / "definitions" / "JOBX"
        defs.mkdir(parents=True, exist_ok=True)
        _write_dummy_dbn(defs / "file.dbn.zst", "definition")

    if statistics:
        stats = opt / "statistics"
        stats.mkdir(parents=True, exist_ok=True)
        _write_dummy_dbn(stats / "ES_stats.dbn.zst", "statistics")

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
# Test 2: complete synthetic lake -> all OK
# ---------------------------------------------------------------------------

def test_complete_lake_all_ok(tmp_path: Path) -> None:
    dd = _load_dd()
    dates = _expected_dates()
    # Remove dates that are covered elsewhere so coverage is satisfied
    covered = dd.OPTIONS_FIXING_COVERED_ELSEWHERE
    effective_dates = [d for d in dates if d not in covered]
    lroot = _build_lake(tmp_path, dates=effective_dates, ohlcv=True, definitions=True, statistics=True)
    result = dd.options_lane_checks(lroot, today=_SHORT_TODAY, start=_SHORT_START)
    assert result is not None

    by_name = {c["name"]: c for c in dd.checks}

    assert by_name["options-datasets"]["status"] == "OK"
    assert by_name["options-fixing-mbo"]["status"] == "OK"
    assert by_name["options-fixing-coverage"]["status"] == "OK"
    assert by_name["options-ohlcv"]["status"] == "OK"
    assert by_name["options-definitions"]["status"] == "OK"
    assert by_name["options-statistics"]["status"] == "OK"

    fails = [c for c in dd.checks if c["status"] == "FAIL"]
    assert fails == []


# ---------------------------------------------------------------------------
# Test 3: date present only as trades file -> does not cover fixing MBO
# ---------------------------------------------------------------------------

def test_trades_only_does_not_count_as_fixing_mbo_covered(tmp_path: Path) -> None:
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
    _write_dummy_dbn(fixing / f"ES_fixing_trades_{trade_only_date}.dbn.zst", "trades")
    # Write rest as quotes
    for d in other_dates:
        _write_dummy_dbn(fixing / f"ES_fixing_{d}.dbn.zst", "mbo")
    # Add ohlcv and definitions
    ohlcv_dir = opt / "ohlcv"
    ohlcv_dir.mkdir(parents=True, exist_ok=True)
    _write_dummy_dbn(ohlcv_dir / "ES_ohlcv_2026.dbn.zst", "ohlcv-1m")
    defs = opt / "definitions" / "JOBX"
    defs.mkdir(parents=True, exist_ok=True)
    _write_dummy_dbn(defs / "file.dbn.zst", "definition")
    stats = opt / "statistics"
    stats.mkdir(parents=True, exist_ok=True)
    _write_dummy_dbn(stats / "ES_stats.dbn.zst", "statistics")

    result = dd.options_lane_checks(lroot, today=_SHORT_TODAY, start=_SHORT_START)
    assert result is not None

    by_name = {c["name"]: c for c in dd.checks}
    assert by_name["options-fixing-coverage"]["status"] == "OK"
    assert by_name["options-fixing-mbo-coverage"]["status"] == "WARN"

    assert result["fixing_mbo"]["dates_covered"] == len(other_dates)
    assert result["fixing_mbo"]["study_dates_covered"] == len(effective_dates)
    assert result["fixing_mbo"]["trade_only_dates"] == 1
    assert result["expiry_coverage"]["gap_count"] == 0
    assert result["expiry_coverage"]["strict_mbo_gap_count"] >= 1


def test_all_trades_only_lake_satisfies_fixing_study_presence(tmp_path: Path) -> None:
    dd = _load_dd()
    dates = _expected_dates()
    lroot = tmp_path / "lake"
    opt = lroot / "options"
    fixing = opt / "fixing_mbo"
    fixing.mkdir(parents=True, exist_ok=True)
    for d in dates:
        _write_dummy_dbn(fixing / f"ES_fixing_trades_{d}.dbn.zst", "trades")
    ohlcv_dir = opt / "ohlcv"
    ohlcv_dir.mkdir(parents=True, exist_ok=True)
    _write_dummy_dbn(ohlcv_dir / "ES_ohlcv_2026.dbn.zst", "ohlcv-1m")
    defs = opt / "definitions" / "JOBX"
    defs.mkdir(parents=True, exist_ok=True)
    _write_dummy_dbn(defs / "file.dbn.zst", "definition")
    stats = opt / "statistics"
    stats.mkdir(parents=True, exist_ok=True)
    _write_dummy_dbn(stats / "ES_stats.dbn.zst", "statistics")

    result = dd.options_lane_checks(lroot, today=_SHORT_TODAY, start=_SHORT_START)

    assert result is not None
    by_name = {c["name"]: c for c in dd.checks}
    assert by_name["options-fixing-mbo"]["status"] == "OK"
    assert by_name["options-fixing-coverage"]["status"] == "OK"
    assert by_name["options-fixing-mbo-coverage"]["status"] == "WARN"
    assert result["fixing_mbo"]["dates_covered"] == 0
    assert result["fixing_mbo"]["study_dates_covered"] == len(dates)
    assert result["expiry_coverage"]["gap_count"] == 0
    assert result["expiry_coverage"]["strict_mbo_gap_count"] == len(dates)


def test_missing_statistics_is_fail(tmp_path: Path) -> None:
    dd = _load_dd()
    dates = _expected_dates()
    covered = dd.OPTIONS_FIXING_COVERED_ELSEWHERE
    effective_dates = [d for d in dates if d not in covered]
    lroot = _build_lake(tmp_path, dates=effective_dates, ohlcv=True, definitions=True, statistics=False)
    result = dd.options_lane_checks(lroot, today=_SHORT_TODAY, start=_SHORT_START)
    assert result is not None
    by_name = {c["name"]: c for c in dd.checks}
    assert by_name["options-statistics"]["status"] == "FAIL"


def test_zero_byte_fixing_file_is_invalid(tmp_path: Path) -> None:
    dd = _load_dd()
    dates = _expected_dates()
    if not dates:
        pytest.skip("no dates in short range")
    bad_date = dates[0]
    lroot = _build_lake(tmp_path, dates=dates[1:], ohlcv=True, definitions=True, statistics=True)
    fixing = lroot / "options" / "fixing_mbo"
    (fixing / f"ES_fixing_{bad_date}.dbn.zst").write_bytes(b"")

    result = dd.options_lane_checks(lroot, today=_SHORT_TODAY, start=_SHORT_START)

    assert result is not None
    assert result["fixing_mbo"]["invalid_files"] == 1
    assert result["expiry_coverage"]["gap_count"] >= 1
    [diag] = [
        row for row in result["expiry_coverage"]["gap_diagnostics"]
        if row["date"] == bad_date
    ]
    assert diag["status"] == "FAIL"
    assert diag["reason"] == "invalid_artifact"
    assert diag["stale"] is True
    assert diag["required_action"] == "replace_invalid_artifact_or_manifest_no_data_proof"
    assert diag["retry_window"]["date"] == bad_date
    assert diag["invalid_artifacts"] == [
        {
            "file": f"ES_fixing_{bad_date}.dbn.zst",
            "schema": "mbo",
            "reason": "missing_or_empty",
        }
    ]


def test_wrong_schema_sidecar_rejects_fixing_mbo(tmp_path: Path) -> None:
    dd = _load_dd()
    dates = _expected_dates()
    if not dates:
        pytest.skip("no dates in short range")
    bad_date = dates[0]
    lroot = _build_lake(tmp_path, dates=dates, ohlcv=True, definitions=True, statistics=True)
    bad_file = lroot / "options" / "fixing_mbo" / f"ES_fixing_{bad_date}.dbn.zst"
    bad_file.with_name(f"{bad_file.name}.doctor.json").write_text(
        json.dumps({"valid": True, "schema": "trades", "record_count": 1}),
        encoding="utf-8",
    )

    result = dd.options_lane_checks(lroot, today=_SHORT_TODAY, start=_SHORT_START)

    assert result is not None
    assert result["fixing_mbo"]["invalid_files"] == 1
    assert result["expiry_coverage"]["gap_count"] >= 1


def test_incomplete_sidecar_rejects_fixing_mbo(tmp_path: Path) -> None:
    dd = _load_dd()
    dates = _expected_dates()
    if not dates:
        pytest.skip("no dates in short range")
    bad_date = dates[0]
    lroot = _build_lake(tmp_path, dates=dates, ohlcv=True, definitions=True, statistics=True)
    bad_file = lroot / "options" / "fixing_mbo" / f"ES_fixing_{bad_date}.dbn.zst"
    bad_file.with_name(f"{bad_file.name}.doctor.json").write_text(
        json.dumps({"valid": True}),
        encoding="utf-8",
    )

    result = dd.options_lane_checks(lroot, today=_SHORT_TODAY, start=_SHORT_START)

    assert result is not None
    assert result["fixing_mbo"]["invalid_files"] == 1
    assert result["expiry_coverage"]["gap_count"] >= 1


def test_missing_valid_flag_sidecar_rejects_fixing_mbo(tmp_path: Path) -> None:
    dd = _load_dd()
    dates = _expected_dates()
    if not dates:
        pytest.skip("no dates in short range")
    bad_date = dates[0]
    lroot = _build_lake(tmp_path, dates=dates, ohlcv=True, definitions=True, statistics=True)
    bad_file = lroot / "options" / "fixing_mbo" / f"ES_fixing_{bad_date}.dbn.zst"
    bad_file.with_name(f"{bad_file.name}.doctor.json").write_text(
        json.dumps({"schema": "mbo", "record_count": 1}),
        encoding="utf-8",
    )

    result = dd.options_lane_checks(lroot, today=_SHORT_TODAY, start=_SHORT_START)

    assert result is not None
    assert result["fixing_mbo"]["invalid_files"] == 1
    assert result["expiry_coverage"]["gap_count"] >= 1


def test_stale_sidecar_rejects_corrupt_fixing_mbo(tmp_path: Path) -> None:
    dd = _load_dd()
    dates = _expected_dates()
    if not dates:
        pytest.skip("no dates in short range")
    bad_date = dates[0]
    lroot = _build_lake(tmp_path, dates=dates, ohlcv=True, definitions=True, statistics=True)
    bad_file = lroot / "options" / "fixing_mbo" / f"ES_fixing_{bad_date}.dbn.zst"
    bad_file.write_bytes(b"dumMy")

    result = dd.options_lane_checks(lroot, today=_SHORT_TODAY, start=_SHORT_START)

    assert result is not None
    assert result["fixing_mbo"]["invalid_files"] == 1
    assert result["expiry_coverage"]["gap_count"] >= 1


def test_large_corrupt_dbn_is_rejected(tmp_path: Path) -> None:
    dd = _load_dd()
    dates = _expected_dates()
    if not dates:
        pytest.skip("no dates in short range")
    bad_date = dates[0]
    lroot = _build_lake(tmp_path, dates=dates[1:], ohlcv=True, definitions=True, statistics=True)
    bad_file = lroot / "options" / "fixing_mbo" / f"ES_fixing_{bad_date}.dbn.zst"
    bad_file.write_bytes(b"not-a-dbn" * 600)

    result = dd.options_lane_checks(lroot, today=_SHORT_TODAY, start=_SHORT_START)

    assert result is not None
    assert result["fixing_mbo"]["invalid_files"] == 1
    assert result["expiry_coverage"]["gap_count"] >= 1


def test_tiny_corrupt_dbn_without_sidecar_is_rejected(tmp_path: Path) -> None:
    dd = _load_dd()
    dates = _expected_dates()
    if not dates:
        pytest.skip("no dates in short range")
    bad_date = dates[0]
    lroot = _build_lake(tmp_path, dates=dates[1:], ohlcv=True, definitions=True, statistics=True)
    bad_file = lroot / "options" / "fixing_mbo" / f"ES_fixing_{bad_date}.dbn.zst"
    bad_file.write_bytes(b"tiny-not-a-dbn")

    result = dd.options_lane_checks(lroot, today=_SHORT_TODAY, start=_SHORT_START)

    assert result is not None
    assert result["fixing_mbo"]["invalid_files"] == 1
    assert result["expiry_coverage"]["gap_count"] >= 1


def test_matching_sidecar_does_not_make_corrupt_dbn_valid(tmp_path: Path) -> None:
    dd = _load_dd()
    dates = _expected_dates()
    if not dates:
        pytest.skip("no dates in short range")
    bad_date = dates[0]
    lroot = _build_lake(tmp_path, dates=dates[1:], ohlcv=True, definitions=True, statistics=True)
    bad_file = lroot / "options" / "fixing_mbo" / f"ES_fixing_{bad_date}.dbn.zst"
    bad_file.write_bytes(b"dummy")
    blob = bad_file.read_bytes()
    bad_file.with_name(f"{bad_file.name}.doctor.json").write_text(
        json.dumps(
            {
                "valid": True,
                "schema": "mbo",
                "record_count": 1,
                "size_bytes": len(blob),
                "sha256": hashlib.sha256(blob).hexdigest(),
            }
        ),
        encoding="utf-8",
    )

    result = dd.options_lane_checks(lroot, today=_SHORT_TODAY, start=_SHORT_START)

    assert result is not None
    assert result["fixing_mbo"]["invalid_files"] == 1
    assert result["expiry_coverage"]["gap_count"] >= 1


# ---------------------------------------------------------------------------
# Test 4: remove most recent expected date (within 5-day grace) -> FAIL with vendor-lag reason
# ---------------------------------------------------------------------------

def test_recent_gap_is_fail_not_warn(tmp_path: Path) -> None:
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
    assert cov["status"] == "FAIL", f"expected FAIL but got {cov['status']}: {cov['detail']}"

    # The gap must appear in summary
    assert result["expiry_coverage"]["gap_count"] >= 1
    assert result["expiry_coverage"]["stale_gap_count"] == 0
    [diag] = [
        row for row in result["expiry_coverage"]["gap_diagnostics"]
        if row["date"] == drop_date
    ]
    assert diag["status"] == "FAIL"
    assert diag["reason"] == "missing_artifact_vendor_lag"
    assert diag["stale"] is False
    assert diag["invalid_artifacts"] == []
    assert diag["required_action"] == "backfill_or_manifest_vendor_no_data_proof"
    assert diag["retry_window"]["date"] == drop_date


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
# Test 6: monkeypatch OPTIONS_FIXING_COVERED_ELSEWHERE does not cover a missing date without proof
# ---------------------------------------------------------------------------

def test_covered_elsewhere_without_manifest_does_not_clear_gap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert by_name["options-fixing-coverage"]["status"] in {"WARN", "FAIL"}
    assert result["expiry_coverage"]["gap_count"] >= 1


def test_manifest_backed_covered_elsewhere_clears_gap(tmp_path: Path) -> None:
    dd = _load_dd()
    dates = _expected_dates()
    if not dates:
        pytest.skip("no dates in short range")
    missing_date = dates[0]
    present_dates = [d for d in dates if d != missing_date]

    lroot = _build_lake(tmp_path, dates=present_dates, ohlcv=True, definitions=True, statistics=True)
    opt = lroot / "options"
    alt = opt / "alternate" / f"ES_fixing_{missing_date}.dbn.zst"
    alt.parent.mkdir(parents=True, exist_ok=True)
    _write_dummy_dbn(alt, "mbo")
    (opt / "coverage_manifest.json").write_text(
        json.dumps(
            {
                "covered_elsewhere": [
                    {
                        "date": missing_date,
                        "dataset": "fixing_mbo",
                        "schema": "mbo",
                        "start_utc": f"{missing_date}T19:55:00Z",
                        "end_utc": f"{missing_date}T20:05:00Z",
                        "path": f"alternate/{alt.name}",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = dd.options_lane_checks(lroot, today=_SHORT_TODAY, start=_SHORT_START)

    assert result is not None
    assert result["expiry_coverage"]["gap_count"] == 0
    assert result["expiry_coverage"]["covered_elsewhere"] == [missing_date]
    assert result["expiry_coverage"]["invalid_covered_elsewhere"] == []


def test_npz_manifest_backed_prop_flatten_clears_gap(tmp_path: Path) -> None:
    dd = _load_dd()
    dates = _expected_dates()
    if not dates:
        pytest.skip("no dates in short range")
    missing_date = dates[0]
    present_dates = [d for d in dates if d != missing_date]

    lroot = _build_lake(tmp_path, dates=present_dates, ohlcv=True, definitions=True, statistics=True)
    npz_root = lroot / "npz"
    npz_root.mkdir()
    event_date = missing_date.replace("-", "_")
    npz = npz_root / f"ES.v.0_PROP_FLATTEN_TOPSTEP_{event_date}_MAIN_mbo.npz"
    npz.write_bytes(b"npz-proof")
    (npz_root / "manifest.json").write_text(
        json.dumps(
            [
                {
                    "event_id": f"PROP_FLATTEN_TOPSTEP_{event_date}_MAIN",
                    "symbol": "ES.v.0",
                    "npz_path": str(npz),
                    "event_count": 7,
                }
            ]
        ),
        encoding="utf-8",
    )

    result = dd.options_lane_checks(lroot, today=_SHORT_TODAY, start=_SHORT_START)

    assert result is not None
    assert result["expiry_coverage"]["gap_count"] == 0
    assert result["expiry_coverage"]["covered_elsewhere"] == [missing_date]
    [proof] = result["expiry_coverage"]["covered_elsewhere_manifest"]
    assert proof["source"] == "active_npz_manifest"
    assert proof["schema"] == "npz_mbo"


def test_invalid_npz_manifest_proof_marks_gap_invalid(tmp_path: Path) -> None:
    dd = _load_dd()
    dates = _expected_dates()
    if not dates:
        pytest.skip("no dates in short range")
    missing_date = dates[0]
    present_dates = [d for d in dates if d != missing_date]

    lroot = _build_lake(tmp_path, dates=present_dates, ohlcv=True, definitions=True, statistics=True)
    npz_root = lroot / "npz"
    npz_root.mkdir()
    event_date = missing_date.replace("-", "_")
    npz = npz_root / f"ES.v.0_PROP_FLATTEN_TOPSTEP_{event_date}_MAIN_mbo.npz"
    (npz_root / "manifest.json").write_text(
        json.dumps(
            [
                {
                    "event_id": f"PROP_FLATTEN_TOPSTEP_{event_date}_MAIN",
                    "symbol": "ES.v.0",
                    "npz_path": str(npz),
                    "event_count": 7,
                }
            ]
        ),
        encoding="utf-8",
    )

    result = dd.options_lane_checks(lroot, today=_SHORT_TODAY, start=_SHORT_START)

    assert result is not None
    assert result["expiry_coverage"]["gap_count"] >= 1
    assert result["expiry_coverage"]["covered_elsewhere"] == []
    [invalid] = result["expiry_coverage"]["invalid_covered_elsewhere"]
    assert invalid["source"] == "active_npz_manifest"
    assert invalid["date"] == missing_date
    assert invalid["path"].endswith("_mbo.npz")
    [diag] = [
        row for row in result["expiry_coverage"]["gap_diagnostics"]
        if row["date"] == missing_date
    ]
    assert diag["status"] == "FAIL"
    assert diag["reason"] == "invalid_artifact"
    assert diag["required_action"] == "replace_invalid_artifact_or_manifest_no_data_proof"
    assert diag["invalid_artifacts"] == [invalid]


def test_manifest_proof_txt_does_not_clear_fixing_gap(tmp_path: Path) -> None:
    dd = _load_dd()
    dates = _expected_dates()
    if not dates:
        pytest.skip("no dates in short range")
    missing_date = dates[0]
    present_dates = [d for d in dates if d != missing_date]

    lroot = _build_lake(tmp_path, dates=present_dates, ohlcv=True, definitions=True, statistics=True)
    opt = lroot / "options"
    proof = opt / "alternate" / "proof.txt"
    proof.parent.mkdir(parents=True, exist_ok=True)
    proof.write_text("manual vendor note", encoding="utf-8")
    (opt / "coverage_manifest.json").write_text(
        json.dumps(
            {
                "covered_elsewhere": [
                    {
                        "date": missing_date,
                        "dataset": "fixing_mbo",
                        "schema": "mbo",
                        "start_utc": f"{missing_date}T19:55:00Z",
                        "end_utc": f"{missing_date}T20:05:00Z",
                        "path": f"alternate/{proof.name}",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = dd.options_lane_checks(lroot, today=_SHORT_TODAY, start=_SHORT_START)

    assert result is not None
    assert result["expiry_coverage"]["gap_count"] >= 1
    assert result["expiry_coverage"]["covered_elsewhere"] == []
    [invalid] = result["expiry_coverage"]["invalid_covered_elsewhere"]
    assert invalid["source"] == "coverage_manifest"
    assert invalid["date"] == missing_date
    assert invalid["path"].endswith("proof.txt")
    [diag] = [
        row for row in result["expiry_coverage"]["gap_diagnostics"]
        if row["date"] == missing_date
    ]
    assert diag["status"] == "FAIL"
    assert diag["reason"] == "invalid_artifact"
    assert diag["required_action"] == "replace_invalid_artifact_or_manifest_no_data_proof"
    assert diag["invalid_artifacts"] == [invalid]


def test_statistics_job_status_json_is_not_counted(tmp_path: Path) -> None:
    dd = _load_dd()
    dates = _expected_dates()
    lroot = _build_lake(tmp_path, dates=dates, ohlcv=True, definitions=True, statistics=False)
    stats = lroot / "options" / "statistics" / "JOBX"
    stats.mkdir(parents=True, exist_ok=True)
    (stats / "job_status.json").write_text(
        json.dumps({"status": "delivered", "files": 1}),
        encoding="utf-8",
    )

    result = dd.options_lane_checks(lroot, today=_SHORT_TODAY, start=_SHORT_START)

    assert result is not None
    assert result["statistics"]["files"] == 0
    assert result["statistics"]["invalid_files"]
    by_name = {c["name"]: c for c in dd.checks}
    assert by_name["options-statistics"]["status"] == "FAIL"


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
    for k in (
        "quote_files", "trades_files", "dates_covered", "study_dates_covered",
        "trade_only_date_list", "invalid_file_details", "first_date", "last_date",
    ):
        assert k in fm, f"missing key fixing_mbo.{k}"

    # expiry_coverage sub-keys
    ec = result["expiry_coverage"]
    for k in (
        "coverage_mode", "expected_dates", "dates_covered", "covered_elsewhere",
        "gaps", "gap_count", "gap_dates", "stale_gap_count", "stale_gap_dates",
        "gap_diagnostics", "gap_request_windows", "strict_mbo_gap_count",
        "strict_mbo_gap_dates", "grace_days", "calendar",
    ):
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
