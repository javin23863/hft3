"""Tests for the paid-screen bounded content-addressed cache."""
import pytest
import numpy as np
from backtest_pipeline.src.paid_screen_cache import (
    BoundedLRUCache, compute_npz_cache_key, compute_bar_cache_key,
    compute_feature_cache_key, compute_signal_cache_key,
    compute_vbt_result_cache_key,
)


class TestBoundedLRUCache:
    def test_put_and_get(self):
        cache = BoundedLRUCache(max_entries=10, max_memory_mb=10)
        cache.put("key1", {"data": "value"})
        assert cache.get("key1") == {"data": "value"}
        assert cache.hit_count == 1
        assert cache.miss_count == 0

    def test_miss_on_missing_key(self):
        cache = BoundedLRUCache()
        assert cache.get("missing") is None
        assert cache.miss_count == 1

    def test_lru_eviction(self):
        cache = BoundedLRUCache(max_entries=2, max_memory_mb=10)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.get("a")
        cache.put("c", 3)
        assert cache.get("a") == 1
        assert cache.get("b") is None
        assert cache.get("c") == 3

    def test_memory_limit_eviction(self):
        cache = BoundedLRUCache(max_entries=100, max_memory_mb=1)
        big = np.zeros(70_000, dtype=np.float64)
        cache.put("big", big)
        assert cache.memory_usage_bytes > 0
        big2 = np.zeros(70_000, dtype=np.float64)
        cache.put("big2", big2)
        assert cache.get("big") is None or cache.entry_count <= 2

    def test_oversized_value_is_not_stored(self):
        cache = BoundedLRUCache(max_entries=100, max_memory_mb=1)
        max_bytes = cache.max_memory_bytes
        cache.put("small", np.zeros(1000, dtype=np.float64))
        assert cache.entry_count == 1
        assert cache.memory_usage_bytes <= max_bytes

        oversized = np.zeros(max_bytes // 8 + 1, dtype=np.float64)
        cache.put("oversized", oversized)

        assert cache.get("oversized") is None
        assert cache.entry_count == 1
        assert cache.get("small") is not None
        assert cache.memory_usage_bytes <= max_bytes
        assert cache.oversized_reject_count == 1

    def test_clear(self):
        cache = BoundedLRUCache()
        cache.put("a", 1)
        cache.clear()
        assert cache.entry_count == 0
        assert cache.get("a") is None

    def test_invalidate(self):
        cache = BoundedLRUCache()
        cache.put("a", 1)
        assert cache.invalidate("a") is True
        assert cache.get("a") is None
        assert cache.invalidate("nonexistent") is False

    def test_hit_rate(self):
        cache = BoundedLRUCache()
        cache.put("a", 1)
        cache.get("a")
        cache.get("b")
        assert cache.hit_rate == 0.5

    def test_hit_rate_no_activity(self):
        cache = BoundedLRUCache()
        assert cache.hit_rate == 0.0

    def test_overwrite_existing_key(self):
        cache = BoundedLRUCache()
        cache.put("a", 1)
        cache.put("a", 2)
        assert cache.get("a") == 2
        assert cache.hit_count == 1

    def test_entry_count(self):
        cache = BoundedLRUCache(max_entries=10)
        cache.put("a", 1)
        cache.put("b", 2)
        assert cache.entry_count == 2


class TestCacheKeyConstruction:
    def test_npz_key_includes_data_hash(self):
        k1 = compute_npz_cache_key("path.npz", "hash_a")
        k2 = compute_npz_cache_key("path.npz", "hash_b")
        assert k1 != k2

    def test_npz_key_includes_path(self):
        k1 = compute_npz_cache_key("path_a.npz", "hash_a")
        k2 = compute_npz_cache_key("path_b.npz", "hash_a")
        assert k1 != k2

    def test_bar_key_includes_bar_construction_id(self):
        k1 = compute_bar_cache_key("npz_key", "ohlcv_1m")
        k2 = compute_bar_cache_key("npz_key", "ohlcv_5m")
        assert k1 != k2

    def test_bar_key_includes_npz_key(self):
        k1 = compute_bar_cache_key("npz_a", "ohlcv_1m")
        k2 = compute_bar_cache_key("npz_b", "ohlcv_1m")
        assert k1 != k2

    def test_feature_key_includes_feature_set_hash(self):
        k1 = compute_feature_cache_key("bar_key", "fs_hash_a")
        k2 = compute_feature_cache_key("bar_key", "fs_hash_b")
        assert k1 != k2

    def test_feature_key_includes_bar_key(self):
        k1 = compute_feature_cache_key("bar_a", "fs_hash")
        k2 = compute_feature_cache_key("bar_b", "fs_hash")
        assert k1 != k2

    def test_signal_key_includes_model_and_implementation(self):
        k1 = compute_signal_cache_key("feat_key", "HYP_5", "impl_a")
        k2 = compute_signal_cache_key("feat_key", "HYP_6", "impl_a")
        k3 = compute_signal_cache_key("feat_key", "HYP_5", "impl_b")
        assert k1 != k2
        assert k1 != k3

    def test_signal_key_includes_feature_key(self):
        k1 = compute_signal_cache_key("feat_a", "HYP_5", "impl_a")
        k2 = compute_signal_cache_key("feat_b", "HYP_5", "impl_a")
        assert k1 != k2

    def test_vbt_result_key_includes_all_determinants(self):
        k1 = compute_vbt_result_cache_key("sig", "chunk_a", "wf_1", "fees_1", "slip_1", "1.0.0", "rust")
        k2 = compute_vbt_result_cache_key("sig", "chunk_b", "wf_1", "fees_1", "slip_1", "1.0.0", "rust")
        k3 = compute_vbt_result_cache_key("sig", "chunk_a", "wf_2", "fees_1", "slip_1", "1.0.0", "rust")
        k4 = compute_vbt_result_cache_key("sig", "chunk_a", "wf_1", "fees_2", "slip_1", "1.0.0", "rust")
        k5 = compute_vbt_result_cache_key("sig", "chunk_a", "wf_1", "fees_1", "slip_2", "1.0.0", "rust")
        k6 = compute_vbt_result_cache_key("sig", "chunk_a", "wf_1", "fees_1", "slip_1", "1.1.0", "rust")
        k7 = compute_vbt_result_cache_key("sig", "chunk_a", "wf_1", "fees_1", "slip_1", "1.0.0", "numba")
        assert k1 != k2  # different param chunk
        assert k1 != k3  # different split scheme
        assert k1 != k4  # different fees
        assert k1 != k5  # different slippage
        assert k1 != k6  # different vbt version
        assert k1 != k7  # different engine

    def test_keys_are_deterministic(self):
        k1 = compute_vbt_result_cache_key("sig", "chunk_a", "wf_1", "fees_1", "slip_1", "1.0.0", "rust")
        k2 = compute_vbt_result_cache_key("sig", "chunk_a", "wf_1", "fees_1", "slip_1", "1.0.0", "rust")
        assert k1 == k2

    def test_all_keys_are_hex_strings(self):
        k = compute_vbt_result_cache_key("sig", "chunk", "wf", "fees", "slip", "1.0", "rust")
        assert all(c in "0123456789abcdef" for c in k)


class TestCacheInvalidation:
    """Cache never returns stale data after source/config/code changes."""

    def test_code_change_invalidates(self):
        k1 = compute_signal_cache_key("feat_key", "HYP_5", "impl_v1")
        k2 = compute_signal_cache_key("feat_key", "HYP_5", "impl_v2")
        assert k1 != k2

    def test_data_change_invalidates(self):
        k1 = compute_npz_cache_key("path.npz", "manifest_v1")
        k2 = compute_npz_cache_key("path.npz", "manifest_v2")
        assert k1 != k2

    def test_config_change_invalidates_fees(self):
        k1 = compute_vbt_result_cache_key("sig", "chunk", "wf", "fees_v1", "slip", "1.0.0", "rust")
        k2 = compute_vbt_result_cache_key("sig", "chunk", "wf", "fees_v2", "slip", "1.0.0", "rust")
        assert k1 != k2

    def test_config_change_invalidates_slippage(self):
        k1 = compute_vbt_result_cache_key("sig", "chunk", "wf", "fees", "slip_v1", "1.0.0", "rust")
        k2 = compute_vbt_result_cache_key("sig", "chunk", "wf", "fees", "slip_v2", "1.0.0", "rust")
        assert k1 != k2

    def test_config_change_invalidates_split(self):
        k1 = compute_vbt_result_cache_key("sig", "chunk", "wf_v1", "fees", "slip", "1.0.0", "rust")
        k2 = compute_vbt_result_cache_key("sig", "chunk", "wf_v2", "fees", "slip", "1.0.0", "rust")
        assert k1 != k2

    def test_engine_change_invalidates(self):
        k1 = compute_vbt_result_cache_key("sig", "chunk", "wf", "fees", "slip", "1.0.0", "rust")
        k2 = compute_vbt_result_cache_key("sig", "chunk", "wf", "fees", "slip", "1.0.0", "numba")
        assert k1 != k2

    def test_version_change_invalidates(self):
        k1 = compute_vbt_result_cache_key("sig", "chunk", "wf", "fees", "slip", "1.0.0", "rust")
        k2 = compute_vbt_result_cache_key("sig", "chunk", "wf", "fees", "slip", "2.0.0", "rust")
        assert k1 != k2


class TestCorruptedCacheRecovery:
    def test_corrupted_entry_does_not_crash(self):
        cache = BoundedLRUCache()
        cache.put("a", None)
        result = cache.get("a")
        assert result is None

    def test_clear_and_rebuild_after_corruption(self):
        cache = BoundedLRUCache()
        cache.put("a", "stale")
        cache.clear()
        cache.put("a", "fresh")
        assert cache.get("a") == "fresh"

    def test_invalidate_then_repopulate(self):
        cache = BoundedLRUCache()
        cache.put("a", "old")
        cache.invalidate("a")
        cache.put("a", "new")
        assert cache.get("a") == "new"
class TestEstimateSizeBytes:
    def test_nested_dataclass_counts_numpy_buffers(self):
        from pathlib import Path
        from backtest_pipeline.src.fs_v1_screen_path import FsV1ScreenContext
        from backtest_pipeline.src.paid_screen_cache import _estimate_size_bytes

        ts = np.zeros(50_000, dtype=np.int64)
        X = np.zeros((50_000, 8), dtype=np.float64)
        ctx = FsV1ScreenContext(
            symbol="MES.v.0",
            event_id="EVT",
            store_path=Path("dummy.npz"),
            store={"ts": ts, "X": X},
            feature_latency_ms=1.0,
            content_hash="c",
            manifest_hash="m",
            has_vix=False,
            vix_cols=(),
            vix_ts=None,
            vix_X=None,
        )
        est = _estimate_size_bytes(ctx)
        assert est >= ts.nbytes + X.nbytes
