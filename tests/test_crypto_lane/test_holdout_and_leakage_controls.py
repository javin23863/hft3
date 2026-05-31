"""Holdout and leakage control flags in validation blocks."""
from __future__ import annotations

from crypto_lane.src.ml.candidate_registry import discover_candidates


def test_validation_blocks_holdout_and_leakage():
    for c in discover_candidates():
        v = c["validation"]
        assert v.get("holdout_blocked") is True
        assert v.get("leakage_checks") is True
        assert v.get("walk_forward") is True
