"""Workbench UI import smoke tests."""

from __future__ import annotations


def test_campaign_panel_imports_defensive_stub() -> None:
    from workbench.src.core.composition import DefensiveStub, ModelComposition
    from workbench.ui import campaign_panel

    assert campaign_panel.DefensiveStub is DefensiveStub
    assert campaign_panel.ModelComposition is ModelComposition


def test_protocol_reexports_composition_types() -> None:
    from workbench.src.core import composition, protocol

    assert protocol.CatalogEntry is composition.CatalogEntry
    assert protocol.DefensiveStub is composition.DefensiveStub
    assert protocol.ModelComposition is composition.ModelComposition


def test_model_catalog_imports_catalog_entry() -> None:
    from workbench.src.core.composition import CatalogEntry
    from workbench.src.registry.model_catalog import load_catalog

    catalog = load_catalog()
    assert catalog
    assert isinstance(next(iter(catalog.values())), CatalogEntry)


def test_campaign_panel_full_import() -> None:
    import workbench.ui.campaign_panel as campaign_panel

    assert hasattr(campaign_panel, "init_session")
    assert hasattr(campaign_panel, "model_selector_panel")
