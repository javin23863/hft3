"""Unified registry: 55 models."""

from workbench.src.registry.unified_registry import build_models_config, list_models


def test_fifty_five_models():
    cfg = build_models_config()
    assert len(cfg) == 55
    assert len(list_models()) == 55
    for i in range(1, 45):
        assert f"HYP_{i}" in cfg
    for i in range(1, 12):
        assert f"PDF_MODEL_{i}" in cfg
