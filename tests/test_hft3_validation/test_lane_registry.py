"""Tests for LaneRegistry and lane resolution."""
from __future__ import annotations

import pytest

from hft3.validation.lanes import Lane, LaneRegistry
from hft3.validation.lanes.lane_registry import LaneRegistration
from hft3.validation.lanes.registration import register_all_lanes


@pytest.fixture(autouse=True)
def _reset_registry():
    LaneRegistry.reset()
    register_all_lanes()
    yield
    LaneRegistry.reset()


def test_lane_enum_values():
    assert Lane.CME_FUTURES.value == "cme_futures"
    assert Lane.CRYPTO.value == "crypto"
    assert Lane.EQUITIES.value == "equities"
    assert Lane.OPTIONS.value == "options"


def test_lane_from_model_id_crypto():
    assert Lane.from_model_id("CRYPTO_H1") == Lane.CRYPTO


def test_lane_from_model_id_does_not_infer_crypto_from_tickers():
    assert Lane.from_model_id("BTC_FEE_SPIKE") == Lane.CME_FUTURES
    assert Lane.from_model_id("ETH_BASIS") == Lane.CME_FUTURES
    assert Lane.from_model_id("SOL_VOLATILITY") == Lane.CME_FUTURES


def test_lane_from_model_id_equities():
    assert Lane.from_model_id("EQUITY_RUNNER") == Lane.EQUITIES
    assert Lane.from_model_id("LOW_FLOAT_BREAKOUT") == Lane.EQUITIES


def test_lane_from_model_id_options():
    assert Lane.from_model_id("OPTIONS_PUT_CALL") == Lane.OPTIONS
    assert Lane.from_model_id("PARITY_LEG") == Lane.OPTIONS


def test_lane_from_model_id_cme_default():
    assert Lane.from_model_id("HYP_1") == Lane.CME_FUTURES
    assert Lane.from_model_id("CUSTOM_MODEL") == Lane.CME_FUTURES
    assert Lane.from_model_id("") == Lane.CME_FUTURES


def test_registry_has_all_four_lanes():
    reg = LaneRegistry.instance()
    lanes = reg.all_lanes()
    assert Lane.CME_FUTURES in lanes
    assert Lane.CRYPTO in lanes
    assert Lane.EQUITIES in lanes
    assert Lane.OPTIONS in lanes


def test_registry_get_returns_registration():
    reg = LaneRegistry.instance()
    reg_crypto = reg.get(Lane.CRYPTO)
    assert reg_crypto is not None
    assert isinstance(reg_crypto, LaneRegistration)
    assert reg_crypto.lane == Lane.CRYPTO
    assert "tests/test_crypto_lane" in reg_crypto.test_paths


def test_registry_resolve_lane_via_prefix():
    reg = LaneRegistry.instance()
    assert reg.resolve_lane("CRYPTO_H7") == Lane.CRYPTO
    assert reg.resolve_lane("EQUITY_LOW_FLOAT") == Lane.EQUITIES
    assert reg.resolve_lane("OPTIONS_PUT") == Lane.OPTIONS


def test_registry_resolve_lane_falls_back_to_enum():
    reg = LaneRegistry.instance()
    assert reg.resolve_lane("UNKNOWN_PREFIX_123") == Lane.CME_FUTURES
    assert reg.resolve_lane("") == Lane.CME_FUTURES
