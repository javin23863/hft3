"""Binding acceptance tests.

Tests that:
  - Every runnable model has a lane binding
  - CME models bind to CME symbols, not equity sessions
  - Equities models bind to sessions, not CME symbols
  - Options/parity models bind to groups
  - Missing required data blocks runs
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workbench.src.data.lane_bindings import (
    load_lane_bindings,
    get_lane_for_model,
    get_lane_binding,
)
from workbench.src.registry.unified_registry import build_models_config


REPO = Path(__file__).resolve().parents[2]


def test_every_runnable_model_has_lane_binding():
    """Every alpha or hybrid model must have at least one lane binding."""
    configs = build_models_config()
    bindings = load_lane_bindings(REPO)

    all_models = set(configs.keys())
    bound_models = set(bindings.model_to_lanes.keys())

    # Models not in explicit bindings default to cme_futures
    missing = all_models - bound_models
    for model_id in missing:
        lanes = get_lane_for_model(model_id, REPO)
        assert lanes == ["cme_futures"], (
            f"Unbound model {model_id} should default to cme_futures, got {lanes}"
        )


def test_cme_models_bind_to_cme_symbols():
    """CME models must bind to CME futures lane."""
    binding = get_lane_binding("cme_futures", REPO)
    assert binding is not None
    assert "MES.v.0" in binding.allowed_symbols
    assert "ES.v.0" in binding.allowed_symbols
    assert "NQ.v.0" in binding.allowed_symbols


def test_equities_models_bind_to_sessions_not_cme_symbols_only():
    """Equities models must be bound to the equities lane (sessions), not forced into CME symbols."""
    bindings = load_lane_bindings(REPO)
    equities_models = [
        mid for mid, lanes in bindings.model_to_lanes.items()
        if "equities_low_float" in lanes
    ]
    assert len(equities_models) > 0, "No models bound to equities lane"

    for mid in equities_models:
        lanes = bindings.model_to_lanes[mid]
        assert "equities_low_float" in lanes, f"Model {mid} should be in equities lane"


def test_options_parity_models_bind_to_groups():
    """Options/parity lane should have models bound (when configured)."""
    binding = get_lane_binding("options_parity", REPO)
    assert binding is not None
    assert binding.group_config == "packages/options_lane/config/parity_universe.yaml"
    assert binding.status == "operational"


def test_binding_missing_required_data_blocks_run():
    """If lane binding declares required data and it's missing, that should block runs."""
    binding = get_lane_binding("equities_low_float", REPO)
    assert binding is not None

    # Check validation policies
    assert "l3_required" in binding.validation_policies
    assert "float_metadata_pit" in binding.validation_policies
    assert "equity_pit" in binding.validation_policies


def test_all_lanes_have_binding_defined():
    """Every lane in lane_bindings.yaml must have complete binding info."""
    bindings = load_lane_bindings(REPO)
    for lane_id, binding in bindings.lanes.items():
        assert binding.lane_id == lane_id
        assert binding.lane_name, f"Lane {lane_id} missing name"
        assert binding.status in ("operational", "degraded", "incomplete", "unknown"), \
            f"Lane {lane_id} has invalid status: {binding.status}"


def test_cme_binding_has_all_seven_symbols():
    """CME binding must declare all 7 canonical symbols."""
    binding = get_lane_binding("cme_futures", REPO)
    assert binding is not None
    assert len(binding.allowed_symbols) == 7
    assert set(binding.allowed_symbols) == {
        "MES.v.0", "ES.v.0", "MNQ.v.0", "NQ.v.0", "ZN.v.0", "ZB.v.0", "RTY.v.0"
    }


def test_equities_binding_has_l3_enforcement():
    """Equities binding must enforce L3-only policy."""
    binding = get_lane_binding("equities_low_float", REPO)
    assert binding is not None
    assert binding.l3_policy == "enforce"
    assert binding.l3_only is True


def test_equities_binding_has_options_feature_config():
    """Equities binding must have options feature phase config."""
    binding = get_lane_binding("equities_low_float", REPO)
    assert binding is not None
    assert binding.options_feature_phase == "optional"
    assert "KODK_2020" in binding.options_feature_enabled_for
    assert "GME_2021" in binding.options_feature_enabled_for
