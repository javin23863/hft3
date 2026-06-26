"""Cross-validation and Probability of Backtest Overfitting (Phase 6 §10).

Combinatorially Symmetric Cross-Validation (CSCV) from Bailey, Borwein,
Lopez de Prado & Zhu (2017). Estimates the Probability of Backtest Overfitting
(PBO) by partitioning the return stream into N blocks, holding each out in
turn, and measuring how often the in-sample-best strategy ranks worst
out-of-sample.

Reference (PDF §16): https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf
"""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np


def _as_returns(returns: Iterable[float]) -> np.ndarray:
    arr = np.asarray(list(returns), dtype=np.float64)
    if arr.size == 0:
        return arr
    return arr[np.isfinite(arr)]


def _sharpe(arr: np.ndarray) -> float:
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
    """Estimate the Probability of Backtest Overfitting for a single strategy.

    The PDF CSCV framework is defined for a *matrix* of strategy returns
    (many trials). For a single return stream we form a pseudo-panel by
    block-bootstrap resampling of the observed returns, then run CSCV on the
    resampled panel. The resulting PBO is the fraction of leave-one-block-out
    folds where the in-sample-best pseudo-strategy ranks in the bottom half
    out-of-sample.

    Returns ``{"pbo": float, "num_blocks": int, "folds": int, "lambda": float}``.
    PBO near 0.5 means no overfitting signal (in-sample rank carries no
    out-of-sample information); PBO near 1.0 means severe overfitting.
    """
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

    pseudo_strategies = []
    for _ in range(num_blocks):
        idx = rng.integers(0, len(blocks))
        pseudo_strategies.append(blocks[int(idx)])

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
        out_sample_idx = holdout
        if not in_sample_idx:
            continue
        in_sample_sharpes = np.array([_sharpe(panel[j]) for j in in_sample_idx])
        best_local = int(np.argmax(in_sample_sharpes))
        best_strategy_global_idx = in_sample_idx[best_local]
        out_sample_sharpe = _sharpe(panel[out_sample_idx])
        all_out_sharpes = np.array([_sharpe(panel[j]) for j in range(num_strategies)])
        if all_out_sharpes.std() <= 0.0:
            continue
        ranks = np.argsort(np.argsort(all_out_sharpes))
        rank_of_best = ranks[best_strategy_global_idx]
        percentile = rank_of_best / max(num_strategies - 1, 1)
        ranks_out_of_sample[holdout] = percentile
        if percentile <= 0.5:
            count_worst += 1
        folds += 1

    if folds == 0:
        return {"pbo": 0.5, "num_blocks": num_blocks, "folds": 0, "lambda": 0.5}
    pbo = count_worst / folds
    lam = float(np.mean(ranks_out_of_sample[ranks_out_of_sample > 0])) if np.any(ranks_out_of_sample > 0) else 0.5
    return {
        "pbo": float(pbo),
        "num_blocks": int(num_blocks),
        "folds": int(folds),
        "lambda": float(lam),
    }


def cscv_pbo_panel(
    panel: Iterable[Iterable[float]],
    *,
    num_blocks: int = 16,
) -> dict:
    """CSCV PBO for a panel of strategy returns (rows = strategies, cols = time).

    This is the canonical Bailey et al. formulation when many trial return
    streams are available. Partitions the time axis into ``num_blocks`` blocks,
    leaves each block out in turn, ranks in-sample-best by Sharpe, and measures
    its out-of-sample rank.
    """
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
        in_sample_sharpes = np.array([_sharpe(in_sample[s]) for s in range(num_strategies)])
        best_strategy = int(np.argmax(in_sample_sharpes))
        out_sample_sharpes = np.array([_sharpe(out_sample[s]) for s in range(num_strategies)])
        if out_sample_sharpes.std() <= 0.0:
            continue
        ranks = np.argsort(np.argsort(out_sample_sharpes))
        rank_of_best = ranks[best_strategy]
        percentile = rank_of_best / max(num_strategies - 1, 1)
        ranks_out_of_sample.append(percentile)
        if percentile <= 0.5:
            count_worst += 1
        folds += 1

    if folds == 0:
        return {"pbo": 0.5, "num_blocks": num_blocks, "folds": 0, "lambda": 0.5}
    pbo = count_worst / folds
    arr_ranks = np.array(ranks_out_of_sample)
    lam = float(np.mean(arr_ranks[arr_ranks > 0])) if np.any(arr_ranks > 0) else 0.5
    return {
        "pbo": float(pbo),
        "num_blocks": int(num_blocks),
        "folds": int(folds),
        "lambda": float(lam),
    }


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
    """Walk-forward in-sample vs out-of-sample Sharpe comparison.

    Returns JSON-serialisable dict. In-sample Sharpe materially exceeding
    out-of-sample Sharpe is an overfitting indicator.
    """
    arr = _as_returns(returns)
    if arr.size < 4:
        return {"in_sample_sharpe": 0.0, "out_sample_sharpe": 0.0, "degradation": 0.0, "overfit_flag": False}
    train, test = walk_forward_split(arr, train_fraction=train_fraction)
    if train.size < 2 or test.size < 2:
        return {"in_sample_sharpe": 0.0, "out_sample_sharpe": 0.0, "degradation": 0.0, "overfit_flag": False}
    in_sr = _sharpe(train)
    out_sr = _sharpe(test)
    degradation = in_sr - out_sr if in_sr > 0.0 else 0.0
    overfit = bool(in_sr > 0.0 and out_sr <= 0.0 and in_sr > abs(out_sr) * 2)
    return {
        "in_sample_sharpe": float(in_sr),
        "out_sample_sharpe": float(out_sr),
        "degradation": float(degradation),
        "overfit_flag": overfit,
    }