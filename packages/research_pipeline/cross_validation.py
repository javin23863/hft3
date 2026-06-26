"""Chronological validation and CSCV helpers for edge evaluation.

Two API layers:
- Edge-evaluation panel API: ``combinatorially_symmetric_cv(performance_matrix)`` — rows are chronological blocks, columns are strategies. Pure-Python.
- Continuous-lane stream API: ``cscv_pbo(returns)``, ``cscv_pbo_panel(panel)``, ``walk_forward_split``, ``walk_forward_eval`` — accepts a single return stream or a strategy panel; uses NumPy.
"""

from __future__ import annotations

from itertools import combinations, islice
import math
from typing import Any, Iterable, Sequence

import numpy as np


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
    # Continuous-lane stream API
    "cscv_pbo", "cscv_pbo_panel", "walk_forward_split", "walk_forward_eval",
]


# ---------------------------------------------------------------------------
# Continuous-lane stream API (PDF section 10). Single-stream and panel PBO
# plus PIT-safe chronological walk-forward split/eval.
# ---------------------------------------------------------------------------


def _as_returns(returns: Iterable[float]) -> np.ndarray:
    arr = np.asarray(list(returns), dtype=np.float64)
    if arr.size == 0:
        return arr
    return arr[np.isfinite(arr)]


def _stream_sharpe(arr: np.ndarray) -> float:
    if arr.size < 2:
        return 0.0
    std = float(arr.std(ddof=1))
    if std <= 0.0:
        return 0.0
    return float(arr.mean() / std)


