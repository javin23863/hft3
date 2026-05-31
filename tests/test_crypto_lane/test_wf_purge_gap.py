"""Purged expanding WF folds respect label purge gap."""
from __future__ import annotations

from crypto_lane.src.ml.walk_forward import purged_expanding_folds


def test_purged_expanding_folds_respect_gap():
    n = 100
    embargo = 2
    label_horizon = 3
    gap = embargo + label_horizon
    folds = purged_expanding_folds(
        n, min_train=10, test_size=5, embargo=embargo, label_horizon=label_horizon, min_folds=3
    )
    assert len(folds) >= 1
    for fold in folds:
        train = list(range(fold.train_idx.start or 0, fold.train_idx.stop or 0))
        test = list(range(fold.test_idx.start or 0, fold.test_idx.stop or n))
        if train and test:
            assert max(train) + gap < min(test)
