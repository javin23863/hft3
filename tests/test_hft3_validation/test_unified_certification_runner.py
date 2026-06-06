"""Tests for the unified certification runner and unified staleness paths."""
from __future__ import annotations

from pathlib import Path

from hft3.validation.lanes.lane import Lane
from hft3.validation.lanes.lane_registry import LaneRegistry
from hft3.validation.lanes.registration import register_all_lanes
from hft3.validation.lanes.unified_certification_runner import (
    LaneRunResult,
    _run_pytest_for_lane,
    run_unified_certification,
    write_unified_certification_report,
)
from hft3.validation.lanes.unified_staleness import (
    DEFAULT_LANE_PATHS,
    all_critical_paths,
    get_lane_staleness_paths,
)


def test_unified_certification_runs_all_lanes():
    card = run_unified_certification(skip_pytest=True)
    assert len(card.covered_lanes) == 4
    for lane_value in ("cme_futures", "crypto", "equities", "options"):
        assert lane_value in card.lane_coverage
        run_result = card.lane_coverage[lane_value].get("run_result")
        assert run_result is not None
        assert run_result["passed"] is False
        assert run_result["returncode"] != 0
        assert run_result["status"] == "PYTEST_SKIPPED"


def test_unified_certification_runs_subset_of_lanes():
    card = run_unified_certification(skip_pytest=True, lanes=[Lane.CRYPTO, Lane.EQUITIES])
    assert "crypto" in card.covered_lanes
    assert "equities" in card.covered_lanes
    assert "crypto" in card.lane_coverage
    assert "equities" in card.lane_coverage
    assert card.lane_coverage["crypto"].get("run_result") is not None
    assert card.lane_coverage["equities"].get("run_result") is not None
    assert "run_result" not in card.lane_coverage["cme_futures"]
    assert "run_result" not in card.lane_coverage["options"]


def test_unified_certification_main_skip_pytest_is_not_success(tmp_path, monkeypatch):
    from hft3.validation.lanes import unified_certification_runner as runner

    monkeypatch.chdir(tmp_path)
    assert runner.main(["--skip-pytest"]) == 1


def test_write_unified_certification_report(tmp_path):
    card = run_unified_certification(skip_pytest=True)
    out = write_unified_certification_report(card, root=tmp_path)
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "lane_coverage" in text
    assert "legacy_cme_fields" in text


def test_lane_run_result_to_dict():
    r = LaneRunResult(lane="crypto", passed=True, returncode=0)
    d = r.to_dict()
    assert d["lane"] == "crypto"
    assert d["passed"] is True
    assert d["status"] == "PASSED"


def test_pytest_lane_with_no_test_paths_fails_closed(tmp_path: Path):
    result = _run_pytest_for_lane([], tmp_path)
    assert result.passed is False
    assert result.returncode != 0
    assert result.status == "CONFIG_MISSING"
    assert any("CONFIG_MISSING" in note for note in result.failure_notes)


def test_pytest_lane_with_missing_test_path_fails_closed(tmp_path: Path):
    result = _run_pytest_for_lane(["tests/not_present"], tmp_path)
    assert result.passed is False
    assert result.returncode != 0
    assert result.status == "TEST_PATH_MISSING"
    assert result.test_paths == ["tests/not_present"]
    assert any("TEST_PATH_MISSING" in note for note in result.failure_notes)


def test_pytest_lane_with_missing_and_passing_path_keeps_nonzero_returncode(tmp_path: Path):
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    passing = test_dir / "test_ok.py"
    passing.write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    result = _run_pytest_for_lane(["tests/not_present", "tests/test_ok.py"], tmp_path)

    assert result.passed is False
    assert result.returncode != 0
    assert result.status == "TEST_PATH_MISSING"


def test_staleness_paths_cover_all_lanes():
    paths = get_lane_staleness_paths()
    assert "cme_futures" in paths.paths_by_lane
    assert "crypto" in paths.paths_by_lane
    assert "equities" in paths.paths_by_lane
    assert "options" in paths.paths_by_lane


def test_staleness_crypto_includes_lane_packages():
    paths = get_lane_staleness_paths()
    crypto_paths = paths.paths_by_lane["crypto"]
    assert any("crypto_lane" in p for p in crypto_paths)


def test_staleness_equities_includes_lane_packages():
    paths = get_lane_staleness_paths()
    equities_paths = paths.paths_by_lane["equities"]
    assert any("equities_lane" in p for p in equities_paths)


def test_staleness_options_includes_lane_packages():
    paths = get_lane_staleness_paths()
    options_paths = paths.paths_by_lane["options"]
    assert any("options_lane" in p for p in options_paths)


def test_staleness_cme_keeps_legacy_paths():
    paths = get_lane_staleness_paths()
    cme_paths = paths.paths_by_lane["cme_futures"]
    assert "packages/hft3/validation" in cme_paths
    assert "scripts/run_event_replay.py" in cme_paths


def test_all_critical_paths_includes_all_lanes():
    all_paths = all_critical_paths()
    assert any("crypto_lane" in p for p in all_paths)
    assert any("equities_lane" in p for p in all_paths)
    assert any("options_lane" in p for p in all_paths)
    assert "scripts/run_event_replay.py" in all_paths
