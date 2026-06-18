"""VIX/VVIX sensor and VIX-options assembly tests (Phase 3)."""

from __future__ import annotations

import math

import numpy as np

from replay.sensor_assembly import (
    VIX_SENSOR_KEY,
    apply_vix_to_recipe_families,
    enrich_sensor_leg,
    validate_vix_families,
)


def test_enrich_sensor_leg_adds_provenance() -> None:
    leg = enrich_sensor_leg({"vix_atm_strike": 18.5}, sensor_id="VIX", source_timestamp_ns=5000)
    assert leg["_symbol"] == "VIX"
    assert leg["_source_timestamp_ns"] == 5000
    assert leg["vix_atm_strike"] == 18.5


def test_validate_rejects_missing_leg() -> None:
    result = validate_vix_families(None)
    assert not result.vix_vvix_ok
    assert not result.vix_options_ok
    assert "vix_sensor_leg_missing" in result.reasons


def test_validate_nan_not_treated_as_present() -> None:
    leg = enrich_sensor_leg(
        {"vix_atm_strike": float("nan"), "vix_opt_spread_stress": 0.1},
        sensor_id="VIX",
        source_timestamp_ns=1000,
    )
    result = validate_vix_families(leg, decision_timestamp_ns=2000)
    assert not result.vix_vvix_ok
    assert result.vix_options_ok
    assert any("malformed_vvix" in r for r in result.reasons)


def test_validate_accepts_vvix_and_options_columns() -> None:
    leg = enrich_sensor_leg(
        {
            "vix_atm_strike": 20.0,
            "vix_atm_ramp": 0.05,
            "vix_opt_spread_stress": 1.2,
            "vix_opt_depth_imbalance": 0.3,
        },
        sensor_id="VIX",
        source_timestamp_ns=1000,
    )
    result = validate_vix_families(leg, decision_timestamp_ns=2000)
    assert result.vix_vvix_ok
    assert result.vix_options_ok
    assert "vix_opt_depth_imbalance" in result.proxy_only_columns


def test_validate_rejects_future_source_timestamp() -> None:
    leg = enrich_sensor_leg({"vix_atm_strike": 19.0}, sensor_id="VIX", source_timestamp_ns=3000)
    result = validate_vix_families(leg, decision_timestamp_ns=2000)
    assert not result.vix_vvix_ok
    assert "future_vix_source_timestamp" in result.reasons


def test_validate_rejects_missing_provenance_when_decision_ts_set() -> None:
    leg = {"vix_atm_strike": 19.0}
    result = validate_vix_families(leg, decision_timestamp_ns=2000)
    assert not result.vix_vvix_ok
    assert "missing_vix_provenance" in result.reasons


def test_apply_vix_updates_recipe_families() -> None:
    leg = enrich_sensor_leg(
        {"vix_atm_strike": 18.0, "vix_opt_quote_intensity": 2.0},
        sensor_id="VIX",
        source_timestamp_ns=1000,
    )
    validation = validate_vix_families(leg, decision_timestamp_ns=2000)
    proof = validation.to_proof()
    assert proof["source_timestamp_ns"] == 1000
    assert proof["decision_timestamp_ns"] == 2000
    families: dict = {
        "vix_vvix_sensor": {"family_id": "vix_vvix_sensor"},
        "vix_options": {"family_id": "vix_options"},
    }
    apply_vix_to_recipe_families(families, validation)
    assert families["vix_vvix_sensor"]["source_ids"] == [VIX_SENSOR_KEY]
    assert families["vix_vvix_sensor"]["selected_features"] == ["vix_atm_strike"]
    assert families["vix_options"]["selected_features"] == ["vix_opt_quote_intensity"]
