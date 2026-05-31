"""Purged k-fold with embargo on event timestamps."""

from __future__ import annotations

from typing import Iterator, List, Tuple


def purged_splits(
    n: int,
    n_splits: int = 5,
    embargo: int = 10,
) -> Iterator[Tuple[List[int], List[int]]]:
    """Simple purged CV: train on past, test on future chunk with embargo gap."""
    if n <= n_splits + embargo:
        yield list(range(0, max(1, n // 2))), list(range(max(1, n // 2), n))
        return
    fold = n // n_splits
    for i in range(n_splits):
        test_start = i * fold
        test_end = min(n, (i + 1) * fold)
        train_end = max(0, test_start - embargo)
        train = list(range(0, train_end))
        test = list(range(test_start, test_end))
        if train and test:
            yield train, test
