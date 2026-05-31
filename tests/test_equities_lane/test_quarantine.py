"""Tests for equities lane quarantine."""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "packages" / "equities_lane" / "config" / "universe.yaml"


def test_paths_not_under_production_npz():
    from equities_lane.src.config_loader import load_universe

    _, _, paths = load_universe(CONFIG)
    prod_npz = (REPO / "data" / "npz").resolve()
    for label, p in paths.items():
        resolved = p.resolve()
        try:
            resolved.relative_to(prod_npz)
            raise AssertionError(f"{label}={resolved} is under production npz")
        except ValueError as exc:
            if "is under production npz" in str(exc):
                raise
            continue
