# DATA_LAKE.md — Event-Window MBO Lake

Version: 2026-06-10.

---

## 1. Event Universe

Source: `packages/data_system/config/events.csv` (55 data rows, 1 header).

| Type | Count | Date range | Release time |
|------|-------|------------|--------------|
| NFP | 33 | 2018-01-05 – 2025-12-05 | 08:30 ET |
| CPI | 19 | 2018-01-11 – 2025-12-11 | 08:30 ET |
| PROP_FLATTEN_TOPSTEP | 3 | 2023-09-15, 2024-09-18, 2025-06-20 | 15:10 CT |

Window offsets (from events.csv):
- NFP/CPI: `start_offset_seconds=-30`, `end_offset_seconds=300` (tight window).
- PROP_FLATTEN_TOPSTEP: `start_offset_seconds=-1500`, `end_offset_seconds=600`
  (MAIN window).

Priority: 50 for all events. Source: BLS (NFP/CPI), TOPSTEP
(PROP_FLATTEN_TOPSTEP). `row_status=SOURCED` on all rows.

---

## 2. Symbol Set

Per events.csv `symbols` column (same across all events):

```
MES.v.0, MNQ.v.0, ES.v.0, NQ.v.0, ZN.v.0, ZB.v.0, RTY.v.0
```

Primary CME micro futures for replay: MES.v.0 (primary), ES.v.0, NQ.v.0,
MNQ.v.0, ZN.v.0. ZB.v.0 and RTY.v.0 are in the event corpus but not the
primary replay basket.

Cross-asset families in the feature engine (slots 16–20 hypotheses):
ES/MES, NQ/MNQ, ZN/ZB pairs; multi-symbol MBO feeds required.

---

## 3. File Naming and Resolution

Source: `packages/data_system/src/npz_resolver.py`.

Canonical path: `data/npz/{symbol}_{event_id}_mbo.npz`

Function `npz_path_for(repo_root, event_id, symbol)` constructs this path.

Fallback order when requested symbol absent:
`PDF_PRIMARY_FALLBACK_ORDER = ("ES.v.0", "MNQ.v.0", "NQ.v.0")`.
`resolve_npz_for_event()` iterates candidates and returns the first `.is_file()`
hit, or the primary path with `present=False` if none found.

---

## 4. Acquisition: CME Lane

Source: `packages/data_system/src/databento_client.py`
(`DatabentoResearchClient`).

- Dataset: `GLBX.MDP3`, schema: `mbo`, stype: `continuous`.
- `DATABENTO_API_KEY` required; raises `ValueError` if absent.
- Cost check: `metadata.get_cost()` called before any download; `BudgetManager`
  enforces hard limit and operating cap.
- `override_operating_cap=False` (default): operating cap enforced; hard limit
  also checked when `override_hard_limit=False`.
- Download via `timeseries.get_range()` → raw `.dbn.zst`.
- Manifest: `DatabentoResearchClient._record_manifest()` appends to
  `data/manifest.parquet` (not `data/npz/manifest.json`).

Conversion: `packages/backtest_pipeline/src/converter.py`
(`DatabentoConverter.convert_file`).

NOTE: `data/npz/manifest.json` does not exist in the current repo state.
The system brief references it as a target artifact; it is a TODO for the
`build_event_lake.py` script (Phase 0).

---

## 5. Acquisition: Crypto Lane

Sources in `packages/crypto_lane/src/data_io/`:

| Exchange | Recorder | Converter | Data type |
|----------|----------|-----------|-----------|
| Kraken | `kraken_l3_recorder.py` | `kraken_l3_converter.py` | L3 (true MBO) |
| Coinbase | `coinbase_mbo_recorder.py` | `coinbase_mbo_converter.py` | MBO |
| Bitfinex | `bitfinex_mbo_recorder.py` | `bitfinex_mbo_converter.py` | MBO (R0) |
| Binance | `binance_l2_recorder.py` | `binance_l2_converter.py` | L2 aggregate — NOT MBO |

Binance is L2 aggregate only; it is not an MBO source and is not used for
true-MBO replay. Kraken, Coinbase, and Bitfinex provide true L3/MBO feeds.

Recording is session-based (scheduled sessions, not continuous).
Data is stored under the crypto_lane data root
(`crypto_lane/src/ingest/paths.py` `data_root()`).

Crypto replay uses `packages/backtest_pipeline/src/crypto_hft_builder.py`
with exchange-specific builders (`build_kraken_hftbacktest`,
`build_coinbase_hftbacktest`, `build_bitfinex_hftbacktest`,
`build_binance_hftbacktest`) that apply per-exchange fee models and queue
models (L3FifoQueueModel for true-MBO exchanges; SquareProbQueueModel for
Binance L2).

---

## 6. Scope Boundary

Continuous multi-year tape is explicitly deferred. The event-window lake
(55 events × up to 7 symbols per event) must demonstrate edge before
continuous data acquisition is justified. No continuous-tape pipeline exists
in the current codebase.
