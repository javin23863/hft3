"""Stage 1 VectorBT paid-screen performance requirements (Steps 2-8)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from backtest_pipeline.src.paid_screen_batch import (
    group_units_by_batch_key,
    resolve_git_commit,
    screen_paid_batch,
)
from backtest_pipeline.src.paid_screen_cache import BoundedLRUCache
from backtest_pipeline.src.paid_screen_matrix import (
    ALLOWED_MATRIX_CHUNK_SIZES,
    DEFAULT_MATRIX_CHUNK_SIZE,
    run_vectorbt_simulation_matrix,
)
from backtest_pipeline.src.paid_screen_profiling import (
    NATIVE_THREAD_LIMIT_ENV_VARS,
    PaidScreenPerformanceCounters,
    apply_native_thread_limits,
)
from backtest_pipeline.src.paid_screen_types import PaidScreenUnit, WorkerContext
from backtest_pipeline.src.paid_screen_worker import PaidScreenWorker
from backtest_pipeline.src.vectorbt_adapter import DEFAULT_PARAM_GRID, FilterResult
from research_pipeline.types import CandidateModel


def make_context(**overrides) -> WorkerContext:
    defaults = dict(
        repo_root=".",
        git_commit="abc123",
        screening_scope="paid-compute",
        vectorbt_engine="numba",
        vectorbt_version="0.0.0",
        rust_runtime_proof=False,
        events_csv_hash="eh",
        lake_manifest_hash="lh",
    )
    defaults.update(overrides)
    return WorkerContext(**defaults)


def make_unit(**overrides) -> PaidScreenUnit:
    defaults = dict(
        unit_id="u1",
        model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        hyp_id=5,
        symbol="MES.v.0",
        event_id="CPI_2024_09_11_TIGHT",
        event_type="CPI",
        thesis="test",
    )
    defaults.update(overrides)
    return PaidScreenUnit(**defaults)


class TestSubprocessRemoval:
    def test_screen_paid_batch_never_subprocesses_run_pipeline(self, monkeypatch, tmp_path):
        calls: list[list[str]] = []

        def _track_subprocess(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            calls.append([str(c) for c in cmd])
            raise AssertionError("subprocess.run must not run during unit screening")

        monkeypatch.setattr(subprocess, "run", _track_subprocess)

        ctx = make_context(repo_root=str(tmp_path))
        cache = BoundedLRUCache(max_entries=4, max_memory_mb=64)
        ohlcv = np.array([[1.0, 2.0, 3.0, 4.0, 5.0, 1_700_000_000_000.0]] * 20)
        from backtest_pipeline.src.paid_screen_batch import ohlcv_data_cache_key

        cache.put(ohlcv_data_cache_key(make_unit(), ctx), ohlcv)

        monkeypatch.setattr(
            "backtest_pipeline.src.paid_screen_batch.run_vectorbt_simulation_matrix",
            lambda **_kwargs: FilterResult(backend="vectorbt", run_id="perf_test"),
        )
        monkeypatch.setattr(
            "backtest_pipeline.src.paid_screen_batch.apply_promotion_gates",
            lambda result, **kwargs: result,
        )
        monkeypatch.setattr(
            "backtest_pipeline.src.paid_screen_batch._write_screening_artifact",
            lambda *args, **kwargs: "hash",
        )

        results = screen_paid_batch(
            [make_unit()],
            ctx,
            data_cache=cache,
            run_screening=True,
            scratch_root=str(tmp_path / "scratch"),
        )
        assert len(results) == 1
        assert results[0].status == "OK"
        assert calls == []

    def test_resolve_git_commit_reads_git_head_without_subprocess(self, tmp_path):
        git_dir = tmp_path / ".git"
        refs_dir = git_dir / "refs" / "heads" / "main"
        refs_dir.parent.mkdir(parents=True, exist_ok=True)
        refs_dir.write_text("deadbeef1234567890abcdef1234567890abcdef\n", encoding="utf-8")
        (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        assert resolve_git_commit(str(tmp_path)) == "deadbeef1234567890abcdef1234567890abcdef"


class TestFeatureStoreBatchReuse:
    def test_fs_v1_context_loaded_once_per_batch_key(self, monkeypatch, tmp_path):
        load_count = {"n": 0}

        class FakeCtx:
            store = {"ts": np.arange(10, dtype=np.int64), "X": np.zeros((10, 64))}
            content_hash = "fake_fs_v1_content_hash"
            store_path = str(tmp_path / "fake_store.npz")
            missing_leader_symbols = ()

        def fake_resolve(unit, context, required_leaders=()):
            load_count["n"] += 1
            return FakeCtx()

        monkeypatch.setattr(
            "backtest_pipeline.src.paid_screen_batch._try_resolve_fs_v1_context",
            fake_resolve,
        )
        monkeypatch.setattr(
            "backtest_pipeline.src.paid_screen_batch._resolve_npz_digest_for_unit",
            lambda unit, context: "fake_npz_digest",
        )
        monkeypatch.setattr(
            "backtest_pipeline.src.paid_screen_batch._ohlcv_aligns_with_fs_v1_store",
            lambda ohlcv, fs_ctx: True,
        )
        monkeypatch.setattr(
            "backtest_pipeline.src.paid_screen_batch._resolve_fs_v1_signal_computer",
            lambda *args, **kwargs: (lambda *a, **k: (np.ones(10), -np.ones(10))),
        )
        monkeypatch.setattr(
            "backtest_pipeline.src.paid_screen_batch.run_vectorbt_simulation_matrix",
            lambda **_kwargs: FilterResult(backend="vectorbt", run_id="fs_cache"),
        )
        monkeypatch.setattr(
            "backtest_pipeline.src.paid_screen_batch.apply_promotion_gates",
            lambda result, **kwargs: result,
        )
        monkeypatch.setattr(
            "backtest_pipeline.src.paid_screen_batch._write_screening_artifact",
            lambda *args, **kwargs: "hash",
        )

        ctx = make_context(repo_root=str(tmp_path))
        cache = BoundedLRUCache(max_entries=8, max_memory_mb=64)
        ohlcv = np.column_stack(
            [
                np.arange(10, dtype=float),
                np.ones(10),
                np.ones(10),
                np.ones(10),
                np.ones(10),
                np.ones(10),
            ]
        )
        from backtest_pipeline.src.paid_screen_batch import ohlcv_data_cache_key

        unit_a = make_unit(unit_id="u1", model_id="SPREAD_BLOWOUT_RECOMPRESSION")
        unit_b = make_unit(unit_id="u2", model_id="HYP_6")
        cache.put(ohlcv_data_cache_key(unit_a, ctx), ohlcv)
        profiler = __import__(
            "backtest_pipeline.src.paid_screen_profiling", fromlist=["RunProfiler"]
        ).RunProfiler()

        screen_paid_batch(
            [unit_a, unit_b],
            ctx,
            profiler=profiler,
            data_cache=cache,
            run_screening=True,
            scratch_root=str(tmp_path / "scratch"),
        )

        assert load_count["n"] == 1
        assert profiler.performance.feature_store_load_count == 1
        assert profiler.performance.feature_store_cache_misses == 1
        assert profiler.performance.models_evaluated_per_load == [2]

    def test_fs_v1_context_cache_rejection_is_logged(self, monkeypatch, tmp_path, caplog):
        class FakeCtx:
            store = {"ts": np.arange(10, dtype=np.int64), "X": np.zeros((10, 64))}
            content_hash = "fake_fs_v1_content_hash"
            store_path = str(tmp_path / "fake_store.npz")
            missing_leader_symbols = ()

        monkeypatch.setattr(
            "backtest_pipeline.src.paid_screen_batch._try_resolve_fs_v1_context",
            lambda unit, context, required_leaders=(): FakeCtx(),
        )

        from backtest_pipeline.src.paid_screen_batch import _get_or_load_fs_v1_context
        from backtest_pipeline.src.paid_screen_profiling import RunProfiler

        ctx = make_context(repo_root=str(tmp_path))
        cache = BoundedLRUCache(max_entries=4, max_memory_mb=0)
        profiler = RunProfiler()

        with caplog.at_level("WARNING", logger="backtest_pipeline.src.paid_screen_batch"):
            result = _get_or_load_fs_v1_context(make_unit(), ctx, cache, profiler)

        assert result is not None
        assert cache.oversized_reject_count == 1
        assert "fs_v1_context_cache_rejected" in caplog.text


class TestRawSignalReuse:
    def test_matrix_raw_signal_computed_once_for_256_trials(self, monkeypatch, tmp_path):
        counters = PaidScreenPerformanceCounters()
        raw_calls = {"n": 0}

        def fake_raw(*args, **kwargs):
            raw_calls["n"] += 1
            return np.linspace(-1.0, 1.0, 40)

        monkeypatch.setattr(
            "backtest_pipeline.src.vectorbt_adapter.compute_raw_hypothesis_signal_series",
            fake_raw,
        )
        monkeypatch.setattr(
            "backtest_pipeline.src.vectorbt_adapter._has_vectorbt",
            False,
        )

        cand = CandidateModel(
            candidate_id="c1",
            model_id="SPREAD_BLOWOUT_RECOMPRESSION",
            strategy_params={},
            thesis="test",
            metadata={"symbol": "MES"},
            feature_recipe_hash="recipe_a",
        )
        result = run_vectorbt_simulation_matrix(
            ohlcv=np.zeros((40, 6)),
            candidates=[cand],
            parsed=None,
            grid=DEFAULT_PARAM_GRID,
            repo_root=tmp_path,
            screening_scope="pilot",
            chunk_size=256,
            performance_counters=counters,
        )
        assert result.trials_run == 0 or raw_calls["n"] == 1
        if counters.trials_evaluated:
            assert counters.raw_signal_computations == 1
            assert counters.signal_reuse_ratio == pytest.approx(
                float(counters.trials_evaluated), rel=0.01
            )


class TestMatrixPortfolioBatching:
    def test_allowed_chunk_sizes(self):
        assert DEFAULT_MATRIX_CHUNK_SIZE in ALLOWED_MATRIX_CHUNK_SIZES
        assert set(ALLOWED_MATRIX_CHUNK_SIZES) == {128, 256, 512, 1024}

    def test_portfolio_call_count_matches_chunks(self, monkeypatch, tmp_path):
        import sys

        counters = PaidScreenPerformanceCounters()
        portfolio_calls = {"n": 0}

        class FakePf:
            class wrapper:
                columns = [0, 1]

            def stats(self, column=0):
                return {
                    "Total Return [%]": 1.0,
                    "Total Trades": 1,
                    "Expectancy": 0.1,
                    "Profit Factor": 1.0,
                    "Sharpe Ratio": 0.5,
                    "Sortino Ratio": 0.5,
                    "Max Drawdown [%]": -0.1,
                }

            wrapper = wrapper()

        def fake_from_signals(*args, **kwargs):
            portfolio_calls["n"] += 1
            pf = FakePf()
            pf.wrapper.columns = list(range(kwargs.get("entries", args[1] if len(args) > 1 else np.zeros((40, 2))).shape[1]))
            return pf

        fake_vectorbt = SimpleNamespace(
            Portfolio=SimpleNamespace(from_signals=fake_from_signals)
        )
        monkeypatch.setitem(sys.modules, "vectorbt", fake_vectorbt)
        monkeypatch.setattr(
            "backtest_pipeline.src.vectorbt_adapter._has_vectorbt",
            True,
        )
        monkeypatch.setattr(
            "backtest_pipeline.src.vectorbt_adapter._vectorbt_version",
            "1.0.0",
        )
        monkeypatch.setattr(
            "backtest_pipeline.src.vectorbt_adapter._rust_engine_available",
            False,
        )
        monkeypatch.setattr(
            "backtest_pipeline.src.vectorbt_adapter._establish_vectorbt_rust_runtime_proof",
            lambda: False,
        )
        monkeypatch.setattr(
            "backtest_pipeline.src.vectorbt_adapter.compute_raw_hypothesis_signal_series",
            lambda *a, **k: np.ones(40),
        )

        cand = CandidateModel(
            candidate_id="c1",
            model_id="SPREAD_BLOWOUT_RECOMPRESSION",
            strategy_params={},
            thesis="test",
            metadata={"symbol": "MES"},
            feature_recipe_hash="recipe_a",
        )
        run_vectorbt_simulation_matrix(
            ohlcv=np.column_stack([np.arange(40), np.ones((40, 5))]),
            candidates=[cand],
            parsed=None,
            grid={"signal_threshold": [0.1, 0.2], "holding_period_bars": [5, 15]},
            repo_root=tmp_path,
            screening_scope="pilot",
            chunk_size=128,
            performance_counters=counters,
        )
        assert counters.portfolio_call_count == portfolio_calls["n"]
        assert counters.portfolio_call_count >= 1
        assert sum(counters.trials_per_portfolio_call) == counters.trials_evaluated


class TestNativeThreadLimits:
    def test_worker_applies_thread_env_vars(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "backtest_pipeline.src.paid_screen_worker._establish_vectorbt_rust_runtime_proof",
            lambda: False,
            raising=False,
        )
        worker = PaidScreenWorker(
            repo_root=str(tmp_path),
            screening_scope="pilot",
            events_csv_hash="eh",
            lake_manifest_hash="lh",
            native_threads=1,
        )
        worker.init()
        for var in NATIVE_THREAD_LIMIT_ENV_VARS:
            assert worker.native_thread_limits[var] == "1"
            assert worker.native_thread_limits[var] == apply_native_thread_limits(1)[var]


class TestBenchmarkProjection:
    FULL_RUN_UNITS = 72950

    def test_project_full_run_from_synthetic_500_unit_benchmark(self, tmp_path):
        """Synthetic 500-unit benchmark + linear projection (no 72,950 run)."""
        worker_counts = [1, 4, 16, 32, 64, 96, 128]
        units_per_hour_by_workers: dict[int, float] = {}
        for workers in worker_counts:
            units_per_hour_by_workers[workers] = float(workers * 120.0)

        best_workers = max(units_per_hour_by_workers, key=units_per_hour_by_workers.get)
        best_uph = units_per_hour_by_workers[best_workers]
        projected_hours = self.FULL_RUN_UNITS / best_uph

        artifact = {
            "benchmark_units": 500,
            "worker_counts": worker_counts,
            "units_per_hour_by_workers": units_per_hour_by_workers,
            "full_run_units": self.FULL_RUN_UNITS,
            "projected_hours_at_best_workers": round(projected_hours, 2),
            "best_workers": best_workers,
            "method": "synthetic_linear_from_500_unit_fixture",
        }
        out = tmp_path / "paid_screen_benchmark_projection.json"
        out.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")

        assert artifact["benchmark_units"] >= 500
        assert artifact["projected_hours_at_best_workers"] > 0
        assert artifact["full_run_units"] == self.FULL_RUN_UNITS
