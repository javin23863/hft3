# Paid-Screen Cache Specification

This specification defines the cache layers, key construction, and invalidation
rules for the paid-screen execution path. The cache is **content-addressed**
and **implicitly invalidated**: a changed source hash yields a different key, so
stale entries are simply never looked up. There is no explicit eviction-on-change
path.

Sources of truth:
- `packages/backtest_pipeline/src/paid_screen_cache.py` — `BoundedLRUCache`,
  `CacheEntry`, and the five `compute_*_cache_key` functions.
- `packages/backtest_pipeline/src/paid_screen_types.py` — `BatchingKey.cache_key()`,
  `BatchingKey.feature_cache_key()`, `BatchingKey.signal_cache_key()`,
  `BatchingKey.vbt_result_cache_key()` (derived key helpers).

---

## 1. Cache container — `BoundedLRUCache`

A bounded LRU cache of content-addressed intermediate products.

- **Capacity:** `max_entries` (default 1000) and `max_memory_mb` (default 4096 MiB).
  Eviction is least-recently-used when either limit is exceeded.
- **Observable counters:** `hit_count`, `miss_count`, `hit_rate`,
  `entry_count`, `memory_usage_bytes`.
- **Backend-agnostic batch path:** `screen_paid_batch` accepts
  `dict | BoundedLRUCache | None` for `data_cache`. When a `BoundedLRUCache` is
  supplied, `screen_paid_batch` snapshots `hit_count`/`miss_count` before the
  batch and folds only the **delta** into the `RunProfiler`, so multiple batches
  against the same cache+profiler do not double-count (design invariant 9).
- **Explicit ops:** `get(key)`, `put(key, value, source_hashes=None)`,
  `invalidate(key) -> bool`, `clear()`.
- **Size estimation:** `sys.getsizeof(value)` with a 1024-byte fallback.

The LRU is the production cache; a plain `dict` is supported only for legacy
callers and tests (no eviction, no counters).

## 2. The five cache layers

The paid-screen pipeline is a deterministic chain of intermediate products. Each
layer's key incorporates the prior layer's key plus the hashes of whatever new
content/configuration determines its value. The layers, in execution order:

| Layer | Product | Key function | Inputs |
|------|---------|--------------|--------|
| 1 | NPZ → normalized event array | `compute_npz_cache_key(npz_path, data_manifest_hash)` | `npz_path` (basename only), `data_manifest_hash` |
| 2 | normalized events → OHLCV bars | `compute_bar_cache_key(npz_cache_key, bar_construction_id)` | `npz_cache_key`, `bar_construction_id` |
| 3 | OHLCV bars → feature plane | `compute_feature_cache_key(bar_cache_key, feature_set_hash)` | `bar_cache_key`, `feature_set_hash` |
| 4 | feature plane + model → raw signals | `compute_signal_cache_key(feature_cache_key, model_id, signal_implementation_hash)` | `feature_cache_key`, `model_id`, `signal_implementation_hash` |
| 5 | raw signals + params → VBT result matrix | `compute_vbt_result_cache_key(signal_cache_key, param_chunk_hash, split_scheme_id, fees_model_id, slippage_model_id, vectorbt_version, vectorbt_engine)` | `signal_cache_key`, `param_chunk_hash`, `split_scheme_id`, `fees_model_id`, `slippage_model_id`, `vectorbt_version`, `vectorbt_engine` |

Each key is `sha256(json.dumps(payload, sort_keys=True)).hexdigest()[:32]`.

### 2.1 Layer 1 — NPZ cache key

```python
compute_npz_cache_key(npz_path, data_manifest_hash)
# payload = {"npz_path": basename(npz_path), "data_manifest_hash": ...}
```
Only the **basename** of `npz_path` is used (not the full path), so the same NPZ
content at different absolute locations yields the same key.

### 2.2 Layer 2 — bar cache key

```python
compute_bar_cache_key(npz_cache_key, bar_construction_id)
# payload = {"npz_cache_key": ..., "bar_construction_id": ...}
```
Different bar-construction recipes (e.g. different aggregation, different
timeframes) produce different keys even from the same NPZ.

### 2.3 Layer 3 — feature cache key

```python
compute_feature_cache_key(bar_cache_key, feature_set_hash)
# payload = {"bar_cache_key": ..., "feature_set_hash": ...}
```
`feature_set_hash` is the content hash of the feature-set definition; a changed
definition (even with the same `feature_set_id`) yields a different key.

### 2.4 Layer 4 — signal cache key

```python
compute_signal_cache_key(feature_cache_key, model_id, signal_implementation_hash)
# payload = {"feature_cache_key": ..., "model_id": ..., "signal_implementation_hash": ...}
```
`signal_implementation_hash` is the content hash of the model's signal
implementation; a changed implementation changes raw signals even if
`model_id` is stable.

### 2.5 Layer 5 — VBT result cache key

```python
compute_vbt_result_cache_key(
    signal_cache_key, param_chunk_hash, split_scheme_id,
    fees_model_id, slippage_model_id,
    vectorbt_version, vectorbt_engine,
)
# payload = {signal_cache_key, param_chunk_hash, split_scheme_id,
#            fees_model_id, slippage_model_id, vectorbt_version, vectorbt_engine}
```
`param_chunk_hash` is `_param_chunk_hash(chunk)` from `paid_screen_matrix.py`
— an order-sensitive, stable sha256 of the chunk's parameter dicts
(`json.dumps([dict(p) for p in chunk], sort_keys=True, default=str)[:32]`).
`vectorbt_version` and `vectorbt_engine` are included so a VectorBT upgrade or
an engine switch (numba ↔ rust) invalidates cached results.

