"""Tests for the lane-aware scorecard."""
from __future__ import annotations

from hft3.validation.lanes import Lane
from hft3.validation.lanes.scorecard import (
    build_lane_scorecard,
    legacy_cme_scorecard_fields,
)


def test_scorecard_contains_three_lanes():
    card = build_lane_scorecard()
    assert "cme_futures" in card.covered_lanes
    assert "crypto" in card.covered_lanes
    assert "equities" in card.covered_lanes
    assert "options" not in card.covered_lanes


def test_scorecard_per_lane_coverage_fields():
    card = build_lane_scorecard()
    for lane_value in ("cme_futures", "crypto", "equities"):
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


def test_scorecard_equities_merged_symbols():
    card = build_lane_scorecard()
    equities = card.lane_coverage["equities"]
    assert "OPTIONS" in equities["symbols"]
    assert "PARITY" in equities["symbols"]


def test_scorecard_equities_latency_floor():
    card = build_lane_scorecard()
    equities = card.lane_coverage["equities"]
    assert "latency_floor_ms" in equities
    assert equities["latency_floor_ms"] == 5.0


def test_scorecard_event_types_per_lane():
    card = build_lane_scorecard()
    assert "macro" in card.lane_coverage["cme_futures"]["event_types"]
    assert "synthetic" in card.lane_coverage["cme_futures"]["event_types"]
    assert "crypto_l2" in card.lane_coverage["crypto"]["event_types"]
    assert "equities_low_float" in card.lane_coverage["equities"]["event_types"]
    assert "options_parity" in card.lane_coverage["equities"]["event_types"]


def test_scorecard_to_dict_round_trip():
    card = build_lane_scorecard(git_sha="abc123", timestamp_utc="2026-06-03T00:00:00Z")
    d = card.to_dict()
    assert d["git_sha"] == "abc123"
    assert d["timestamp_utc"] == "2026-06-03T00:00:00Z"
    assert d["schema_version"] == 1
    assert len(d["covered_lanes"]) == 3


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
