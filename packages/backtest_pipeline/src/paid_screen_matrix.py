"""VectorBT parameter-matrix screening mode (Phase 5).

This module is a performance-oriented companion to
``vectorbt_adapter._run_vectorbt_simulation``. It replaces the 256
sequential ``Portfolio.from_signals`` calls of the loop mode with a small
number of *chunked matrix* calls, where each column of the entries/exits
matrix is one parameter combination.

The module reuses the exact no-lookahead signal shift
(``_shift_signal_to_executable_bar``), holding-period exit
(``_apply_holding_period_exit``), gate-metric extraction
(``_normalise_vectorbt_stats_for_gate``), auxiliary numpy metrics
(``_compute_metrics_for_params``), walk-forward simulation
(``_simulate_walk_forward``), candidate identity (``_candidate_id``), and
budget fail-closed logic from ``vectorbt_adapter``.  The result ordering,
parameter hashes, gating, rejection, and promotion logic match the loop mode
exactly.

CRITICAL INVARIANTS preserved by this module:
- ``_shift_signal_to_executable_bar`` is applied identically per column.
- ``_apply_holding_period_exit`` is applied identically per column.
- ``sl_stop`` / ``tp_stop`` per column from the parameter grid.
- ``pf.stats(column=i)`` extracted per column (per-trial).
- Walk-forward simulation per column.
- Parameter hash computed per combination (same as loop mode).
- Candidate ID computed per combination (same as loop mode).
- Same fail-closed behavior for missing stats, Rust requirement, etc.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import replace
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

import numpy as np

# Reuse the loop-mode implementation.  Everything below calls the same
# helpers so per-trial results are byte-for-byte compatible with the loop
# mode for the same inputs.
from backtest_pipeline.src.vectorbt_adapter import (
    DEFAULT_PARAM_GRID,
    _append_budget_skipped_trials,
    _apply_holding_period_exit,
    _base_candidate_metric_values,
    _build_run_budget,
    _candidate_id,
    _compute_metrics_for_params,
    _establish_vectorbt_rust_runtime_proof,
    _grid_iter,
    _new_filter_result,
    _normalise_vectorbt_stats_for_gate,
    _ohlcv_column,
    _pretrial_rejection_id,
    _run_budget_fail_closed_reason,
    _screening_engine_metadata,
    _shift_signal_to_executable_bar,
    _simulate_walk_forward,
    _surface_stability_formula_missing,
)
from backtest_pipeline.src.promotion_gate import PromotedCandidate, RejectedCandidate
from backtest_pipeline.src.vectorbt_adapter import FilterResult, RunBudget
from backtest_pipeline.src.paid_screen_profiling import PaidScreenPerformanceCounters
from research_pipeline.types import CandidateModel

logger = logging.getLogger(__name__)

# Default chunk size for matrix Portfolio.from_signals calls.
DEFAULT_MATRIX_CHUNK_SIZE = 256
ALLOWED_MATRIX_CHUNK_SIZES = (128, 256, 512, 1024)


def _finalize_matrix_result(
    result: FilterResult,
    candidates: List[CandidateModel],
) -> FilterResult:
    if not result.feature_recipe_hash:
        promoted_recipe_hash = next(
            (
                str(prom.vectorbt_results.get("feature_recipe_hash") or "").strip()
                for prom in result.promoted
                if str(prom.vectorbt_results.get("feature_recipe_hash") or "").strip()
            ),
            "",
        )
        candidate_recipe_hash = next(
            (
                str(getattr(candidate, "feature_recipe_hash", "") or "").strip()
                for candidate in candidates
                if str(getattr(candidate, "feature_recipe_hash", "") or "").strip()
            ),
            "",
        )
        result.feature_recipe_hash = promoted_recipe_hash or candidate_recipe_hash
    return result


def _chunk_parameter_trials(
    trials: Sequence[Dict[str, Any]], chunk_size: int
) -> Iterator[List[Dict[str, Any]]]:
    """Split the flat parameter trial list into bounded chunks.

    Each yielded chunk is a contiguous slice of ``trials`` of length
    ``chunk_size`` (the final chunk may be shorter).  Chunk boundaries are
    independent of the parameter ordering — concatenating the yields
    reproduces ``trials`` exactly.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    n = len(trials)
    for start in range(0, n, chunk_size):
        yield list(trials[start : start + chunk_size])


