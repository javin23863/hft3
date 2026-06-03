"""Tests for the lane-aware promotion coverage check."""
from __future__ import annotations

from hft3.validation.lanes import Lane
from hft3.validation.lanes.lane_aware_promotion import (
    check_candidate_lane_coverage,
    check_lane_coverage,
    resolve_lane_for_candidate,
)
from hft3.validation.lanes.scorecard import build_lane_scorecard


def _crypto_environment_scorecard() -> dict:
    scorecard = build_lane_scorecard().to_dict()
    crypto = scorecard["lane_coverage"]["crypto"]
    crypto["symbols"] = []
    crypto["data_environment"] = "btc_computer_validated_crypto_data_environment"
    crypto["instrument_coverage"] = "validated_crypto_data_environment"
    crypto["environment_validated"] = True
    crypto["environment_source_ref"] = "config://btc-computer/crypto-data-environment.yaml"
    return scorecard


def test_resolve_lane_crypto_via_model_id():
    assert resolve_lane_for_candidate(model_id="CRYPTO_H7") == Lane.CRYPTO


def test_resolve_lane_does_not_infer_crypto_from_symbol():
    assert resolve_lane_for_candidate(symbol="DOGEUSDT") == Lane.CME_FUTURES
    assert resolve_lane_for_candidate(symbol="WIF/USDT") == Lane.CME_FUTURES


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


def test_crypto_candidate_with_environment_covered_meme_symbol_passes():
    scorecard = _crypto_environment_scorecard()
    result = check_candidate_lane_coverage(
        model_id="CRYPTO_H1",
        symbol="DOGEUSDT",
        event_id="CRYPTO_L2_test",
        scorecard=scorecard,
    )
    assert result.passed is True
    assert result.lane == "crypto"
    assert result.capability_profile["node_direct"] is True


def test_crypto_candidate_with_configured_symbol_passes_coverage():
    scorecard = build_lane_scorecard().to_dict()
    scorecard["lane_coverage"]["crypto"]["symbols"] = ["WIF/USDT"]
    result = check_candidate_lane_coverage(
        model_id="CRYPTO_H3",
        symbol="WIF/USDT",
        event_id="CRYPTO_L3_foo",
        scorecard=scorecard,
    )
    assert result.passed is True


def test_crypto_candidate_without_environment_coverage_fails():
    scorecard = build_lane_scorecard().to_dict()
    scorecard["lane_coverage"]["crypto"]["symbols"] = []
    result = check_candidate_lane_coverage(
        model_id="CRYPTO_H1",
        symbol="UNKNOWNCOIN",
        event_id="CRYPTO_L2_test",
        scorecard=scorecard,
    )
    assert result.passed is False
    assert "UNKNOWNCOIN" in result.failure_reasons[0]


def test_crypto_environment_coverage_requires_trusted_provenance():
    scorecard = _crypto_environment_scorecard()
    scorecard["lane_coverage"]["crypto"]["environment_source_ref"] = "candidate://self-attested.yaml"
    result = check_candidate_lane_coverage(
        model_id="CRYPTO_H1",
        symbol="DOGEUSDT",
        event_id="CRYPTO_L2_test",
        scorecard=scorecard,
    )
    assert result.passed is False
    assert "DOGEUSDT" in result.failure_reasons[0]


def test_crypto_environment_coverage_requires_expected_data_environment():
    scorecard = _crypto_environment_scorecard()
    scorecard["lane_coverage"]["crypto"]["data_environment"] = "candidate_claimed_environment"
    result = check_candidate_lane_coverage(
        model_id="CRYPTO_H1",
        symbol="DOGEUSDT",
        event_id="CRYPTO_L2_test",
        scorecard=scorecard,
    )
    assert result.passed is False
    assert "DOGEUSDT" in result.failure_reasons[0]


def test_crypto_candidate_not_in_configured_environment_fails():
    scorecard = build_lane_scorecard().to_dict()
    scorecard["lane_coverage"]["crypto"]["symbols"] = ["WIF/USDT"]
    result = check_candidate_lane_coverage(
        model_id="CRYPTO_H1",
        symbol="UNKNOWNCOIN",
        event_id="CRYPTO_L2_test",
        scorecard=scorecard,
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
        symbol="DOGEUSDT",
        event_id="CPI_2024_09_11_TIGHT",
    )
    assert result.passed is False
    assert "DOGEUSDT" in result.failure_reasons[0]


def test_check_lane_coverage_latency_band():
    result = check_lane_coverage(Lane.CME_FUTURES, symbol="ES", latency_ms=1.0)
    assert result.passed is True


def test_check_lane_coverage_latency_band_out_of_range():
    result = check_lane_coverage(Lane.CME_FUTURES, symbol="ES", latency_ms=50.0)
    assert result.passed is False


def test_crypto_coverage_with_50ms_latency_passes():
    result = check_lane_coverage(
        Lane.CRYPTO,
        symbol="DOGEUSDT",
        latency_ms=50.0,
        scorecard=_crypto_environment_scorecard(),
    )
    assert result.passed is True


def test_cme_lane_requires_true_hft_dma_profile():
    scorecard = build_lane_scorecard().to_dict()
    scorecard["lane_coverage"]["cme_futures"]["capability_profile"]["dma"] = False
    result = check_lane_coverage(Lane.CME_FUTURES, symbol="ES", scorecard=scorecard)
    assert result.passed is False
    assert "CME lane requires true HFT/DMA" in result.failure_reasons[0]


def test_crypto_lane_requires_node_direct_hft_profile():
    scorecard = _crypto_environment_scorecard()
    scorecard["lane_coverage"]["crypto"]["capability_profile"]["node_direct"] = False
    result = check_lane_coverage(Lane.CRYPTO, symbol="DOGEUSDT", scorecard=scorecard)
    assert result.passed is False
    assert "crypto lane requires node-direct HFT" in result.failure_reasons[0]


def test_equities_lane_is_not_blocked_for_lacking_dma_hft():
    result = check_lane_coverage(Lane.EQUITIES, symbol="RUNNER", event_id="EQUITIES_LOW_FLOAT_v1")
    assert result.passed is True
    assert result.capability_profile["is_hft"] is False
    assert result.capability_profile["dma"] is False
