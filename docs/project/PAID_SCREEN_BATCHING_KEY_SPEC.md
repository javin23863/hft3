# Paid-Screen Batching-Key Specification

This specification defines `BatchingKey`, the frozen dataclass that determines
which `PaidScreenUnit`s may safely share a batch. It is the authoritative
contract for batch compatibility in the paid-screen redesign.

Source of truth: `packages/backtest_pipeline/src/paid_screen_types.py`
(`BatchingKey`), constructed by `build_batching_key` in
`packages/backtest_pipeline/src/paid_screen_batch.py`.

---

## 1. Purpose

Two units can be batched together **if and only if** their `BatchingKey`s are
equal. Every field that can change the semantics of a unit's execution is
included in the key. Omitting a semantically-relevant field from the key would
allow incompatible units to share a batch and produce wrong results; the key is
therefore the complete, minimal set of execution-defining fields.

The thesis text is **not** in the key — it is descriptive metadata only
(see `PAID_SCREEN_REDESIGN_DESIGN.md`, invariant 1).

## 2. The 15 fields

`BatchingKey` is a `@dataclass(frozen=True)` with exactly these fields, in
declared order:

| # | Field | Type | Source | Why it matters |
|---|------|------|--------|----------------|
| 1 | `symbol` | `str` | `PaidScreenUnit.symbol` | Different instruments have different OHLCV; cannot share data. |
| 2 | `event_id` | `str` | `PaidScreenUnit.event_id` | Identifies the NPZ/event slice loaded for the batch; the shared data unit. |
| 3 | `event_type` | `str` | `PaidScreenUnit.event_type` | Event semantics (e.g. scheduled vs unscheduled) affect bar construction and feature gating. |
| 4 | `data_manifest_hash` | `str` | caller-supplied to `build_batching_key` | Hash of the data manifest for the event's NPZ; a different manifest means different underlying data. |
| 5 | `lake_manifest_hash` | `str` | `WorkerContext.lake_manifest_hash` | Hash of the data-lake manifest the worker is running against; different lake → different data provenance. |
| 6 | `events_csv_hash` | `str` | `WorkerContext.events_csv_hash` | Hash of the events CSV defining the event universe; different universe → different coverage. |
| 7 | `bar_construction_id` | `str` | literal in `build_batching_key` (`"ohlcv_1m_from_npz_or_supplied_array"`) | Identifies the bar-construction recipe; different recipe → different bars → different everything downstream. |
| 8 | `feature_set_id` | `str \| None` | `PaidScreenUnit.feature_set_id` | Identifies which feature set is computed over the bars; different feature set → different feature plane. |
| 9 | `feature_set_hash` | `str` | caller-supplied | Content hash of the feature-set definition; a changed definition changes the feature plane even if the id is stable. |
| 10 | `research_clock` | `str` | literal `"scheduled_event"` | The research-clock convention used for time alignment; different convention shifts bar boundaries. |
| 11 | `split_scheme_id` | `str` | literal `"wf_2018_2024"` | The walk-forward split scheme; different splits produce different in/out-of-sample windows. |
| 12 | `fees_model_id` | `str` | literal `"cme_fees_v1"` | The fee schedule used in portfolio simulation; different fees change PnL and gating. |
| 13 | `slippage_model_id` | `str` | literal `"slip_v1"` | The slippage model; different slippage changes fills and gating. |
| 14 | `signal_implementation_hash` | `str` | caller-supplied | Content hash of the model's signal-implementation code; a changed implementation changes raw signals. |
| 15 | `model_registry_hash` | `str` | caller-supplied | Hash of the model registry; a changed registry changes model resolution semantics. |

### Field categories

- **Per-unit (1–3, 8):** come from the `PaidScreenUnit`.
- **Per-worker / per-run (5, 6):** come from the frozen `WorkerContext`.
- **Content hashes (4, 9, 14, 15):** caller-supplied; must reflect the actual
  content the batch will execute against.
- **Recipe literals (7, 10, 11, 12, 13):** currently fixed strings in
  `build_batching_key`. Changing any recipe value must change the literal so
  old and new batches are incompatible.

## 3. Equality & compatibility rules

`BatchingKey` is frozen and uses dataclass default equality: **two keys are
equal iff all 15 fields are equal.** Therefore:

1. **Rule (same symbol, same event) is necessary but not sufficient.** Units
   with the same `symbol` and `event_id` may still be incompatible if any other
   field differs (e.g. different `feature_set_id`, different
   `data_manifest_hash`, different `signal_implementation_hash`).
