"""Long-lived worker process for the paid-screen execution path.

Phase 3 deliverable: long-lived workers.
Removes per-unit subprocess spawning. Workers import modules once,
initialize VectorBT once, run Rust runtime proof once, and process
multiple batches with bounded caches.
"""
from __future__ import annotations

import os
import sys
import time
import json
import traceback
from typing import Optional

from backtest_pipeline.src.paid_screen_types import (
    PaidScreenUnit, WorkerContext, UnitScreeningResult,
)
from backtest_pipeline.src.paid_screen_batch import screen_paid_batch
from backtest_pipeline.src.paid_screen_profiling import RunProfiler, apply_native_thread_limits
from backtest_pipeline.src.paid_screen_cache import BoundedLRUCache
from backtest_pipeline.src.paid_screen_batch import resolve_git_commit


class PaidScreenWorker:
    """Long-lived worker process that processes multiple batches.

    Lifecycle:
    1. init() - import modules, init VectorBT, run Rust runtime proof, resolve git commit
    2. process_batch(batch) - screen a batch of compatible units
    3. recycle() - optional recycling after N batches for memory control
    4. shutdown() - cleanup
    """

    def __init__(self, repo_root: str, screening_scope: str,
                 events_csv_hash: str, lake_manifest_hash: str,
                 run_budget: dict | None = None,
                 max_batches_before_recycle: int = 100,
                 cache_memory_limit_mb: int = 4096,
                 cache_max_entries: int = 1000,
                 scratch_root: str | None = None,
                 native_threads: int = 1):
        self.repo_root = repo_root
        self.screening_scope = screening_scope
        self.events_csv_hash = events_csv_hash
        self.lake_manifest_hash = lake_manifest_hash
        self.run_budget = run_budget or {}
        self.max_batches_before_recycle = max_batches_before_recycle
        self.cache_memory_limit_mb = cache_memory_limit_mb
        self.cache_max_entries = cache_max_entries
        self.scratch_root = scratch_root
        self.native_threads = max(1, int(native_threads))
        self.native_thread_limits = apply_native_thread_limits(self.native_threads)
        self._batches_processed = 0
        self._data_cache = BoundedLRUCache(
            max_entries=cache_max_entries,
            max_memory_mb=cache_memory_limit_mb,
        )
        self._profiler = RunProfiler()
        self._git_commit: str | None = None
        self._ctx: WorkerContext | None = None
        self._initialized = False

    def init(self) -> None:
        """One-time initialization. Called once per worker process."""
        if self._initialized:
            return
        self._profiler.start_stage("worker_init")

        # Resolve git commit once (filesystem read — no subprocess per batch)
        self._git_commit = resolve_git_commit(self.repo_root)

        # Initialize VectorBT (import triggers Rust init)
        vbt_version = "unknown"
        rust_proof = False
        engine = "numba"
        try:
            import vectorbt as vbt
            vbt_version = vbt.__version__

            try:
                from backtest_pipeline.src.vectorbt_adapter import (
                    _establish_vectorbt_rust_runtime_proof,
                    _screening_engine_metadata,
                )
                meta = _screening_engine_metadata(self.screening_scope)
                rust_required = bool(meta.get("rust_engine_required_for_scope", False))
                rust_proof = bool(meta.get("vectorbt_engine_runtime_proof", False))
                if (
                    rust_required
                    and bool(meta.get("rust_engine_available", False))
                    and not rust_proof
                ):
                    rust_proof = _establish_vectorbt_rust_runtime_proof()
                    meta = _screening_engine_metadata(self.screening_scope)
                engine = meta.get("vectorbt_engine", "rust" if rust_proof else "numba")
                rust_proof = bool(meta.get("vectorbt_engine_runtime_proof", rust_proof))
            except Exception:
                engine = "numba"
                rust_proof = False
        except ImportError:
            vbt_version = "not_installed"
            engine = "unavailable"

        self._ctx = WorkerContext(
            repo_root=self.repo_root,
            git_commit=self._git_commit or "unknown",
            screening_scope=self.screening_scope,
            vectorbt_engine=engine,
            vectorbt_version=vbt_version,
            rust_runtime_proof=rust_proof,
            events_csv_hash=self.events_csv_hash,
            lake_manifest_hash=self.lake_manifest_hash,
            run_budget=self.run_budget,
        )

        self._profiler.end_stage("worker_init")
        self._initialized = True

    def process_batch(self, units: list[PaidScreenUnit]) -> list[UnitScreeningResult]:
        """Process a batch of compatible units."""
        if not self._initialized:
            raise RuntimeError("Worker not initialized — call init() first")
        if self._ctx is None:
            raise RuntimeError("Worker context is None")

        results = screen_paid_batch(
            units=units,
            context=self._ctx,
            profiler=self._profiler,
            data_cache=self._data_cache,
            scratch_root=self.scratch_root,
        )
        self._profiler.performance.native_thread_limits = dict(self.native_thread_limits)

        # The BoundedLRUCache's authoritative hit/miss counters are folded
        # into the profiler inside screen_paid_batch (delta-based, so safe
        # across multiple batches). No additional reconciliation is needed
        # here — the counters are already up to date.
        self._batches_processed += 1

        if self._batches_processed >= self.max_batches_before_recycle:
            self._recycle()

        return results

    def _recycle(self) -> None:
        """Recycle worker state for memory control.

        Clears caches and resets counters, but does NOT restart the process.
        Modules stay imported; VectorBT stays initialized.
        """
        self._data_cache.clear()
        self._batches_processed = 0

    def shutdown(self) -> None:
        """Cleanup before process exit."""
        self._data_cache.clear()

    def get_profiler(self) -> RunProfiler:
        return self._profiler

    def should_recycle(self) -> bool:
        return self._batches_processed >= self.max_batches_before_recycle

    @property
    def batches_processed(self) -> int:
        return self._batches_processed

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    def get_context(self) -> WorkerContext | None:
        return self._ctx


def worker_process_main(worker_args: dict, batch_queue, result_queue):
    """Entry point for a worker process via multiprocessing.

    Args:
        worker_args: dict with repo_root, screening_scope, events_csv_hash,
                     lake_manifest_hash, and optional run_budget,
                     max_batches_before_recycle, cache_memory_limit_mb
        batch_queue: multiprocessing queue of (batch_id, list[PaidScreenUnit])
        result_queue: multiprocessing queue of (batch_id, results, profiler_summary)
    """
    worker_args = dict(worker_args)
    for key in ("HFT3_NPZ_ROOT", "HFT3_MANIFEST_PATH"):
        val = worker_args.pop(key, None)
        if val:
            os.environ[key] = str(val)

    worker = PaidScreenWorker(**worker_args)
    worker.init()
    worker.get_profiler().performance.native_thread_limits = dict(worker.native_thread_limits)

    while True:
        batch = batch_queue.get()
        if batch is None:
            worker.shutdown()
            break

        batch_id, units = batch
        try:
            results = worker.process_batch(units)
            profiler_summary = worker.get_profiler().manifest_summary()
            result_queue.put((batch_id, results, profiler_summary))
        except Exception as e:
            worker.get_profiler().record_failure(
                "batch_processing", e, f"batch_{batch_id}")
            result_queue.put((
                batch_id,
                [],
                worker.get_profiler().manifest_summary(),
            ))
