"""Purged CV must leave gap between train and test."""
from __future__ import annotations

from crypto_lane.src.ml.purged_cv import purged_splits


def test_purged_splits_respect_gap():
    n = 100
    embargo = 2
    label_horizon = 3
    gap = embargo + label_horizon
    for train, test in purged_splits(n, n_splits=5, embargo=embargo, label_horizon=label_horizon):
        if not train or not test:
            continue
        assert max(train) + gap < min(test)