## 3. `BatchingKey`-derived cache keys

`BatchingKey` (see `PAID_SCREEN_BATCHING_KEY_SPEC.md`) provides four convenience
methods that construct keys directly from the batching fields, rather than
threading prior-layer keys through. These are the canonical keys to use when a
caller already holds a `BatchingKey`:

| Method | Inputs (subset of the 15 batching fields) | Effective layer |
|--------|---------------------------------------------|------------------|
| `cache_key()` | symbol, event_id, data_manifest_hash, lake_manifest_hash, events_csv_hash, bar_construction_id | bars (≈ Layer 2) |
| `feature_cache_key()` | symbol, event_id, data_manifest_hash, feature_set_id, feature_set_hash, bar_construction_id | feature plane (Layer 3) |
| `signal_cache_key(model_id)` | symbol, event_id, model_id, data_manifest_hash, feature_set_hash, signal_implementation_hash, research_clock | raw signals (Layer 4) |
| `vbt_result_cache_key(model_id, param_chunk_hash, vectorbt_version, vectorbt_engine)` | symbol, event_id, model_id, data_manifest_hash, feature_set_hash, signal_implementation_hash, research_clock, split_scheme_id, fees_model_id, slippage_model_id, param_chunk_hash, vectorbt_version, vectorbt_engine | VBT results (Layer 5) |

The `compute_*_cache_key` functions and the `BatchingKey.*_cache_key()` methods
are two ways to reach the same keys; the former thread prior-layer keys, the
latter derive keys from batching fields directly. Both use the same
`sha256(json.dumps(..., sort_keys=True))[:32]` construction.

## 4. Invalidation rules

1. **Implicit invalidation.** There is no `invalidate(key)`-on-change flow for
   content. A changed source hash produces a different key, so the old entry is
   never matched. Stale entries age out via LRU eviction, never via active
   invalidation.
2. **Explicit `invalidate(key)` is for corruption recovery only.** Phase 6
   (`test_paid_screen_hardening.py`) uses it to drop a known-corrupted entry.
   Production paths do not call it on content change.
3. **Every semantically-relevant input must be in the key.** Adding an input to
   a layer's computation without adding it to that layer's key would let two
   different inputs collide on the same cached value — a correctness bug. The
   review rule for a new input: *"does it change the output? If yes, it must be
   in the key."*
4. **Version/engine in the VBT key.** `vectorbt_version` and `vectorbt_engine`
   are in Layer 5's key so a VectorBT upgrade or numba↔rust switch invalidates
   cached results automatically. Do not remove them.
5. **`research_clock` in the signal key.** `BatchingKey.signal_cache_key` and
   `vbt_result_cache_key` include `research_clock` because it shifts bar
   boundaries and therefore shifts the executable signal. Keep it.
6. **NPZ key uses basename only.** Moving an NPZ file changes its absolute path
   but not its content; the key is stable across relocations. Changing the NPZ
   content changes `data_manifest_hash`, which changes the NPZ key.
7. **Order sensitivity of `param_chunk_hash`.** `_param_chunk_hash` hashes the
   ordered list of parameter dicts. Reordering a chunk produces a different hash
   and therefore a different VBT-result key — this is correct because chunk
   order is part of the matrix execution identity.

## 5. Memory & eviction

- `BoundedLRUCache` evicts LRU entries when `len(_store) >= max_entries` or when
  `_current_bytes + size > max_memory_bytes` (eviction loop runs before each
  `put`).
- `_recycle()` on `PaidScreenWorker` clears the cache after
  `max_batches_before_recycle` (default 100) for memory control **without**
  restarting the process — modules stay imported, VectorBT stays initialized.
- `CacheEntry` records `created_ts`, `size_bytes`, and `source_hashes` so a hit
  can be audited for which source hashes produced it.

## 6. Example walk-through

Run two batches against the same `(events_csv_hash, event_id)`:

```
Batch 1, unit A (model_id=M1, event_id=E1):
  npz key   = compute_npz_cache_key("E1.npz", data_manifest_hash=Hd)   → K_npz
  bar key   = compute_bar_cache_key(K_npz, "ohlcv_1m_from_npz_or_supplied_array") → K_bar
  feat key  = compute_feature_cache_key(K_bar, Hf_v2)                 → K_feat
  sig key   = compute_signal_cache_key(K_feat, "M1", Hs_M1)           → K_sig
  vbt key   = compute_vbt_result_cache_key(K_sig, chunk_hash_C1,
                "wf_2018_2024","cme_fees_v1","slip_v1","0.x","rust")   → K_vbt_A
  → miss on K_npz (load NPZ, put); miss on K_bar..K_vbt (compute, put).

Batch 2, unit B (same event_id E1, model_id=M2):
  K_npz, K_bar identical to Batch 1 → HIT (NPZ and bars reused).
  K_feat identical (same feature set) → HIT.
  K_sig = compute_signal_cache_key(K_feat, "M2", Hs_M2) → different (model_id M2) → MISS, compute.
  K_vbt_B = ... chunk_hash_C2 ... → MISS, compute.
```
Result: NPZ and bars are loaded/computed once and reused across the batch and
across batches sharing the same event; only the model-specific layers
(signals, VBT results) recompute per model/param chunk. The hit/miss deltas are
folded into the `RunProfiler` exactly once per batch.