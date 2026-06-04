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
        _render_catalog_rows(None, [], {}, key_prefix="", symbol="MES.v.0", audit_grade=True)
    with pytest.raises(ValueError, match="requires a non-empty unique key_prefix"):
        _render_catalog_rows(None, [], {}, key_prefix="   ", symbol="MES.v.0", audit_grade=True)
    with pytest.raises(ValueError, match="requires a non-empty unique key_prefix"):
        _catalog_widget_key("  ", "catalog_search")


def test_catalog_tab_key_patterns_do_not_collide() -> None:
    from workbench.ui.campaign_panel import _catalog_widget_key

    model_id = "HYP_5"
    all_keys = {
        _catalog_widget_key("all_catalog", "catalog_search"),
        _catalog_widget_key("all_catalog", "set", "0", model_id),
        _catalog_widget_key("all_catalog", "run", "0", model_id),
    }
    assert len(all_keys) == 3


def test_workbench_ui_has_no_literal_catalog_search_keys() -> None:
    ui_dir = Path(__file__).resolve().parents[2] / "apps" / "workbench" / "ui"
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
    assert 'key_prefix="all_catalog"' in src
    assert 'key_prefix="catalog"' not in src

    catalog_src = inspect.getsource(campaign_panel._render_catalog_rows)
    assert "Set primary" in catalog_src
    assert "Run campaign" in catalog_src


def test_tabs_are_pipeline_monitor_surface() -> None:
    from workbench.ui.workflow_tabs import WORKFLOW_TABS

    assert WORKFLOW_TABS[:4] == [
        "Autonomous Run",
        "Registry & Data",
        "Backtest Evidence",
        "Latency Evidence",
    ]
    assert "Decision & Registry" in WORKFLOW_TABS
    assert "Model Selector" not in WORKFLOW_TABS


def test_model_selector_uses_backend_catalog_not_hardcoded_starters() -> None:
    from workbench.ui import campaign_panel

    src = inspect.getsource(campaign_panel.model_selector_panel)
    forbidden = (
        "_RECOMMENDED_STARTERS",
        "Quick start",
        "Trial mode",
        "HYP_5",
        "runnable[0]",
        "backfill_catalog.py",
        'repo / "workbench" / "scripts"',
    )
    for pattern in forbidden:
        assert pattern not in src

    assert "_render_backend_status" in src
    assert "_catalog_symbols" in src
    assert '"workbench"' in src
    assert '"download"' in src
    assert "pythonpath_entries" in inspect.getsource(campaign_panel)
    assert "wb_selection_explicit" in src


def test_backend_status_does_not_surface_event_family_rankings() -> None:
    from workbench.ui import campaign_panel

    src = inspect.getsource(campaign_panel._render_backend_status)
    assert "top types" not in src
    assert "most_common" not in src

    dataset_src = inspect.getsource(campaign_panel._render_dataset_panel)
    assert "Contexts:" not in dataset_src
    assert "Event contexts configured" in dataset_src


def test_catalog_symbols_come_from_event_catalog(tmp_path: Path) -> None:
    from workbench.ui.campaign_panel import _catalog_symbols

    config_dir = tmp_path / "packages" / "data_system" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "events.csv").write_text(
        "event_id,event_type,symbols\n"
        'E1,CUSTOM,"FOO.v.0,BAR.v.0"\n'
        'E2,CUSTOM,"ES.v.0,BAZ.v.0"\n',
        encoding="utf-8",
    )

    assert _catalog_symbols(tmp_path) == ["ES.v.0", "BAR.v.0", "BAZ.v.0", "FOO.v.0"]


def test_catalog_symbols_missing_catalog_does_not_fallback_to_fixed_list(tmp_path: Path) -> None:
    from workbench.ui.campaign_panel import _catalog_symbols

    assert _catalog_symbols(tmp_path) == []


def test_workbench_ui_has_no_stale_operator_surface_strings() -> None:
    ui_dir = Path(__file__).resolve().parents[2] / "apps" / "workbench" / "ui"
    forbidden = (
        "Model Selector",
        "Quick start",
        "Trial mode",
        "_RECOMMENDED_STARTERS",
        "workbench_pipeline_trial.py",
        "backfill_catalog.py",
        "Load preset:",
        "use_container_width",
    )
    for path in ui_dir.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pattern in forbidden:
            assert pattern not in text, f"{path.name} must not contain {pattern!r}"


def test_autonomous_panel_is_registry_and_status_driven() -> None:
    from workbench.ui import autonomous_panel

    src = inspect.getsource(autonomous_panel)
    assert "discover_candidates" in src
    assert "latest_status_path" in src
    assert "Run All Crypto Candidates" in src
    assert "crypto_h1_basis_compression" not in src
    assert "Candidate filter" not in src


def test_app_tabs_use_shared_run_evidence_snapshot() -> None:
    import workbench.ui.app as app
    from workbench.ui import evidence_panels

    src = inspect.getsource(app)
    assert "load_run_evidence" in src
    assert 'st.query_params.get("source"' in src
    assert "render_backtest_evidence(snapshot)" in src
    assert "render_latency_evidence(snapshot)" in src
    assert "render_signal_diagnostics(snapshot)" in src
    assert "render_robustness(snapshot)" in src
    assert "render_decision_registry(snapshot)" in src
    assert hasattr(evidence_panels, "render_registry_data")
    panel_src = inspect.getsource(evidence_panels)
    assert "Bitcoin state packet transport" in panel_src
    assert "Bitcoin edge packet schema" in panel_src
    assert "P&L ranking" in panel_src
    assert "OOS equity curve" in panel_src
    assert "Leakage controls" in panel_src