def cscv_pbo(
    returns: Iterable[float],
    *,
    num_blocks: int = 16,
    random_state: int | None = None,
) -> dict:
    """Estimate PBO for a single strategy via block-bootstrap pseudo-panel."""
    arr = _as_returns(returns)
    n = arr.size
    if n < num_blocks * 2:
        return {"pbo": 0.5, "num_blocks": num_blocks, "folds": 0, "lambda": 0.5}
    rng = np.random.default_rng(random_state)
    block_size = max(1, n // num_blocks)
    blocks = [arr[i * block_size : (i + 1) * block_size] for i in range(num_blocks)]
    blocks = [b for b in blocks if b.size >= 2]
    if len(blocks) < 2:
        return {"pbo": 0.5, "num_blocks": num_blocks, "folds": 0, "lambda": 0.5}
    pseudo_strategies = [blocks[int(rng.integers(0, len(blocks)))] for _ in range(num_blocks)]
    min_len = min(s.size for s in pseudo_strategies)
    if min_len < 2:
        return {"pbo": 0.5, "num_blocks": num_blocks, "folds": 0, "lambda": 0.5}
    panel = np.array([s[:min_len] for s in pseudo_strategies])
    num_strategies = panel.shape[0]
    ranks_out_of_sample = np.zeros(num_strategies)
    count_worst = 0
    folds = 0
    for holdout in range(num_strategies):
        in_sample_idx = [j for j in range(num_strategies) if j != holdout]
        if not in_sample_idx:
            continue
        in_sample_sharpes = np.array([_stream_sharpe(panel[j]) for j in in_sample_idx])
        best_local = int(np.argmax(in_sample_sharpes))
        best_strategy_global_idx = in_sample_idx[best_local]
        all_out_sharpes = np.array([_stream_sharpe(panel[j]) for j in range(num_strategies)])
        if all_out_sharpes.std() <= 0.0:
            continue
        ranks = np.argsort(np.argsort(all_out_sharpes))
        percentile = ranks[best_strategy_global_idx] / max(num_strategies - 1, 1)
        ranks_out_of_sample[holdout] = percentile
        if percentile <= 0.5:
            count_worst += 1
        folds += 1
    if folds == 0:
        return {"pbo": 0.5, "num_blocks": num_blocks, "folds": 0, "lambda": 0.5}
    pbo = count_worst / folds
    # Include zero percentiles (worst-rank out-of-sample) — excluding them would
    # bias lambda upward and mask the worst overfitting cases.
    lam = float(np.mean(ranks_out_of_sample)) if ranks_out_of_sample.size > 0 else 0.5
    return {"pbo": float(pbo), "num_blocks": int(num_blocks), "folds": int(folds), "lambda": float(lam)}


def cscv_pbo_panel(
    panel: Iterable[Iterable[float]],
    *,
    num_blocks: int = 16,
) -> dict:
    """CSCV PBO for a panel (rows = strategies, cols = time). Canonical Bailey et al."""
    mat = np.array([_as_returns(row) for row in panel], dtype=np.float64)
    if mat.ndim != 2 or mat.shape[0] < 2 or mat.shape[1] < num_blocks * 2:
        return {"pbo": 0.5, "num_blocks": num_blocks, "folds": 0, "lambda": 0.5}
    num_strategies, total_len = mat.shape
    block_size = max(1, total_len // num_blocks)
    blocks = [mat[:, i * block_size : (i + 1) * block_size] for i in range(num_blocks)]
    blocks = [b for b in blocks if b.shape[1] >= 2]
    if len(blocks) < 2:
        return {"pbo": 0.5, "num_blocks": num_blocks, "folds": 0, "lambda": 0.5}
    ranks_out_of_sample = []
    count_worst = 0
    folds = 0
    for holdout in range(len(blocks)):
        in_sample_idx = [j for j in range(len(blocks)) if j != holdout]
        if not in_sample_idx:
            continue
        in_sample = np.concatenate([blocks[j] for j in in_sample_idx], axis=1)
        out_sample = blocks[holdout]
        in_sample_sharpes = np.array([_stream_sharpe(in_sample[s]) for s in range(num_strategies)])
        best_strategy = int(np.argmax(in_sample_sharpes))
        out_sample_sharpes = np.array([_stream_sharpe(out_sample[s]) for s in range(num_strategies)])
        if out_sample_sharpes.std() <= 0.0:
            continue
        ranks = np.argsort(np.argsort(out_sample_sharpes))
        percentile = ranks[best_strategy] / max(num_strategies - 1, 1)
        ranks_out_of_sample.append(percentile)
        if percentile <= 0.5:
            count_worst += 1
        folds += 1
    if folds == 0:
        return {"pbo": 0.5, "num_blocks": num_blocks, "folds": 0, "lambda": 0.5}
    pbo = count_worst / folds
    arr_ranks = np.array(ranks_out_of_sample)
    lam = float(np.mean(arr_ranks)) if arr_ranks.size > 0 else 0.5
    return {"pbo": float(pbo), "num_blocks": int(num_blocks), "folds": int(folds), "lambda": float(lam)}


def walk_forward_split(
    returns: Iterable[float],
    *,
    train_fraction: float = 0.7,
) -> tuple[np.ndarray, np.ndarray]:
    """Deterministic chronological train/test split (no shuffle — PIT safe)."""
    if not 0.0 < train_fraction < 1.0:
        raise ValueError("train_fraction must be in (0, 1)")
    arr = _as_returns(returns)
    if arr.size < 4:
        return arr, np.array([], dtype=np.float64)
    cut = max(2, int(arr.size * train_fraction))
    return arr[:cut], arr[cut:]


def walk_forward_eval(
    returns: Iterable[float],
    *,
    train_fraction: float = 0.7,
) -> dict:
    """Walk-forward in-sample vs out-of-sample Sharpe comparison."""
    arr = _as_returns(returns)
    if arr.size < 4:
        return {"in_sample_sharpe": 0.0, "out_sample_sharpe": 0.0, "degradation": 0.0, "overfit_flag": False}
    train, test = walk_forward_split(arr, train_fraction=train_fraction)
    if train.size < 2 or test.size < 2:
        return {"in_sample_sharpe": 0.0, "out_sample_sharpe": 0.0, "degradation": 0.0, "overfit_flag": False}
    in_sr = _stream_sharpe(train)
    out_sr = _stream_sharpe(test)
    degradation = in_sr - out_sr if in_sr > 0.0 else 0.0
    overfit = bool(in_sr > 0.0 and out_sr <= 0.0 and in_sr > abs(out_sr) * 2)
    return {
        "in_sample_sharpe": float(in_sr), "out_sample_sharpe": float(out_sr),
        "degradation": float(degradation), "overfit_flag": overfit,
    }
