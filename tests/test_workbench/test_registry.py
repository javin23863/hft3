"""Unified registry: model slugs."""

from features_engine.src.model_registry import all_slugs, legacy_to_slug
from hft3.validation.lanes import Lane, LaneRegistry
from hft3.validation.lanes.registration import register_all_lanes
from workbench.src.registry.unified_registry import build_models_config, list_models


def test_all_registered_models_are_exposed():
    cfg = build_models_config()
    slugs = all_slugs()
    assert len(cfg) == len(slugs)
    assert len(list_models()) == len(slugs)
    for slug in slugs:
        assert slug in cfg
    assert "SPREAD_BLOWOUT_RECOMPRESSION" in cfg
    assert "HYBRID_EXECUTION" in cfg
    assert "GHOST_ROUTE" in cfg
    assert cfg["GHOST_ROUTE"].latency_lane == "microsecond"
    assert cfg["GHOST_ROUTE"].execution_assumptions == "fak_limit_replay"


def test_legacy_resolve_via_get_model():
    from workbench.src.registry.unified_registry import get_model_by_id

    m = get_model_by_id("HYP_5")
    assert m.config.model_id == "SPREAD_BLOWOUT_RECOMPRESSION"


def test_ghost_route_resolves_to_cme_lane():
    register_all_lanes()
    reg = LaneRegistry.instance()

    assert reg.resolve_lane("GHOST_ROUTE") == Lane.CME_FUTURES
    assert reg.resolve_lane("HYP_45") == Lane.CME_FUTURES
