"""Model catalog coverage for all registered models."""

from __future__ import annotations

from pathlib import Path

from workbench.src.core.composition import DefensiveStub, ModelComposition
from workbench.src.registry.model_catalog import load_catalog, resolve_stub_dependencies, validate_composition
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


def test_defensive_stub_dependencies_skip_non_defensive_models():
    stubs = resolve_stub_dependencies([DefensiveStub("HAWKES_TOXIC_FLOW", "during", 2500.0)], REPO)
    ids = {stub.model_id for stub in stubs}

    assert "HYBRID_EXECUTION" in ids
    assert "VPIN_TOXICITY" in ids
    assert "BOOK_PRESSURE" not in ids


def test_validate_composition_rejects_non_defensive_stub():
    composition = ModelComposition(
        "SPREAD_BLOWOUT_RECOMPRESSION",
        [DefensiveStub("BOOK_PRESSURE", "during", 2500.0)],
    )

    errors = validate_composition(composition, REPO)

    assert any("defensive role" in error for error in errors)
