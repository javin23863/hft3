"""Chronological validation and CSCV helpers for edge evaluation."""

from __future__ import annotations

from itertools import combinations, islice
import math
from typing import Any, Sequence


Window = tuple[int, int, int, int]


def _column_means(matrix: Sequence[Sequence[float]], rows: Sequence[int]) -> list[float]:
    n_cols = len(matrix[0])
    means: list[float] = []
    for col in range(n_cols):
        vals = [float(matrix[row][col]) for row in rows]
        if not all(math.isfinite(v) for v in vals):
            means.append(float("nan"))
        else:
            means.append(sum(vals) / len(vals))
    return means


def combinatorially_symmetric_cv(
    performance_matrix: Sequence[Sequence[float]],
    *,
    max_partitions: int = 200,
) -> dict[str, float | int | None | str]:
    """Return CSCV PBO, degradation, and loss diagnostics.

    Rows are chronological blocks; columns are strategy/config variants.
    """

    matrix = [list(row) for row in performance_matrix]
    if len(matrix) < 4:
        return {"pbo": None, "performance_degradation": None, "probability_of_loss": None, "n_partitions": 0, "n_configs": None, "reason": "insufficient_blocks"}
    n_cols = len(matrix[0])
    if n_cols < 2 or any(len(row) != n_cols for row in matrix):
        return {"pbo": None, "performance_degradation": None, "probability_of_loss": None, "n_partitions": 0, "n_configs": n_cols, "reason": "invalid_config_count"}

    complete_cols = [
        col
        for col in range(n_cols)
        if all(math.isfinite(float(row[col])) for row in matrix)
    ]
    if len(complete_cols) < 2:
        return {"pbo": None, "performance_degradation": None, "probability_of_loss": None, "n_partitions": 0, "n_configs": len(complete_cols), "reason": "insufficient_complete_configs"}
    matrix = [[float(row[col]) for col in complete_cols] for row in matrix]
    n_blocks = len(matrix)
    half = n_blocks // 2
    all_rows = tuple(range(n_blocks))

    bottom_half = 0
    loss_count = 0
    degradation_sum = 0.0
    n_partitions = 0
    logits: list[float] = []
    for train_rows in islice(combinations(all_rows, half), max_partitions):
        test_rows = tuple(row for row in all_rows if row not in train_rows)
        train_scores = _column_means(matrix, train_rows)
        test_scores = _column_means(matrix, test_rows)
        winner = max(range(len(train_scores)), key=train_scores.__getitem__)
        winner_test = test_scores[winner]
        rank = sum(score >= winner_test for score in test_scores)
        rank_probability = 1.0 - rank / (len(test_scores) + 1.0)
        rank_probability = min(max(rank_probability, 1e-12), 1.0 - 1e-12)
        logits.append(math.log(rank_probability / (1.0 - rank_probability)))
        if rank > len(test_scores) / 2.0:
            bottom_half += 1
        if winner_test < 0.0:
            loss_count += 1
        degradation_sum += train_scores[winner] - winner_test
        n_partitions += 1

    if n_partitions == 0:
        return {"pbo": None, "performance_degradation": None, "probability_of_loss": None, "n_partitions": 0, "n_configs": len(complete_cols), "reason": "no_partitions"}
    return {
        "pbo": bottom_half / n_partitions,
        "performance_degradation": degradation_sum / n_partitions,
        "probability_of_loss": loss_count / n_partitions,
        "logits": logits,
        "n_partitions": n_partitions,
        "n_configs": len(complete_cols),
        "reason": None,
    }


def combinatorial_symmetric_cross_validation(
    performance_matrix: Sequence[Sequence[float]],
    *,
    subsets: int | None = None,
    max_partitions: int = 200,
) -> dict[str, float | int | None | str]:
    """Compatibility wrapper for the same CSCV/PBO calculation."""

    matrix = [list(row) for row in performance_matrix]
    if subsets is not None:
        if subsets < 2 or subsets % 2:
            raise ValueError("subsets must be an even integer >= 2")
        matrix = _chronological_subset_means(matrix, subsets)
    result = combinatorially_symmetric_cv(matrix, max_partitions=max_partitions)
    if "performance_degradation" in result:
        result["median_performance_degradation"] = result["performance_degradation"]
    if "n_partitions" in result:
        result["subsets"] = len(matrix)
    return result