2. **A changed source hash always breaks compatibility.** Regenerating
   `data_manifest_hash`, `feature_set_hash`, `signal_implementation_hash`, or
   `model_registry_hash` against changed content produces a new key, so the old
   and new units are never batched together. This is the content-addressed
   invariant: incompatible units simply never collide.
3. **`feature_set_id = None` is a real value.** A unit with `feature_set_id=None`
   and a unit with `feature_set_id="default"` have **different** keys and must
   not batch together.
4. **Recipe literals are part of the contract.** If `bar_construction_id`,
   `research_clock`, `split_scheme_id`, `fees_model_id`, or `slippage_model_id`
   is ever parameterized, the literal in `build_batching_key` must be replaced
   with the parameterized value so distinct recipes yield distinct keys.
5. **First-pass grouping is a perf optimization, not the contract.**
   `group_units_by_batch_key` groups by `f"{symbol}|{event_id}"` only; the full
   15-field comparison must still happen before shared execution.

## 4. Construction

`build_batching_key(unit, ctx, data_manifest_hash, feature_set_hash,
signal_implementation_hash, model_registry_hash) -> BatchingKey` is the single
construction entry point. The four hashes are caller-supplied because they are
content-addressed values computed outside the unit/worker (by the data audit,
feature-set loader, signal loader, and registry loader respectively).

```python
from backtest_pipeline.src.paid_screen_batch import build_batching_key
key = build_batching_key(
    unit, ctx,
    data_manifest_hash="9a3f...",
    feature_set_hash="c0b1...",
    signal_implementation_hash="7d2e...",
    model_registry_hash="11aa...",
)
```

## 5. Cache keys derived from `BatchingKey`

`BatchingKey` exposes four cache-key methods (see
`PAID_SCREEN_CACHE_SPEC.md` for the full chain):

| Method | Inputs (subset of fields) | Layer |
|--------|------------------------------|-------|
| `cache_key()` | symbol, event_id, data_manifest_hash, lake_manifest_hash, events_csv_hash, bar_construction_id | bars (data batch) |
| `feature_cache_key()` | symbol, event_id, data_manifest_hash, feature_set_id, feature_set_hash, bar_construction_id | feature plane |
| `signal_cache_key(model_id)` | symbol, event_id, model_id, data_manifest_hash, feature_set_hash, signal_implementation_hash, research_clock | raw signals |
| `vbt_result_cache_key(model_id, param_chunk_hash, vectorbt_version, vectorbt_engine)` | symbol, event_id, model_id, data_manifest_hash, feature_set_hash, signal_implementation_hash, research_clock, split_scheme_id, fees_model_id, slippage_model_id, param_chunk_hash, vectorbt_version, vectorbt_engine | VBT result matrix |

Each cache key is the sha256 of a `sort_keys=True` JSON payload of the named
fields, truncated to 32 hex chars. Adding a semantically-relevant field to a
cache key requires adding it to the `BatchingKey` first (otherwise two
incompatible units could collide in the cache).

## 6. Worked examples

### Example A — compatible units (same key)

```
unit_1: symbol=ESH4, event_id=2023-03-15_FOMC, event_type=scheduled, feature_set_id=fs_v2
unit_2: symbol=ESH4, event_id=2023-03-15_FOMC, event_type=scheduled, feature_set_id=fs_v2
# same WorkerContext, same all-hashes, same recipe literals
→ keys equal → may batch together → NPZ loaded once.
```

### Example B — incompatible: different feature_set_id

```
unit_1: symbol=ESH4, event_id=2023-03-15_FOMC, feature_set_id=fs_v2
unit_2: symbol=ESH4, event_id=2023-03-15_FOMC, feature_set_id=fs_v3
→ feature_set_id differs → keys differ → must NOT batch together.
```

### Example C — incompatible: regenerated data_manifest_hash

```
unit_1: data_manifest_hash=9a3f...  (NPZ v1)
unit_2: data_manifest_hash=b71c...  (NPZ v2, re-audited)
→ data_manifest_hash differs → keys differ → must NOT batch together,
  even though symbol and event_id match.
```

### Example D — incompatible: None vs named feature set

```
unit_1: feature_set_id=None
unit_2: feature_set_id="default"
→ keys differ (None != "default") → must NOT batch together.
```

### Example E — incompatible: different symbol

```
unit_1: symbol=ESH4
unit_2: symbol=NQH4
→ symbol differs → keys differ → must NOT batch together
  (and group_units_by_batch_key already separates them in the first pass).
```

### Example F — recipe drift (future)

If `fees_model_id` is parameterized and `build_batching_key` is changed to read
it from the context, then a worker using `cme_fees_v1` and a worker using
`cme_fees_v2` will produce different keys for otherwise-identical units, so
their VBT results are not conflated. This is the required behavior when
introducing recipe parameterization.