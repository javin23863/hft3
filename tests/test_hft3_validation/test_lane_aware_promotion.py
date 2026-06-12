"""Tests for the lane-aware promotion coverage check."""
from __future__ import annotations

from hft3.validation.lanes import Lane
from hft3.validation.lanes.lane_aware_promotion import (
    check_candidate_lane_coverage,
    check_lane_coverage,
    resolve_lane_for_candidate,
)
from hft3.validation.lanes.scorecard import build_lane_scorecard


def test_resolve_lane_options_symbol_resolves_to_equities():
    assert resolve_lane_for_candidate(symbol="OPTIONS_LEG_A") == Lane.EQUITIES


def test_resolve_lane_parity_symbol_resolves_to_equities():
    assert resolve_lane_for_candidate(symbol="PARITY_SPREAD") == Lane.EQUITIES


def test_resolve_lane_cme_default():
    assert resolve_lane_for_candidate(model_id="HYP_1", symbol="ES", event_id="CPI_2024") == Lane.CME_FUTURES


def test_resolve_lane_options_event_id_resolves_to_equities():
    assert resolve_lane_for_candidate(event_id="OPTIONS_PARITY_RUN") == Lane.EQUITIES


def test_resolve_lane_parity_event_id_resolves_to_equities():
    assert resolve_lane_for_candidate(event_id="PARITY_LEG_RUN") == Lane.EQUITIES


def test_equities_options_candidate_passes_coverage():
    result = check_candidate_lane_coverage(
        model_id="OPTIONS_PUT_CALL",
        symbol="OPTIONS_LEG_A",
        event_id="OPTIONS_PARITY_v1",
    )
    assert result.passed is True
    assert result.lane == "equities"


def test_equities_parity_candidate_passes_coverage():
    result = check_candidate_lane_coverage(
        model_id="PARITY_LEG",
        symbol="PARITY_SPREAD",
        event_id="PARITY_RUN_v1",
    )
    assert result.passed is True
    assert result.lane == "equities"


def test_cme_candidate_with_es_passes_coverage():
    result = check_candidate_lane_coverage(
        model_id="HYP_1",
        symbol="ES",
        event_id="CPI_2024_09_11_TIGHT",
    )
    assert result.passed is True
    assert result.lane == "cme_futures"


def test_cme_candidate_with_macro_event_passes():
    result = check_candidate_lane_coverage(
        symbol="MES",
        event_id="NFP_2024_01_05_TIGHT",
    )
    assert result.passed is True


def test_cross_lane_foreign_symbol_against_cme_lane_fails():
    result = check_lane_coverage(
        Lane.CME_FUTURES,
        symbol="DOGEUSDT",
        event_id="CPI_2024_09_11_TIGHT",
    )
    assert result.passed is False
    assert "DOGEUSDT" in result.failure_reasons[0]


# --- CME exact-band latency tests (unchanged semantics) ---


def test_check_lane_coverage_latency_band_cme_1ms_passes():
    result = check_lane_coverage(Lane.CME_FUTURES, symbol="ES", latency_ms=1.0)
    assert result.passed is True


def test_check_lane_coverage_latency_band_cme_50ms_fails():
    result = check_lane_coverage(Lane.CME_FUTURES, symbol="ES", latency_ms=50.0)
    assert result.passed is False


# --- Options/parity lane floor-semantics latency tests ---


def test_equities_coverage_latency_50ms_passes():
    """50ms is above floor (5ms); passes regardless of bands."""
    result = check_lane_coverage(Lane.EQUITIES, symbol="OPTIONS", latency_ms=50.0)
    assert result.passed is True


def test_equities_coverage_latency_1ms_fails_optimistic_claim():
    """1ms is below floor (5ms); should fail with 'optimistic' reason."""
    result = check_lane_coverage(Lane.EQUITIES, symbol="OPTIONS", latency_ms=1.0)
    assert result.passed is False
    assert any("optimistic" in r for r in result.failure_reasons)


def test_equities_coverage_latency_at_floor_passes():
    """Exactly at floor passes."""
    result = check_lane_coverage(Lane.EQUITIES, symbol="OPTIONS", latency_ms=5.0)
    assert result.passed is True


def test_equities_coverage_latency_above_all_bands_passes():
    """999ms is well above floor; floor semantics mean it still passes."""
    result = check_lane_coverage(Lane.EQUITIES, symbol="OPTIONS", latency_ms=999.0)
    assert result.passed is True


# --- Capability profile tests ---


def test_cme_lane_requires_true_hft_dma_profile():
    scorecard = build_lane_scorecard().to_dict()
    scorecard["lane_coverage"]["cme_futures"]["capability_profile"]["dma"] = False
    result = check_lane_coverage(Lane.CME_FUTURES, symbol="ES", scorecard=scorecard)
    assert result.passed is False
    assert "CME lane requires true HFT/DMA" in result.failure_reasons[0]


def test_equities_lane_is_not_blocked_for_lacking_dma_hft():
    result = check_lane_coverage(Lane.EQUITIES, symbol="OPTIONS", event_id="OPTIONS_PARITY_v1")
    assert result.passed is True
    assert result.capability_profile["is_hft"] is False
    assert result.capability_profile["dma"] is False
