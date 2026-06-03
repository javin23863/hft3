"""Tests for the lane-aware promotion coverage check."""
from __future__ import annotations

from hft3.validation.lanes import Lane
from hft3.validation.lanes.lane_aware_promotion import (
    check_candidate_lane_coverage,
    check_lane_coverage,
    resolve_lane_for_candidate,
)


def test_resolve_lane_crypto_via_model_id():
    assert resolve_lane_for_candidate(model_id="CRYPTO_H7") == Lane.CRYPTO


def test_resolve_lane_crypto_via_symbol():
    assert resolve_lane_for_candidate(symbol="BTCUSDT") == Lane.CRYPTO
    assert resolve_lane_for_candidate(symbol="ETH/USDT") == Lane.CRYPTO


def test_resolve_lane_equities_via_symbol():
    assert resolve_lane_for_candidate(symbol="RUNNER") == Lane.EQUITIES
    assert resolve_lane_for_candidate(symbol="LOW_FLOAT_X") == Lane.EQUITIES


def test_resolve_lane_options_via_symbol():
    assert resolve_lane_for_candidate(symbol="OPTIONS_LEG_A") == Lane.OPTIONS


def test_resolve_lane_cme_default():
    assert resolve_lane_for_candidate(model_id="HYP_1", symbol="ES", event_id="CPI_2024") == Lane.CME_FUTURES


def test_resolve_lane_via_event_id():
    assert resolve_lane_for_candidate(event_id="CRYPTO_H7_RUN") == Lane.CRYPTO
    assert resolve_lane_for_candidate(event_id="EQUITY_RUNNER_2024") == Lane.EQUITIES


def test_crypto_candidate_with_btcusdt_passes_coverage():
    result = check_candidate_lane_coverage(
        model_id="CRYPTO_H1",
        symbol="BTCUSDT",
        event_id="CRYPTO_L2_test",
    )
    assert result.passed is True
    assert result.lane == "crypto"


def test_crypto_candidate_with_ethusdt_passes_coverage():
    result = check_candidate_lane_coverage(
        model_id="CRYPTO_H3",
        symbol="ETHUSDT",
        event_id="CRYPTO_L3_foo",
    )
    assert result.passed is True


def test_crypto_candidate_with_unknown_symbol_fails():
    result = check_candidate_lane_coverage(
        model_id="CRYPTO_H1",
        symbol="UNKNOWNCOIN",
        event_id="CRYPTO_L2_test",
    )
    assert result.passed is False
    assert "UNKNOWNCOIN" in result.failure_reasons[0]


def test_equities_candidate_with_runner_passes_coverage():
    result = check_candidate_lane_coverage(
        model_id="LOW_FLOAT_BREAKOUT",
        symbol="RUNNER",
        event_id="EQUITIES_LOW_FLOAT_v1",
    )
    assert result.passed is True
    assert result.lane == "equities"


def test_equities_candidate_with_testco_passes_coverage():
    result = check_candidate_lane_coverage(
        model_id="EQUITY_LOWFLOAT",
        symbol="RUNNER",
        event_id="EQUITIES_RUNNER_EVENT_test",
    )
    assert result.passed is True


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


def test_cross_lane_crypto_symbol_against_cme_lane_fails():
    result = check_lane_coverage(
        Lane.CME_FUTURES,
        symbol="BTCUSDT",
        event_id="CPI_2024_09_11_TIGHT",
    )
    assert result.passed is False
    assert "BTCUSDT" in result.failure_reasons[0]


def test_check_lane_coverage_latency_band():
    result = check_lane_coverage(Lane.CME_FUTURES, symbol="ES", latency_ms=1.0)
    assert result.passed is True


def test_check_lane_coverage_latency_band_out_of_range():
    result = check_lane_coverage(Lane.CME_FUTURES, symbol="ES", latency_ms=50.0)
    assert result.passed is False


def test_crypto_coverage_with_50ms_latency_passes():
    result = check_lane_coverage(Lane.CRYPTO, symbol="BTCUSDT", latency_ms=50.0)
    assert result.passed is True
