"""Negative controls required in candidate YAML."""
from __future__ import annotations

from crypto_lane.src.ml.candidate_registry import discover_candidates


def test_negative_controls_present():
    for c in discover_candidates():
        nc = c["negative_controls"]
        assert nc.get("shuffled_labels") is True
        assert nc.get("shifted_features_forward") is True