def rolling_windows(
    n_obs: int,
    *,
    train_size: int,
    test_size: int,
    step: int | None = None,
    embargo: int = 0,
) -> list[Window]:
    """Return chronological rolling train/test windows as end-exclusive indexes."""

    return _windows(n_obs, initial_train_size=train_size, test_size=test_size, step=step, embargo=embargo, expanding=False)


def rolling_window_validation(
    n_obs: int | Sequence[float],
    *,
    train_size: int | None = None,
    test_size: int | None = None,
    window: int | None = None,
    step: int | None = None,
    embargo: int = 0,
) -> list[Window] | dict[str, Any]:
    """Compatibility wrapper returning windows or PnL-series validation metrics."""

    if isinstance(n_obs, int):
        if train_size is None or test_size is None:
            raise ValueError("train_size and test_size are required for index windows")
        return rolling_windows(n_obs, train_size=train_size, test_size=test_size, step=step, embargo=embargo)
    if window is None:
        if train_size is None:
            raise ValueError("window or train_size is required for series validation")
        window = train_size
    return _rolling_series_summary(n_obs, window=window, step=step)


def expanding_windows(
    n_obs: int,
    *,
    initial_train_size: int,
    test_size: int,
    step: int | None = None,
    embargo: int = 0,
) -> list[Window]:
    """Return chronological expanding train/test windows as end-exclusive indexes."""

    return _windows(n_obs, initial_train_size=initial_train_size, test_size=test_size, step=step, embargo=embargo, expanding=True)


def _windows(
    n_obs: int,
    *,
    initial_train_size: int,
    test_size: int,
    step: int | None,
    embargo: int,
    expanding: bool,
) -> list[Window]:
    if n_obs < 1 or initial_train_size < 1 or test_size < 1:
        raise ValueError("n_obs, train size, and test_size must be positive")
    if embargo < 0:
        raise ValueError("embargo must be non-negative")
    stride = test_size if step is None else step
    if stride < 1:
        raise ValueError("step must be positive")
    windows: list[Window] = []
    train_start = 0
    train_end = initial_train_size
    while train_end + embargo + test_size <= n_obs:
        test_start = train_end + embargo
        test_end = test_start + test_size
        windows.append((0 if expanding else train_start, train_end, test_start, test_end))
        if expanding:
            train_end += stride
        else:
            train_start += stride
            train_end += stride
    return windows


def _chronological_subset_means(matrix: Sequence[Sequence[float]], subsets: int) -> list[list[float]]:
    if not matrix:
        return []
    n_rows = len(matrix)
    if subsets > n_rows:
        subsets = n_rows if n_rows % 2 == 0 else n_rows - 1
    if subsets < 2:
        return [list(row) for row in matrix]
    blocked: list[list[float]] = []
    for subset in range(subsets):
        start = subset * n_rows // subsets
        end = (subset + 1) * n_rows // subsets
        rows = [list(row) for row in matrix[start:end]]
        if not rows:
            continue
        n_cols = len(rows[0])
        blocked.append([sum(float(row[col]) for row in rows) / len(rows) for col in range(n_cols)])
    return blocked


def _series_metrics(values: Sequence[float]) -> dict[str, float | int]:
    vals = [float(value) for value in values]
    if not vals:
        return {"count": 0, "total": 0.0, "mean": 0.0, "win_rate": 0.0}
    return {
        "count": len(vals),
        "total": sum(vals),
        "mean": sum(vals) / len(vals),
        "win_rate": sum(1 for value in vals if value > 0.0) / len(vals),
    }


def _rolling_series_summary(
    values: Sequence[float],
    *,
    window: int,
    step: int | None,
) -> dict[str, Any]:
    vals = [float(value) for value in values]
    stride = window if step is None else step
    if window < 1 or stride < 1:
        raise ValueError("window and step must be positive")
    windows: list[dict[str, Any]] = []
    start = 0
    while start + window <= len(vals):
        end = start + window
        windows.append({"start": start, "end": end, **_series_metrics(vals[start:end])})
        start += stride
    return {
        "status": "ok" if windows else "skipped",
        "window": window,
        "step": stride,
        "window_count": len(windows),
        "windows": windows,
    }


__all__ = [
    "Window",
    "combinatorial_symmetric_cross_validation",
    "combinatorially_symmetric_cv",
    "expanding_windows",
    "rolling_window_validation",
    "rolling_windows",
]
