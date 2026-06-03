"""Tests for the lane-aware backtester certification integration (Phase 41)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hft3.validation.certification_runner import run_full_certification


@pytest.fixture(scope="module")
def certification_result(tmp_path_factory):
    """Run the lane-aware certification once for the test module.

    Marked as slow; skips if the crypto_lane or equities_lane test
    directories are not present (e.g. partial checkout).
    """
    if not Path("tests/test_crypto_lane").exists():
        pytest.skip("tests/test_crypto_lane not present")
    if not Path("tests/test_equities_lane").exists():
        pytest.skip("tests/test_equities_lane not present")
    if not Path("tests/backtester_validation/fast").exists():
        pytest.skip("tests/backtester_validation/fast not present")
    return run_full_certification(Path("."))


def test_certification_status_includes_lane_results(certification_result):
    assert certification_result.status in {"GREEN", "YELLOW", "RED"}
    assert "cme_futures" in certification_result.lane_results
    assert "crypto" in certification_result.lane_results
    assert "equities" in certification_result.lane_results
    assert "options" in certification_result.lane_results


def test_cme_lane_marked_covered_by_t0_t2(certification_result):
    cme = certification_result.lane_results["cme_futures"]
    assert cme.get("covered_by") is not None
    assert "T0" in cme["covered_by"]
    assert "T2" in cme["covered_by"]
    assert cme.get("test_paths") == [
        "tests/backtester_validation/fast",
        "tests/backtester_validation/full",
    ]


def test_crypto_lane_ran_pytest(certification_result):
    crypto = certification_result.lane_results["crypto"]
    assert crypto.get("passed") is True
    assert crypto.get("returncode") == 0
    assert "tests/test_crypto_lane" in crypto.get("test_paths", [])


def test_equities_lane_ran_pytest(certification_result):
    equities = certification_result.lane_results["equities"]
    assert equities.get("passed") is True
    assert equities.get("returncode") == 0
    assert "tests/test_equities_lane" in equities.get("test_paths", [])


def test_options_lane_handled(certification_result):
    options = certification_result.lane_results["options"]
    assert options.get("passed") in {True, None}
    if options.get("passed") is None and options.get("test_paths") is None:
        pass
    else:
        assert options.get("test_paths") == ["tests/test_options_lane"]


def test_scorecard_json_persisted(certification_result):
    p = Path(certification_result.scorecard_json)
    assert p.is_file()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["status"] in {"GREEN", "YELLOW", "RED"}
    assert "lane_results" in data
    assert "lane_failures" in data
    assert data["tier"] == "T2"


def test_scorecard_md_persisted(certification_result):
    p = Path(certification_result.scorecard_md)
    assert p.is_file()
    text = p.read_text(encoding="utf-8")
    assert "Backtester Certification Scorecard" in text
    assert "T0 Fast Gate" in text
    assert "T2 Full Suite" in text
    assert "Lane-aware Certification" in text


def test_coverage_union_across_lanes(certification_result):
    p = Path(certification_result.scorecard_json)
    data = json.loads(p.read_text(encoding="utf-8"))
    symbols = data["covered_symbols"]
    event_types = data["covered_event_types"]
    modules = data["covered_modules"]
    bands = data["covered_latency_bands"]
    assert "ES" in symbols
    assert "RUNNER" in symbols
    assert "macro" in event_types
    assert "crypto_l2" in event_types
    assert "equities_low_float" in event_types
    assert "backtest_pipeline" in modules
    assert "crypto_lane" in modules
    assert "equities_lane" in modules
    assert 0.5 in bands
    assert 50.0 in bands
    assert 200.0 in bands
    assert data["lane_coverage"]["crypto"]["instrument_coverage"] == "candidate_config"
    assert data["lane_coverage"]["crypto"]["environment_validated"] is False


def test_lane_aware_payload_omitted_when_skip_lane_pytest(tmp_path):
    """With skip_lane_pytest=True, lane_results is empty and CME is not auto-marked."""
    result = run_full_certification(Path("."), skip_lane_pytest=True)
    assert result.lane_results == {}
    assert result.lane_failures == []
