"""Lane governance tests: Lane.CME_OPTIONS registration and routing.

Golden derivations:
  - FOPT_ES_CALL -> CME_OPTIONS  (from_model_id: FOPT_ prefix)
  - FOPT_NQ_PUT  -> CME_OPTIONS  (from_model_id: FOPT_ prefix)
  - CRYPTO_H1    -> CRYPTO       (unchanged)
  - EQUITY_X     -> EQUITIES     (unchanged)
  - LOW_FLOAT_Y  -> EQUITIES     (unchanged)
  - PARITY_Z     -> EQUITIES     (unchanged, legacy)
  - OPTIONS_PUT  -> EQUITIES     (unchanged, legacy equities-parity prefix)
  - HYP_1        -> CME_FUTURES  (default fallback)
  - CMEOptionsConfig.capability_profile.is_hft       == False
  - CMEOptionsConfig.capability_profile.dma          == True
  - CMEOptionsConfig.capability_profile.research_only == True
"""
from __future__ import annotations

import pytest

from hft3.validation.lanes import Lane, LaneRegistry
from hft3.validation.lanes.adapters.cme_options_adapter import (
    CMEOptionsBacktester,
    CMEOptionsConfig,
    load_cme_options_config,
)
from hft3.validation.lanes.registration import register_all_lanes


@pytest.fixture(autouse=True)
def _reset_registry():
    LaneRegistry.reset()
    register_all_lanes()
    yield
    LaneRegistry.reset()


# --- from_model_id routing ---


def test_fopt_prefix_routes_to_cme_options():
    assert Lane.from_model_id("FOPT_ES_CALL") == Lane.CME_OPTIONS


def test_fopt_prefix_case_insensitive():
    assert Lane.from_model_id("fopt_nq_put") == Lane.CME_OPTIONS


def test_fopt_prefix_variant():
    assert Lane.from_model_id("FOPT_NQ_PUT_SPREAD") == Lane.CME_OPTIONS


# --- Legacy prefix routing unchanged ---


def test_removed_lane_prefixes_fall_through_to_default():
    # CME-only repo: crypto and dedicated-equities lanes moved to their own
    # repos; their prefixes are no longer recognized and fall through to the
    # CME_FUTURES default.
    assert Lane.from_model_id("CRYPTO_H1") == Lane.CME_FUTURES
    assert Lane.from_model_id("EQUITY_RUNNER") == Lane.CME_FUTURES
    assert Lane.from_model_id("LOW_FLOAT_BREAKOUT") == Lane.CME_FUTURES


def test_parity_prefix_unchanged():
    # PARITY_ is a legacy equities-parity prefix; routes to EQUITIES.
    assert Lane.from_model_id("PARITY_LEG") == Lane.EQUITIES


def test_options_legacy_prefix_unchanged():
    # OPTIONS_ is a legacy equities-parity prefix; routes to EQUITIES.
    # New CME options models use FOPT_.
    assert Lane.from_model_id("OPTIONS_PUT_CALL") == Lane.EQUITIES


def test_cme_default_fallback_unchanged():
    assert Lane.from_model_id("HYP_1") == Lane.CME_FUTURES
    assert Lane.from_model_id("CUSTOM_MODEL") == Lane.CME_FUTURES
    assert Lane.from_model_id("") == Lane.CME_FUTURES


# --- Registry: adapter registered ---


def test_cme_options_registered_in_registry():
    reg = LaneRegistry.instance()
    reg_entry = reg.get(Lane.CME_OPTIONS)
    assert reg_entry is not None
    assert reg_entry.lane == Lane.CME_OPTIONS


def test_registry_resolve_fopt_to_cme_options():
    reg = LaneRegistry.instance()
    assert reg.resolve_lane("FOPT_ES_CALL") == Lane.CME_OPTIONS


def test_cme_options_test_paths_registered():
    reg = LaneRegistry.instance()
    reg_entry = reg.get(Lane.CME_OPTIONS)
    assert reg_entry is not None
    assert any("cme_options" in p for p in reg_entry.test_paths)


def test_cme_options_model_id_prefix_registered():
    reg = LaneRegistry.instance()
    reg_entry = reg.get(Lane.CME_OPTIONS)
    assert reg_entry is not None
    assert "FOPT_" in reg_entry.model_id_prefixes


# --- Capability profile pinned ---


def test_cme_options_capability_profile_is_hft_false():
    cfg = CMEOptionsConfig()
    # Options execution is Phase 2; no HFT claim in Phases 0-1.
    assert cfg.capability_profile.is_hft is False


def test_cme_options_capability_profile_dma_true():
    cfg = CMEOptionsConfig()
    # Futures DMA infrastructure exists (CHI404).
    assert cfg.capability_profile.dma is True


def test_cme_options_capability_profile_research_only_true():
    cfg = CMEOptionsConfig()
    # Execution gate is Phase 2; research_only stays True until then.
    assert cfg.capability_profile.research_only is True


def test_cme_options_capability_profile_node_direct_false():
    cfg = CMEOptionsConfig()
    assert cfg.capability_profile.node_direct is False


def test_cme_options_capability_profile_hft_proof_not_required():
    cfg = CMEOptionsConfig()
    assert cfg.capability_profile.hft_proof_required is False


# --- Config defaults ---


def test_cme_options_config_lane():
    cfg = CMEOptionsConfig()
    assert cfg.lane == Lane.CME_OPTIONS


def test_cme_options_config_tick_size():
    cfg = CMEOptionsConfig()
    # ES options default tick: 0.05 index points.
    assert cfg.tick_size == 0.05


def test_cme_options_config_lot_size():
    cfg = CMEOptionsConfig()
    assert cfg.lot_size == 1.0


def test_cme_options_config_symbols_non_empty():
    cfg = CMEOptionsConfig()
    assert len(cfg.symbols) > 0


def test_cme_options_config_latency_bands_non_empty():
    cfg = CMEOptionsConfig()
    assert len(cfg.latency_bands_ms) > 0


def test_cme_options_config_to_dict():
    cfg = CMEOptionsConfig()
    d = cfg.to_dict()
    assert d["lane"] == "cme_options"
    assert isinstance(d["symbols"], list)
    assert isinstance(d["latency_bands_ms"], list)
    assert isinstance(d["capability_profile"], dict)
    assert d["capability_profile"]["is_hft"] is False
    assert d["capability_profile"]["dma"] is True
    assert d["capability_profile"]["research_only"] is True


# --- Backtester protocol ---


def test_cme_options_backtester_run():
    bt = CMEOptionsBacktester(load_cme_options_config())
    result = bt.run(target="FOPT_ES_CALL_20241220")
    assert result.lane == Lane.CME_OPTIONS
    assert result.degraded is False
    assert result.extra["research_only"] is True


def test_cme_options_backtester_validate_config():
    bt = CMEOptionsBacktester(load_cme_options_config())
    errors = bt.validate_config()
    assert errors == []
