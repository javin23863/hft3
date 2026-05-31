"""Purged k-fold splits with embargo and label horizon gap."""
from __future__ import annotations

from typing import Iterator


def purged_splits(
    n: int,
    n_splits: int = 5,
    embargo: int = 1,
    label_horizon: int = 1,
) -> Iterator[tuple[list[int], list[int]]]:
    """Expanding purged splits; yields up to n_splits when feasible."""
    gap = embargo + label_horizon
    if n < gap + 4:
        return

    min_train = max(6, n // (n_splits + 2))
    test_size = max(2, n // (n_splits + 1))
    test_start = min_train + gap
    yielded = 0

    while test_start + test_size <= n and yielded < n_splits:
        train_end = test_start - gap
        if train_end < min_train:
            test_start += test_size
            continue
        train = list(range(0, train_end))
        test = list(range(test_start, min(test_start + test_size, n)))
        if train and test and max(train) + gap < min(test):
            yield train, test
            yielded += 1
        test_start += test_size
