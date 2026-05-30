"""Workbench UI import smoke tests."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest


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


def test_render_catalog_rows_rejects_empty_key_prefix() -> None:
    from workbench.ui.campaign_panel import _catalog_widget_key, _render_catalog_rows

    with pytest.raises(ValueError, match="requires a non-empty unique key_prefix"):
        _render_catalog_rows(None, [], {}, key_prefix="")
    with pytest.raises(ValueError, match="requires a non-empty unique key_prefix"):
        _render_catalog_rows(None, [], {}, key_prefix="   ")
    with pytest.raises(ValueError, match="requires a non-empty unique key_prefix"):
        _catalog_widget_key("  ", "catalog_search")


def test_catalog_tab_key_patterns_do_not_collide() -> None:
    from workbench.ui.campaign_panel import _catalog_widget_key

    model_id = "HYP_5"
    alpha_keys = {
        _catalog_widget_key("alpha_catalog", "catalog_search"),
        _catalog_widget_key("alpha_catalog", "select", "0", model_id),
    }
    hybrid_keys = {
        _catalog_widget_key("hybrid_catalog", "catalog_search"),
        _catalog_widget_key("hybrid_catalog", "select", "0", model_id),
    }
    defensive_keys = {
        _catalog_widget_key("defensive_catalog", "catalog_search"),
        _catalog_widget_key("defensive_catalog", "enable", "0", model_id),
    }
    assert alpha_keys.isdisjoint(hybrid_keys)
    assert alpha_keys.isdisjoint(defensive_keys)
    assert hybrid_keys.isdisjoint(defensive_keys)


def test_workbench_ui_has_no_literal_catalog_search_keys() -> None:
    ui_dir = Path(__file__).resolve().parents[2] / "workbench" / "ui"
    forbidden = (
        'key="catalog_search"',
        "key='catalog_search'",
        'key_prefix = "catalog"',
        "key_prefix = 'catalog'",
    )
    for path in ui_dir.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pattern in forbidden:
            assert pattern not in text, f"{path.name} must not contain {pattern!r}"


def test_model_selector_panel_uses_unique_catalog_prefixes() -> None:
    from workbench.ui import campaign_panel

    src = inspect.getsource(campaign_panel.model_selector_panel)
    assert 'key_prefix="alpha_catalog"' in src
    assert 'key_prefix="hybrid_catalog"' in src
    assert 'key_prefix="defensive_catalog"' in src
    assert 'key_prefix="catalog"' not in src
