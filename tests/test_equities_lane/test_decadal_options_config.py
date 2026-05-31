"""Plumbing tests for decadal options config."""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CATALOG = REPO / "packages" / "equities_lane" / "config" / "decadal_runners.yaml"


def test_load_options_defaults():
    from equities_lane.src.decadal_config import load_decadal_catalog

    catalog = load_decadal_catalog(CATALOG)
    gme = next(s for s in catalog.sessions if s.id == "gme_2021")
    assert gme.options is not None
    assert gme.options.enabled
    assert gme.options.dataset == "OPRA.PILLAR"
    assert gme.options.schema == "cbbo-1m"
    assert gme.options.chain_rules.strike_window_pct == 0.30


def test_skip_pull_from_catalog():
    from equities_lane.src.decadal_config import load_decadal_catalog

    catalog = load_decadal_catalog(CATALOG)
    drys = next(s for s in catalog.sessions if s.id == "drys_2016")
    assert drys.skip_pull is True
