"""Workbench UI import smoke tests."""

from __future__ import annotations


def test_campaign_panel_imports_defensive_stub() -> None:
    from workbench.src.core.composition import DefensiveStub, ModelComposition
    from workbench.ui import campaign_panel

    assert campaign_panel.DefensiveStub is DefensiveStub
    assert campaign_panel.ModelComposition is ModelComposition


def test_protocol_does_not_reexport_composition_types() -> None:
    from workbench.src.core import protocol

    assert "CatalogEntry" not in protocol.__all__
    assert not hasattr(protocol, "CatalogEntry")


def test_composition_is_canonical_for_catalog_types() -> None:
    from workbench.src.core.composition import CatalogEntry, DefensiveStub, ModelComposition

    assert CatalogEntry.__name__ == "CatalogEntry"
    assert DefensiveStub.__name__ == "DefensiveStub"
    assert ModelComposition.__name__ == "ModelComposition"


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


def test_app_module_imports() -> None:
    import workbench.ui.app as app

    assert hasattr(app, "REPO")
    assert app.REPO.is_dir()
