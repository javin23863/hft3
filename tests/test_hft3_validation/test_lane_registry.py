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
    assert Lane.EQUITIES.value == "equities"


def test_lane_enum_no_options_member():
    # EQUITIES is the historical name of the options/parity lane.
    assert not hasattr(Lane, "OPTIONS")


def test_lane_from_model_id_options_merged_to_equities():
    assert Lane.from_model_id("OPTIONS_PUT_CALL") == Lane.EQUITIES
    assert Lane.from_model_id("PARITY_LEG") == Lane.EQUITIES


def test_lane_from_model_id_cme_default():
    assert Lane.from_model_id("HYP_1") == Lane.CME_FUTURES
    assert Lane.from_model_id("CUSTOM_MODEL") == Lane.CME_FUTURES
    assert Lane.from_model_id("") == Lane.CME_FUTURES


def test_registry_has_two_lanes():
    reg = LaneRegistry.instance()
    lanes = reg.all_lanes()
    assert len(lanes) == 2
    assert Lane.CME_FUTURES in lanes
    assert Lane.EQUITIES in lanes


def test_registry_get_returns_registration():
    reg = LaneRegistry.instance()
    reg_equities = reg.get(Lane.EQUITIES)
    assert reg_equities is not None
    assert isinstance(reg_equities, LaneRegistration)
    assert reg_equities.lane == Lane.EQUITIES
    assert "tests/test_workbench/test_options_lane_campaign.py" in reg_equities.test_paths


def test_registry_resolve_lane_via_prefix():
    reg = LaneRegistry.instance()
    assert reg.resolve_lane("PARITY_LEG") == Lane.EQUITIES
    assert reg.resolve_lane("OPTIONS_PUT") == Lane.EQUITIES


def test_registry_resolve_lane_falls_back_to_enum():
    reg = LaneRegistry.instance()
    assert reg.resolve_lane("UNKNOWN_PREFIX_123") == Lane.CME_FUTURES
    assert reg.resolve_lane("") == Lane.CME_FUTURES


def test_resolve_lane_options_put_routes_to_equities():
    reg = LaneRegistry.instance()
    assert reg.resolve_lane("OPTIONS_PUT") == Lane.EQUITIES


def test_resolve_lane_parity_routes_to_equities():
    reg = LaneRegistry.instance()
    assert reg.resolve_lane("PARITY_LEG") == Lane.EQUITIES
