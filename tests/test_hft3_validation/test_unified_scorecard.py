"""Tests for the lane-aware scorecard."""
from __future__ import annotations

from hft3.validation.lanes import Lane
from hft3.validation.lanes.lane_registry import LaneRegistration, LaneRegistry
from hft3.validation.lanes.scorecard import (
    build_lane_scorecard,
    legacy_cme_scorecard_fields,
)


def test_scorecard_contains_all_four_lanes():
    card = build_lane_scorecard()
    assert "cme_futures" in card.covered_lanes
    assert "crypto" in card.covered_lanes
    assert "equities" in card.covered_lanes
    assert "options" in card.covered_lanes


def test_scorecard_per_lane_coverage_fields():
    card = build_lane_scorecard()
    for lane_value in ("cme_futures", "crypto", "equities", "options"):
        cov = card.lane_coverage[lane_value]
        assert "symbols" in cov
        assert "event_types" in cov
        assert "latency_bands_ms" in cov
        assert "test_paths" in cov
        assert "capability_profile" in cov


def test_scorecard_cme_symbols():
    card = build_lane_scorecard()
    cme = card.lane_coverage["cme_futures"]
    assert "ES" in cme["symbols"]
    assert "MES" in cme["symbols"]


def test_scorecard_crypto_environment_coverage():
    card = build_lane_scorecard()
    crypto = card.lane_coverage["crypto"]
    assert crypto["symbols"] == []
    assert crypto["instrument_coverage"] == "candidate_config"
    assert crypto["environment_validated"] is False
    assert crypto["environment_source_ref"] == ""
    assert crypto["capability_profile"]["node_direct"] is True


def test_scorecard_equities_symbols():
    card = build_lane_scorecard()
    equities = card.lane_coverage["equities"]
    assert "RUNNER" in equities["symbols"]
    assert equities["capability_profile"]["is_hft"] is False
    assert equities["capability_profile"]["dma"] is False


def test_scorecard_event_types_per_lane():
    card = build_lane_scorecard()
    assert "macro" in card.lane_coverage["cme_futures"]["event_types"]
    assert "synthetic" not in card.lane_coverage["cme_futures"]["event_types"]
    assert "crypto_l2" in card.lane_coverage["crypto"]["event_types"]
    assert "equities_low_float" in card.lane_coverage["equities"]["event_types"]
    assert "options_parity" in card.lane_coverage["options"]["event_types"]


def test_scorecard_to_dict_round_trip():
    card = build_lane_scorecard(git_sha="abc123", timestamp_utc="2026-06-03T00:00:00Z")
    d = card.to_dict()
    assert d["git_sha"] == "abc123"
    assert d["timestamp_utc"] == "2026-06-03T00:00:00Z"
    assert d["schema_version"] == 1
    assert len(d["covered_lanes"]) == 4


def _build_scorecard_with_fake_cme_loader(config_loader):
    LaneRegistry.reset()
    LaneRegistry.instance().register(
        LaneRegistration(
            lane=Lane.CME_FUTURES,
            adapter_factory=lambda: object(),
            config_loader=config_loader,
            validator=lambda: object(),
            test_paths=["tests/test_fake_cme_lane"],
        )
    )
    try:
        return build_lane_scorecard(auto_register=False)
    finally:
        LaneRegistry.reset()


def test_scorecard_marks_config_loader_exception_blocking():
    def _raise_config_error():
        raise RuntimeError("config exploded")

    card = _build_scorecard_with_fake_cme_loader(_raise_config_error)

    cme = card.lane_coverage["cme_futures"]
    assert cme["test_paths"] == ["tests/test_fake_cme_lane"]
    assert cme["coverage_status"] == "CONFIG_LOAD_FAILED"
    assert cme["blocking"] is True
    assert "RuntimeError: config exploded" in cme["failure_reasons"]
    assert cme["config_loader_error"] == {
        "type": "RuntimeError",
        "message": "config exploded",
    }


def test_scorecard_none_config_loader_keeps_empty_non_error_coverage():
    card = _build_scorecard_with_fake_cme_loader(lambda: None)

    cme = card.lane_coverage["cme_futures"]
    assert cme["symbols"] == []
    assert cme["event_types"] == []
    assert cme["latency_bands_ms"] == []
    assert cme["test_paths"] == ["tests/test_fake_cme_lane"]
    assert "coverage_status" not in cme
    assert "blocking" not in cme
    assert "failure_reasons" not in cme
    assert "config_loader_error" not in cme


def test_legacy_cme_fields_extracted():
    card = build_lane_scorecard()
    legacy = legacy_cme_scorecard_fields(card)
    assert "covered_symbols" in legacy
    assert "covered_event_types" in legacy
    assert "covered_latency_bands" in legacy
    assert "covered_queue_models" in legacy
    assert "covered_modules" in legacy
    assert "covered_execution_modes" in legacy
    assert "ES" in legacy["covered_symbols"]
    assert "macro" in legacy["covered_event_types"]