def _param_chunk_hash(chunk: Sequence[Mapping[str, Any]]) -> str:
    """Deterministic SHA-256 hash of a parameter chunk for cache keying.

    The hash is order-sensitive (a chunk is an ordered list of trials) and
    stable across processes: parameter dicts are serialized with sorted keys
    and ``default=str`` to handle non-JSON-native values (e.g. ``None``).
    """
    payload = json.dumps(
        [dict(p) for p in chunk],
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def _build_sl_tp_arrays(
    chunk: Sequence[Mapping[str, Any]]
) -> Tuple[List[Optional[float]], List[Optional[float]]]:
    """Build per-column ``sl_stop`` / ``tp_stop`` arrays from the chunk.

    VectorBT accepts a per-column sequence for ``sl_stop`` and ``tp_stop``.
    Values are converted to the fraction form used by the loop mode
    (``pct / 100.0``).  ``None`` trials map to ``None`` so VectorBT applies no
    stop for that column.
    """
    sl_arr: List[Optional[float]] = []
    tp_arr: List[Optional[float]] = []
    for params in chunk:
        stop_loss = params.get("stop_loss_pct")
        take_profit = params.get("take_profit_pct")
        # Match loop mode's falsiness: ``stop_loss_f if stop_loss_f else None``
        # means 0.0 → None (no stop), consistent with the loop mode in
        # vectorbt_adapter._run_vectorbt_simulation.
        sl_f = float(stop_loss) / 100.0 if stop_loss else None
        tp_f = float(take_profit) / 100.0 if take_profit else None
        sl_arr.append(sl_f)
        tp_arr.append(tp_f)
    return sl_arr, tp_arr


def _sl_tp_for_portfolio(
    sl_arr: Sequence[Optional[float]],
    tp_arr: Sequence[Optional[float]],
    *,
    engine: str,
) -> Tuple[Optional[Any], Optional[Any]]:
    """Return per-column ``sl_stop`` / ``tp_stop`` as 1-D ``float64`` arrays.

    Matrix ``Portfolio.from_signals`` requires shape ``(n_cols,)`` for both
    numba and rust.  Python lists are misread as bar-length stops and trigger
    broadcast errors (e.g. ``(n_bars,)`` vs ``(n_cols,)``).
    """
    del engine  # same ndarray contract for all engines
    has_sl = any(s is not None for s in sl_arr)
    has_tp = any(t is not None for t in tp_arr)
    sl = (
        np.array(
            [np.nan if s is None else np.float64(s) for s in sl_arr],
            dtype=np.float64,
        )
        if has_sl
        else None
    )
    tp = (
        np.array(
            [np.nan if t is None else np.float64(t) for t in tp_arr],
            dtype=np.float64,
        )
        if has_tp
        else None
    )
    return sl, tp


def _apply_signal_threshold(
    raw_signal: np.ndarray, threshold: float
) -> Tuple[np.ndarray, np.ndarray]:
    """Convert a continuous hypothesis signal into entry/exit arrays."""
    entry_signal = np.where(raw_signal > threshold, 1.0, 0.0)
    exit_signal = np.where(raw_signal < -threshold, -1.0, 0.0)
    return entry_signal, exit_signal


def _normalize_chunk_size(chunk_size: int, *, production: bool = False) -> int:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if production and chunk_size not in ALLOWED_MATRIX_CHUNK_SIZES:
        raise ValueError(
            f"chunk_size must be one of {ALLOWED_MATRIX_CHUNK_SIZES}, got {chunk_size}"
        )
    return chunk_size


def run_vectorbt_simulation_matrix(
    ohlcv: np.ndarray,
    candidates: List[CandidateModel],
    parsed: Any,
    grid: Dict[str, List[Any]],
    repo_root: Path,
    signal_computer: Optional[Callable] = None,
    screening_scope: str = "pilot",
    max_total_trials: Optional[int] = None,
    run_budget: Optional[RunBudget] = None,
    chunk_size: int = DEFAULT_MATRIX_CHUNK_SIZE,
    performance_counters: PaidScreenPerformanceCounters | None = None,
    fs_v1_ctx: Any | None = None,
) -> FilterResult:
    """Run the VectorBT screen in chunked-matrix mode.

    Same signature and return type as ``vectorbt_adapter._run_vectorbt_simulation``
    (plus an optional ``chunk_size``), but each candidate's 256 parameter
    combinations are executed in a small number of matrix
    ``Portfolio.from_signals`` calls instead of 256 sequential calls.

    The per-trial results (promoted/rejected rows, parameter hashes, candidate
    IDs, gating, walk-forward, auxiliary metrics) are identical to the loop
    mode for the same inputs.
    """
    from backtest_pipeline.src.asset_class_routing import resolve_validation_path
    from backtest_pipeline.src import vectorbt_adapter

    chunk_size = _normalize_chunk_size(
        chunk_size, production=performance_counters is not None
    )
    counters = performance_counters or PaidScreenPerformanceCounters()
    counters.matrix_chunk_size = chunk_size

    signal_computer = signal_computer or vectorbt_adapter._default_signal_computer
    use_raw_signal_reuse = (
        fs_v1_ctx is not None
        or signal_computer is vectorbt_adapter._default_signal_computer
    )
    budget = run_budget or _build_run_budget(
        candidates=candidates,
        grid=grid,
        screening_scope=screening_scope,
        max_total_trials=max_total_trials,
    )

    # ---- Engine / fail-closed gate (identical to loop mode) ----------------
    engine_meta = _screening_engine_metadata(screening_scope)
    vectorbt_available = bool(engine_meta["vectorbt_available"])
    rust_required = bool(engine_meta["rust_engine_required_for_scope"])
    rust_available = bool(engine_meta["rust_engine_available"])
    rust_runtime_proof = bool(engine_meta["vectorbt_engine_runtime_proof"])
    if rust_required and rust_available and not rust_runtime_proof:
        rust_runtime_proof = _establish_vectorbt_rust_runtime_proof()
        if rust_runtime_proof:
            engine_meta = _screening_engine_metadata(screening_scope)
    if not vectorbt_available or (
        rust_required and (not rust_available or not rust_runtime_proof)
    ):
        stop_reason = "vectorbt_unavailable_fail_closed"
        if rust_required and not vectorbt_available:
            stop_reason = "rust_engine_required_unavailable_fail_closed"
        elif vectorbt_available and rust_required and not rust_available:
            stop_reason = "rust_engine_required_unavailable_fail_closed"
        elif vectorbt_available and rust_required and not rust_runtime_proof:
            stop_reason = "rust_runtime_proof_missing_fail_closed"
        result = _new_filter_result(
            backend=(
                "vectorbt_rust_unavailable"
                if vectorbt_available and rust_required
                else "vectorbt_unavailable"
            ),
            run_id=(
                f"{'vbt_rust_unavailable' if vectorbt_available and rust_required else 'vbt_unavailable'}_"
                f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
            ),
            candidates=candidates,
            grid=grid,
            trials_run=0,
            stop_reasons=[stop_reason],
            screening_scope=screening_scope,
            max_total_trials=max_total_trials,
            run_budget=budget,
        )
        for cand in candidates:
            row_id = _pretrial_rejection_id(cand, stop_reason)
            result.rejected.append(
                RejectedCandidate(
                    candidate_id=row_id,
                    hypothesis_id=cand.model_id,
                    reject_reason=stop_reason,
                    metric_values={
                        **_base_candidate_metric_values(cand),
                        "vectorbt_available": vectorbt_available,
                        "engine_parity_status": result.engine_parity_status,
                        "rust_engine_required_for_scope": result.rust_engine_required_for_scope,
                        "rust_engine_available": result.rust_engine_available,
                        "vectorbt_engine_runtime_proof": result.vectorbt_engine_runtime_proof,
                    },
                )
            )
        return _finalize_matrix_result(
            result,
            candidates,
        )

    import vectorbt as vbt  # type: ignore[no-redef]

    portfolio_engine = str(engine_meta.get("vectorbt_engine") or "numba")
    if portfolio_engine != "rust":
        portfolio_engine = "numba"
    close = _ohlcv_column(ohlcv, "close")

    result = _new_filter_result(
        backend="vectorbt",
        run_id=f"vbt_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        candidates=candidates,
        grid=grid,
        screening_scope=screening_scope,
        max_total_trials=max_total_trials,
        run_budget=budget,
    )

    grid_trials = list(_grid_iter(grid))[: budget.max_trials]
    trial_budget = budget.max_total_trials
    started_at = time.monotonic()
    raw_signal_cache: dict[str, np.ndarray] = {}

    def _raw_signal_for_candidate(
        cand: CandidateModel,
        trial_candidate: CandidateModel,
    ) -> np.ndarray:
        recipe_hash = str(getattr(cand, "feature_recipe_hash", "") or cand.candidate_id)
        cache_key = f"{cand.model_id}|{recipe_hash}"
        cached = raw_signal_cache.get(cache_key)
        if cached is not None:
            return cached
        if fs_v1_ctx is not None:
            from backtest_pipeline.src.fs_v1_screen_path import compute_fs_v1_raw_signal_series

            raw = compute_fs_v1_raw_signal_series(fs_v1_ctx, trial_candidate)
        else:
            raw = vectorbt_adapter.compute_raw_hypothesis_signal_series(
                trial_candidate, ohlcv, parsed, repo_root
            )
        raw_signal_cache[cache_key] = raw
        counters.raw_signal_computations += 1
        return raw

    # ---- Per-candidate matrix execution ------------------------------------
    for candidate_index, cand in enumerate(candidates):
        # Pre-chunk the parameter trials so we can apply wall-clock / trial
        # budget checks at chunk boundaries (same semantics as the loop mode,
        # which checks before each trial).
        chunks = list(_chunk_parameter_trials(grid_trials, chunk_size))

        for chunk_index, chunk in enumerate(chunks):
            # Wall-clock budget check (applied per chunk; identical behavior
            # to the loop mode's per-trial check).
            if (
                budget.max_wall_clock_seconds is not None
                and time.monotonic() - started_at >= budget.max_wall_clock_seconds
            ):
                first_param_index = chunk_index * chunk_size
                _append_budget_skipped_trials(
                    result,
                    candidates,
                    grid_trials,
                    candidate_index,
                    first_param_index,
                    reason="WALL_CLOCK_BUDGET_REACHED",
                )
                return _finalize_matrix_result(
                    result,
                    candidates,
                )
            # Trial-count budget check.
            remaining_budget = trial_budget - result.trials_run
            if remaining_budget <= 0:
                first_param_index = chunk_index * chunk_size
                _append_budget_skipped_trials(
                    result,
                    candidates,
                    grid_trials,
                    candidate_index,
                    first_param_index,
                )
                return _finalize_matrix_result(
                    result,
                    candidates,
                )

            # Build the merged param dicts + candidate IDs for this chunk
            # (identical to the loop mode's per-trial merge).  We may need to
            # trim the chunk to the remaining trial budget.
            chunk_trials: List[Tuple[Dict[str, Any], str, CandidateModel]] = []
            budget_exhausted_in_chunk = False
            for params in chunk:
                if result.trials_run >= trial_budget:
                    budget_exhausted_in_chunk = True
                    break
                merged = dict(cand.strategy_params)
                merged.update(params)
                cand_id = _candidate_id(cand, merged)
                trial_candidate = replace(cand, candidate_id=cand_id, strategy_params=merged)
                chunk_trials.append((merged, cand_id, trial_candidate))
                result.trials_run += 1

            if not chunk_trials:
                # Whole chunk exceeded budget; remaining trials were already
                # appended as skipped above.
                continue

            # --- Raw signal once per model×symbol×event×recipe; threshold per trial ---
            per_col_raw_entry: List[np.ndarray] = []
            per_col_raw_exit: List[np.ndarray] = []
            signal_failures: List[
                Tuple[Exception, Dict[str, Any], str, CandidateModel]
            ] = []
            surviving_trials: List[Tuple[Dict[str, Any], str, CandidateModel]] = []
            surviving_chunk: List[Dict[str, Any]] = []

            if use_raw_signal_reuse:
                raw_series: np.ndarray | None = None
                try:
                    raw_series = _raw_signal_for_candidate(cand, chunk_trials[0][2])
                except Exception as exc:
                    for _merged, cand_id, trial_candidate in chunk_trials:
                        signal_failures.append((exc, _merged, cand_id, trial_candidate))
                if raw_series is not None:
                    for merged, cand_id, trial_candidate in chunk_trials:
                        counters.trials_evaluated += 1
                        try:
                            threshold = float(merged.get("signal_threshold", 0.15))
                            raw_entry, raw_exit = _apply_signal_threshold(
                                raw_series, threshold
                            )
                            per_col_raw_entry.append(np.asarray(raw_entry, dtype=float))
                            per_col_raw_exit.append(np.asarray(raw_exit, dtype=float))
                            surviving_trials.append((merged, cand_id, trial_candidate))
                            surviving_chunk.append(merged)
                        except Exception as exc:
                            signal_failures.append(
                                (exc, merged, cand_id, trial_candidate)
                            )
            else:
                for merged, cand_id, trial_candidate in chunk_trials:
                    counters.trials_evaluated += 1
                    try:
                        raw_entry_signal, raw_exit_signal = signal_computer(
                            trial_candidate, ohlcv, parsed, repo_root
                        )
                        per_col_raw_entry.append(np.asarray(raw_entry_signal, dtype=float))
                        per_col_raw_exit.append(np.asarray(raw_exit_signal, dtype=float))
                        surviving_trials.append((merged, cand_id, trial_candidate))
                        surviving_chunk.append(merged)
                        counters.raw_signal_computations += 1
                    except Exception as exc:
                        logger.warning(
                            "signal computer failed for %s: %s", cand_id, exc
                        )
                        signal_failures.append(
                            (exc, merged, cand_id, trial_candidate)
                        )

            # Reject every trial whose signal computation failed (same reason
            # and metric_values as the loop mode).
            for exc, merged, cand_id, trial_candidate in signal_failures:
                result.rejected.append(
                    RejectedCandidate(
                        candidate_id=cand_id,
                        hypothesis_id=cand.model_id,
                        reject_reason="unresolvable_model_id",
                        metric_values={
                            **_base_candidate_metric_values(cand),
                            "error": str(exc),
                            "error_type": type(exc).__name__,
                            "parameter_values": dict(merged),
                            "param_values": dict(merged),
                        },
                    )
                )

            if not surviving_trials:
                # No surviving trials in this chunk.  If the budget was
                # exhausted mid-chunk, still append the remaining unprocessed
                # trials as skipped and return (same as loop mode).
                if budget_exhausted_in_chunk:
                    first_param_index = chunk_index * chunk_size + len(chunk_trials)
                    _append_budget_skipped_trials(
                        result,
                        candidates,
                        grid_trials,
                        candidate_index,
                        first_param_index,
                    )
                    return _finalize_matrix_result(
                        result,
                        candidates,
                    )
                continue

            # Build [bars, n_surviving] matrices with per-column shift +
            # holding-period exit applied identically to the loop mode.
            # The inline path is the general per-column case (each trial
            # may have a different raw signal when the signal computer
            # depends on params).
            n_bars = len(ohlcv)
            n_cols = len(surviving_chunk)
            entries_matrix = np.zeros((n_bars, n_cols), dtype=float)
            exits_matrix = np.zeros((n_bars, n_cols), dtype=float)
            for col, params in enumerate(surviving_chunk):
                holding_period = int(params.get("holding_period_bars", 15))
                entry_signal = _shift_signal_to_executable_bar(per_col_raw_entry[col])
                exit_signal = _shift_signal_to_executable_bar(per_col_raw_exit[col])
                exit_signal = _apply_holding_period_exit(
                    entry_signal, exit_signal, holding_period
                )
                entries_matrix[:, col] = entry_signal
                exits_matrix[:, col] = exit_signal

            sl_arr, tp_arr = _build_sl_tp_arrays(surviving_chunk)
            sl_stop, tp_stop = _sl_tp_for_portfolio(
                sl_arr, tp_arr, engine=portfolio_engine
            )
            for name, arr in (("sl_stop", sl_stop), ("tp_stop", tp_stop)):
                if arr is None:
                    continue
                arr_len = int(arr.shape[0]) if isinstance(arr, np.ndarray) else len(arr)
                if arr_len != n_cols:
                    raise ValueError(
                        f"{name} length {arr_len} != matrix columns {n_cols}"
                    )

            single_column = n_cols == 1
            if single_column:
                close_arg = np.asarray(close, dtype=float)
                entries_arg = entries_matrix[:, 0] > 0
                exits_arg = exits_matrix[:, 0] < 0
                sl_arg = sl_arr[0] if sl_arr else None
                tp_arg = tp_arr[0] if tp_arr else None
            else:
                close_arg = np.broadcast_to(
                    np.asarray(close, dtype=float).reshape(-1, 1),
                    (n_bars, n_cols),
                ).copy()
                entries_arg = entries_matrix > 0
                exits_arg = exits_matrix < 0
                sl_arg = sl_stop
                tp_arg = tp_stop

            # --- One matrix Portfolio.from_signals call ------------------------------
            counters.portfolio_call_count += 1
            counters.trials_per_portfolio_call.append(len(surviving_trials))
            try:
                pf = vbt.Portfolio.from_signals(
                    close_arg,
                    entries=entries_arg,
                    exits=exits_arg,
                    init_cash=10000.0,
                    freq="1min",
                    sl_stop=sl_arg,
                    tp_stop=tp_arg,
                    engine=portfolio_engine,
                )
            except Exception as exc:
                # Whole-chunk failure: reject every surviving trial with the
                # same reason as the loop mode.
                logger.warning(
                    "VectorBT matrix portfolio sim failed for %s chunk %s: %s",
                    cand.candidate_id, chunk_index, exc,
                )
                for (merged, cand_id, trial_candidate) in surviving_trials:
                    result.rejected.append(
                        RejectedCandidate(
                            candidate_id=cand_id,
                            hypothesis_id=cand.model_id,
                            reject_reason="vectorbt_simulation_failed",
                            metric_values={
                                **_base_candidate_metric_values(cand),
                                "error": str(exc),
                                "error_type": type(exc).__name__,
                                "filter_backend": result.backend,
                                "parameter_values": dict(merged),
                                "param_values": dict(merged),
                            },
                        )
                    )
                continue

            # --- Per-column stats extraction + gating (identical to loop mode) -----
            for col, (merged, cand_id, trial_candidate) in enumerate(surviving_trials):
                params = surviving_chunk[col]
                signal_thresh = float(merged.get("signal_threshold", 0.15))
                holding_period = int(merged.get("holding_period_bars", 15))
                stop_loss = merged.get("stop_loss_pct")
                take_profit = merged.get("take_profit_pct")
                stop_loss_f = float(stop_loss) if stop_loss is not None else None
                take_profit_f = float(take_profit) if take_profit is not None else None

                entry_signal = entries_matrix[:, col]
                exit_signal = exits_matrix[:, col]

                # Per-column stats via pf.stats(column=...) — bracket indexing
                # pf[:, col] fails on rust matrix portfolios whose columns are
                # a flat Index (tuple keys require MultiIndex).
                vbt_stats: Dict[str, Any] = {}
                try:
                    if single_column:
                        vbt_stats = dict(pf.stats())
                    else:
                        col_label = pf.wrapper.columns[col]
                        vbt_stats = dict(pf.stats(column=col_label))
                except Exception as exc:
                    logger.warning(
                        "VectorBT column stats failed for %s: %s", cand_id, exc
                    )
                    result.rejected.append(
                        RejectedCandidate(
                            candidate_id=cand_id,
                            hypothesis_id=cand.model_id,
                            reject_reason="vectorbt_simulation_failed",
                            metric_values={
                                **_base_candidate_metric_values(cand),
                                "error": str(exc),
                                "error_type": type(exc).__name__,
                                "filter_backend": result.backend,
                                "parameter_values": dict(merged),
                                "param_values": dict(merged),
                            },
                        )
                    )
                    continue

                gate_metrics, missing_gate_stats = _normalise_vectorbt_stats_for_gate(vbt_stats)
                if missing_gate_stats:
                    result.rejected.append(
                        RejectedCandidate(
                            candidate_id=cand_id,
                            hypothesis_id=cand.model_id,
                            reject_reason="vectorbt_stats_missing_gate_fields",
                            metric_values={
                                **_base_candidate_metric_values(cand),
                                "filter_backend": result.backend,
                                "parameter_values": dict(merged),
                                "param_values": dict(merged),
                                "vbt_stats": vbt_stats,
                                "missing_vectorbt_stats_fields": list(missing_gate_stats),
                                "gate_metric_authority": "official_vectorbt_portfolio_stats",
                            },
                        )
                    )
                    continue

                auxiliary_metrics = _compute_metrics_for_params(
                    ohlcv, entry_signal, exit_signal, stop_loss_f, take_profit_f,
                )
                wf = _simulate_walk_forward(ohlcv, entry_signal, exit_signal)

                vectorbt_results = {
                    "base_candidate_id": cand.candidate_id,
                    "base_candidate_metadata": dict(cand.metadata),
                    "signal_threshold": signal_thresh,
                    "holding_period_bars": holding_period,
                    "stop_loss_pct": stop_loss_f,
                    "take_profit_pct": take_profit_f,
                    "vbt_stats": vbt_stats,
                    "filter_backend": result.backend,
                    **gate_metrics,
                    "oos_expectancy": wf["oos_expectancy"],
                    "wf_consistency": wf["wf_consistency"],
                    "auxiliary_numpy_metrics": auxiliary_metrics,
                    "auxiliary_numpy_walk_forward": wf,
                    "surface_stability_metrics": _surface_stability_formula_missing(),
                }
                robustness_evidence = cand.metadata.get("robustness_evidence")
                if isinstance(robustness_evidence, Mapping):
                    vectorbt_results["robustness_evidence"] = copy.deepcopy(robustness_evidence)
                if getattr(cand, "feature_recipe_hash", None):
                    vectorbt_results["feature_recipe_hash"] = cand.feature_recipe_hash
                if getattr(cand, "feature_recipe", None):
                    vectorbt_results["feature_recipe"] = copy.deepcopy(cand.feature_recipe)

                candidate_path = resolve_validation_path(cand)
                promoted = PromotedCandidate(
                    candidate_id=cand_id,
                    hypothesis_id=cand.model_id,
                    strategy_family=cand.metadata.get("strategy_family", cand.model_id),
                    asset_class=candidate_path.asset_class,
                    symbol=candidate_path.symbol,
                    timeframe="1m",
                    param_values=merged,
                    vectorbt_run_id=result.run_id,
                    vectorbt_results=vectorbt_results,
                    pass_reason="vectorbt_simulated",
                    in_sample_results={"expectancy": gate_metrics.get("expectancy", 0.0)},
                    out_of_sample_results={"expectancy": wf.get("oos_expectancy", 0.0)},
                )
                result.promoted.append(promoted)

            # If the trial budget was exhausted mid-chunk, the collected
            # chunk_trials have now been processed.  Append the remaining
            # unprocessed trials (the tail of this chunk + all subsequent
            # candidates' trials) as skipped, exactly like the loop mode.
            if budget_exhausted_in_chunk:
                first_param_index = chunk_index * chunk_size + len(chunk_trials)
                _append_budget_skipped_trials(
                    result,
                    candidates,
                    grid_trials,
                    candidate_index,
                    first_param_index,
                )
                return _finalize_matrix_result(
                    result,
                    candidates,
                )

    counters.finalize_signal_reuse()
    return _finalize_matrix_result(
        result,
        candidates,
    )
