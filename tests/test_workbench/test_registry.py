"""Unified registry: 55 models with slug IDs."""

from features_engine.src.model_registry import all_slugs, legacy_to_slug
from workbench.src.registry.unified_registry import build_models_config, list_models


def test_fifty_five_models():
    cfg = build_models_config()
    assert len(cfg) == 55
    assert len(list_models()) == 55
    for slug in all_slugs():
        assert slug in cfg
    assert "SPREAD_BLOWOUT_RECOMPRESSION" in cfg
    assert "HYBRID_EXECUTION" in cfg


def test_legacy_resolve_via_get_model():
    from workbench.src.registry.unified_registry import get_model_by_id

    m = get_model_by_id("HYP_5")
    assert m.config.model_id == "SPREAD_BLOWOUT_RECOMPRESSION"
