"""Model catalog coverage for all registered models."""

from __future__ import annotations

from pathlib import Path

from workbench.src.registry.model_catalog import load_catalog
from workbench.src.registry.unified_registry import list_models

REPO = Path(__file__).resolve().parents[2]


def test_every_model_has_catalog_entry():
    catalog = load_catalog(REPO)
    for mid in list_models():
        assert mid in catalog
        assert catalog[mid].display_name
        assert len(catalog[mid].description) <= 240


def test_defensive_models_tagged():
    catalog = load_catalog(REPO)
    assert catalog["QUANTUM_SPREAD_DEFENSE"].role == "defensive"
    assert catalog["QUANTUM_SPREAD_DEFENSE"].blocks_trade is True
    assert catalog["HAWKES_TOXIC_FLOW"].requires == ("HYBRID_EXECUTION",)
