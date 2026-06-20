"""Bounded content-addressed cache for the paid-screen execution path.

Phase 4 deliverable: data and feature caching.
Caches deterministic intermediate products (NPZ→bars→features→signals→VBT results).
Every cache entry is keyed by the content and configuration that determine its value.
Cache invalidation is implicit: a different key is computed when any source hash changes.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field
import dataclasses as dc
from typing import Any, Optional


@dataclass
class CacheEntry:
    """One cached intermediate product."""
    key: str
    value: Any
    created_ts: float
    size_bytes: int
    source_hashes: dict[str, str]


class BoundedLRUCache:
    """Bounded LRU cache with content-addressed keys.

    Cache hits and misses are observable via the hit_count and miss_count
    fields. The cache evicts least-recently-used entries when the size
    limit is exceeded.

    Cache invalidation is implicit: a different key is computed when any
    source hash changes, so stale entries are simply never looked up.
    """

    def __init__(self, max_entries: int = 1000, max_memory_mb: int = 4096):
        self._store: OrderedDict[str, CacheEntry] = OrderedDict()
        self.max_entries = max_entries
        self.max_memory_bytes = max_memory_mb * 1024 * 1024
        self._current_bytes = 0
        self.hit_count = 0
        self.miss_count = 0
        self.oversized_reject_count = 0

    def get(self, key: str) -> Any | None:
        if key in self._store:
            self._store.move_to_end(key)
            self.hit_count += 1
            return self._store[key].value
        self.miss_count += 1
        return None

    def put(self, key: str, value: Any,
            source_hashes: dict[str, str] | None = None) -> None:
        size = _estimate_size_bytes(value)

        if size > self.max_memory_bytes:
            self.oversized_reject_count += 1
            return

        if key in self._store:
            evicted = self._store.pop(key)
            self._current_bytes -= evicted.size_bytes

        # Evict if needed
        while (self._current_bytes + size > self.max_memory_bytes
               or len(self._store) >= self.max_entries):
            if not self._store:
                break
            _, evicted = self._store.popitem(last=False)
            self._current_bytes -= evicted.size_bytes

        self._store[key] = CacheEntry(
            key=key, value=value,
            created_ts=time.time(),
            size_bytes=size,
            source_hashes=source_hashes or {},
        )
        self._current_bytes += size

    def clear(self) -> None:
        self._store.clear()
        self._current_bytes = 0

    def invalidate(self, key: str) -> bool:
        if key in self._store:
            entry = self._store.pop(key)
            self._current_bytes -= entry.size_bytes
            return True
        return False

    @property
    def hit_rate(self) -> float:
        total = self.hit_count + self.miss_count
        return self.hit_count / max(total, 1)

    @property
    def entry_count(self) -> int:
        return len(self._store)

    @property
    def memory_usage_bytes(self) -> int:
        return self._current_bytes


def _estimate_size_bytes(value: Any, _seen: set[int] | None = None) -> int:
    """Estimate memory size of a cached value (recursive for nested buffers)."""
    if _seen is None:
        _seen = set()
    obj_id = id(value)
    if obj_id in _seen:
        return 0
    _seen.add(obj_id)

    nbytes = getattr(value, "nbytes", None)
    if isinstance(nbytes, int) and not isinstance(value, (str, bytes, bytearray)):
        return int(nbytes)

    if dc.is_dataclass(value) and not isinstance(value, type):
        return sum(_estimate_size_bytes(getattr(value, f.name), _seen) for f in dc.fields(value))

    if isinstance(value, dict):
        return sum(_estimate_size_bytes(v, _seen) for v in value.values())

    if isinstance(value, (list, tuple, set, frozenset)):
        return sum(_estimate_size_bytes(v, _seen) for v in value)

    obj_dict = getattr(value, "__dict__", None)
    if isinstance(obj_dict, dict) and obj_dict:
        return sum(_estimate_size_bytes(v, _seen) for v in obj_dict.values())

    try:
        import sys

        return sys.getsizeof(value)
    except Exception:
        return 1024


def compute_npz_cache_key(npz_path: str, data_manifest_hash: str) -> str:
    """Cache key for NPZ source -> normalized event array."""
    payload = json.dumps({
        "npz_path": os.path.basename(npz_path),
        "data_manifest_hash": data_manifest_hash,
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def compute_bar_cache_key(npz_cache_key: str, bar_construction_id: str) -> str:
    """Cache key for normalized events -> OHLCV bars."""
    payload = json.dumps({
        "npz_cache_key": npz_cache_key,
        "bar_construction_id": bar_construction_id,
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def compute_feature_cache_key(bar_cache_key: str, feature_set_hash: str) -> str:
    """Cache key for OHLCV bars + feature config -> feature plane."""
    payload = json.dumps({
        "bar_cache_key": bar_cache_key,
        "feature_set_hash": feature_set_hash,
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def compute_signal_cache_key(feature_cache_key: str, model_id: str,
                               signal_implementation_hash: str) -> str:
    """Cache key for feature plane + model -> raw signals."""
    payload = json.dumps({
        "feature_cache_key": feature_cache_key,
        "model_id": model_id,
        "signal_implementation_hash": signal_implementation_hash,
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def compute_vbt_result_cache_key(signal_cache_key: str, param_chunk_hash: str,
                                  split_scheme_id: str, fees_model_id: str,
                                  slippage_model_id: str,
                                  vectorbt_version: str,
                                  vectorbt_engine: str) -> str:
    """Cache key for raw signals + parameter chunk -> VBT result matrix."""
    payload = json.dumps({
        "signal_cache_key": signal_cache_key,
        "param_chunk_hash": param_chunk_hash,
        "split_scheme_id": split_scheme_id,
        "fees_model_id": fees_model_id,
        "slippage_model_id": slippage_model_id,
        "vectorbt_version": vectorbt_version,
        "vectorbt_engine": vectorbt_engine,
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:32]