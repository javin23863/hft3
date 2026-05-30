"""Workbench UI import smoke tests."""

from __future__ import annotations


def test_campaign_panel_imports_defensive_stub() -> None:
    from workbench.src.core.composition import DefensiveStub, ModelComposition
    from workbench.ui import campaign_panel

    assert campaign_panel.DefensiveStub is DefensiveStub
    assert campaign_panel.ModelComposition is ModelComposition


def test_protocol_reexports_composition_types() -> None:
    from workbench.src.core import composition, protocol

    assert protocol.DefensiveStub is composition.DefensiveStub
    assert protocol.ModelComposition is composition.ModelComposition
