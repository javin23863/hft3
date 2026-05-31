"""Walk-forward fold generation with embargo and label purge."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Fold:
    train_idx: slice
    test_idx: slice


def expanding_walk_forward_folds(
    n: int,
    *,
    min_train: int = 6,
    test_size: int = 2,
    embargo: int = 1,
    min_folds: int = 3,
) -> list[Fold]:
    """Legacy expanding folds (no label purge). Prefer purged_expanding_folds."""
    return purged_expanding_folds(
        n,
        min_train=min_train,
        test_size=test_size,
        embargo=embargo,
        label_horizon=0,
        min_folds=min_folds,
    )


def purged_expanding_folds(
    n: int,
    *,
    min_train: int = 6,
    test_size: int = 2,
    embargo: int = 1,
    label_horizon: int = 1,
    min_folds: int = 3,
) -> list[Fold]:
    """
    Expanding walk-forward with label-overlap purge.

    Train ends at test_start - embargo - label_horizon so labels overlapping
    the test window are excluded from training (BLUEPRINT B3 / purged CV).
    """
    if n < min_train + test_size + embargo + label_horizon:
        return []

    folds: list[Fold] = []
    test_start = min_train + embargo + label_horizon
    while test_start + test_size <= n:
        train_end = test_start - embargo - label_horizon
        if train_end >= min_train:
            folds.append(
                Fold(train_idx=slice(0, train_end), test_idx=slice(test_start, test_start + test_size))
            )
        test_start += test_size

    if len(folds) < min_folds and n >= min_train + test_size + embargo + label_horizon:
        ts = min_train + embargo + label_horizon
        folds = [
            Fold(train_idx=slice(0, max(min_train, ts - embargo - label_horizon)), test_idx=slice(ts, min(ts + test_size, n)))
        ]

    return folds[:max(min_folds, 1)] if folds else []
