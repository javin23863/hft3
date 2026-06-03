"""Tests for the unified certification runner and unified staleness paths."""
from __future__ import annotations

from hft3.validation.lanes.lane import Lane
from hft3.validation.lanes.lane_registry import LaneRegistry
from hft3.validation.lanes.registration import register_all_lanes
from hft3.validation.lanes.unified_certification_runner import (
    LaneRunResult,
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
        assert run_result["passed"] is True
        assert run_result["returncode"] == 0


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
