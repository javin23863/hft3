"""Tests for the long-lived paid-screen worker."""
import pytest
from backtest_pipeline.src.paid_screen_worker import PaidScreenWorker
from backtest_pipeline.src.paid_screen_types import PaidScreenUnit


def make_unit(**overrides) -> PaidScreenUnit:
    defaults = dict(
        unit_id="u1", model_id="HYP_5", hyp_id=5,
        symbol="MES.v.0", event_id="CPI_2024_09_11_TIGHT",
        event_type="CPI", thesis="test",
    )
    defaults.update(overrides)
    return PaidScreenUnit(**defaults)


class TestPaidScreenWorker:
    def test_init_sets_context(self):
        worker = PaidScreenWorker(
            repo_root=".", screening_scope="pilot",
            events_csv_hash="eh", lake_manifest_hash="lh",
        )
        assert not worker.is_initialized
        worker.init()
        assert worker.is_initialized
        ctx = worker.get_context()
        assert ctx is not None
        assert ctx.screening_scope == "pilot"
        assert ctx.events_csv_hash == "eh"

    def test_init_is_idempotent(self):
        worker = PaidScreenWorker(
            repo_root=".", screening_scope="pilot",
            events_csv_hash="eh", lake_manifest_hash="lh",
        )
        worker.init()
        worker.init()  # should not re-initialize
        assert worker.is_initialized

    def test_process_batch_without_init_raises(self):
        worker = PaidScreenWorker(
            repo_root=".", screening_scope="pilot",
            events_csv_hash="eh", lake_manifest_hash="lh",
        )
        with pytest.raises(RuntimeError, match="not initialized"):
            worker.process_batch([])

    def test_process_empty_batch(self):
        worker = PaidScreenWorker(
            repo_root=".", screening_scope="pilot",
            events_csv_hash="eh", lake_manifest_hash="lh",
        )
        worker.init()
        results = worker.process_batch([])
        assert results == []

    def test_recycle_clears_cache(self):
        worker = PaidScreenWorker(
            repo_root=".", screening_scope="pilot",
            events_csv_hash="eh", lake_manifest_hash="lh",
            max_batches_before_recycle=2,
        )
        worker.init()
        worker._data_cache.put("key", "value")
        worker._batches_processed = 2
        worker._recycle()
        assert worker._data_cache.entry_count == 0
        assert worker._batches_processed == 0

    def test_should_recycle(self):
        worker = PaidScreenWorker(
            repo_root=".", screening_scope="pilot",
            events_csv_hash="eh", lake_manifest_hash="lh",
            max_batches_before_recycle=5,
        )
        worker.init()
        assert not worker.should_recycle()
        worker._batches_processed = 5
        assert worker.should_recycle()

    def test_shutdown_clears_cache(self):
        worker = PaidScreenWorker(
            repo_root=".", screening_scope="pilot",
            events_csv_hash="eh", lake_manifest_hash="lh",
        )
        worker.init()
        worker._data_cache.put("key", "value")
        worker.shutdown()
        assert worker._data_cache.entry_count == 0

    def test_batches_processed_counter(self):
        worker = PaidScreenWorker(
            repo_root=".", screening_scope="pilot",
            events_csv_hash="eh", lake_manifest_hash="lh",
            max_batches_before_recycle=100,
        )
        worker.init()
        assert worker.batches_processed == 0
        worker.process_batch([])
        assert worker.batches_processed == 1

    def test_profiler_tracks_init_stage(self):
        worker = PaidScreenWorker(
            repo_root=".", screening_scope="pilot",
            events_csv_hash="eh", lake_manifest_hash="lh",
        )
        worker.init()
        profiler = worker.get_profiler()
        assert any(t.stage_name == "worker_init" for t in profiler.stage_timings)

    def test_cache_memory_limit_configurable(self):
        worker = PaidScreenWorker(
            repo_root=".", screening_scope="pilot",
            events_csv_hash="eh", lake_manifest_hash="lh",
            cache_memory_limit_mb=512,
            cache_max_entries=100,
        )
        worker.init()
        assert worker._data_cache.max_memory_bytes == 512 * 1024 * 1024
        assert worker._data_cache.max_entries == 100

    def test_git_commit_resolved(self):
        worker = PaidScreenWorker(
            repo_root=".", screening_scope="pilot",
            events_csv_hash="eh", lake_manifest_hash="lh",
        )
        worker.init()
        ctx = worker.get_context()
        # In test env, git may or may not be available — just check it's a string
        assert isinstance(ctx.git_commit, str)