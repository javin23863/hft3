"""Hypothesis YAML schema validation."""
from __future__ import annotations

from crypto_lane.src.config_loader import load_hypotheses
from crypto_lane.src.types import HYPOTHESIS_REQUIRED_KEYS


def test_all_hypotheses_have_required_keys():
    hyps = load_hypotheses()
    assert len(hyps) == 7
    for h in hyps:
        missing = HYPOTHESIS_REQUIRED_KEYS - set(h.keys())
        assert not missing, f"{h.get('hypothesis_id')}: missing {missing}"
