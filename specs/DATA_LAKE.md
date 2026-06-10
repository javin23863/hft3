# DATA_LAKE.md — Event-Window MBO Lake

Version: 2026-06-10.

---

## 1. Discovered Lake

A 4,868-NPZ event lake lives at:

```
C:\Users\MSI\Documents\New project\data\npz
```

The lake root is relocatable via the `HFT3_NPZ_ROOT` environment variable (see §3).

### Lake summary

| Symbol | Files |
|--------|-------|
| ES.v.0  | 712 |
| MES.v.0 | 706 |
| MNQ.v.0 | 690 |
| NQ.v.0  | 690 |
| RTY.v.0 | 689 |
| ZB.v.0  | 690 |
| ZN.v.0  | 691 |
| **Total** | **4,868** |

### Event types with NPZ coverage

`CPI`, `EIA_CRUDE`, `EIA_NATGAS`, `FED_BEIGE_BOOK`, `FED_H41`, `FOMC_PRESS`,
`NFP`, `PROP_FLATTEN_TOPSTEP`, `TREASURY_AUCTION`, `UNEMPLOYMENT_CLAIMS`

Approximately 695 unique event instances × symbol combinations are represented
(varying per event type and date range).

---

## 2. Event Universe

Source: `packages/data_system/config/events.csv` (12,147 data rows, 1 header).

45 event types; all rows have `row_status=SOURCED`.

Selected types with current NPZ coverage in the external lake:

| Type | NPZ symbols | Date range |
|------|-------------|------------|
| CPI | ES, MES, MNQ, NQ, RTY, ZB, ZN | 2018-2025 |
| NFP | ES, MES, MNQ, NQ, RTY, ZB, ZN | 2018-2025 |
| UNEMPLOYMENT_CLAIMS | ES, MES, MNQ, NQ, RTY, ZB, ZN | ~weekly 2018+ |
| EIA_CRUDE | ES, MES, MNQ, NQ, RTY, ZB, ZN | ~weekly 2018+ |
| EIA_NATGAS | ES, MES, MNQ, NQ, RTY, ZB, ZN | ~weekly 2018+ |
| FED_H41 | ES, MES, MNQ, NQ, RTY, ZB, ZN | ~weekly 2018+ |
| FOMC_PRESS | ES, MES, MNQ, NQ, RTY, ZB, ZN | 2018-2025 |
| TREASURY_AUCTION | ES, MES, MNQ, NQ, RTY, ZB, ZN | 2018-2025 |
| FED_BEIGE_BOOK | ES, MES, MNQ, NQ, RTY, ZB, ZN | 2018-2025 |
| PROP_FLATTEN_TOPSTEP | ES, MES, MNQ, NQ, RTY, ZB, ZN | 2023-2025 |

Types present in events.csv but not yet in the external lake (SEED or no NPZ):
`ADP_EMPLOYMENT`, `BAKER_HUGHES_RIG`, `BUILDING_PERMITS`, `CASH_EQUITY_OPEN`,
`CONSTRUCTION_SPENDING`, `CORE_CPI`, `CORE_PCE`, `CORE_PPI`, `DURABLE_GOODS_*`,
`ECI`, `EXISTING_HOME_SALES`, `EXPORT_PRICES`, `FACTORY_ORDERS`,
`FED_BEIGE_BOOK`, `FED_SPEAKER`, `FOMC_MINUTES`, `FRIDAY_CLOSE`, `GDP_*`,
`HOUSING_STARTS`, `IMPORT_PRICES`, `INDUSTRIAL_PRODUCTION`, `ISM_*`, `JOLTS`,
`NEW_HOME_SALES`, `PCE`, `PPI`, `PRODUCTIVITY`, `PROP_REOPEN`,
`RETAIL_SALES`, `TRADE_BALANCE`, `TREASURY_REFUNDING`.
Databento purchases are only needed for these genuinely-missing windows.

Release calendars for 30 event types are in-repo:
`packages/data_system/config/release_calendars/`.

---

## 3. HFT3_NPZ_ROOT Mechanism

The NPZ lake root is resolved by `packages/data_system/src/npz_resolver.py::npz_root()`:

```python
def npz_root(repo_root: Path) -> Path:
    override = os.environ.get("HFT3_NPZ_ROOT", "").strip()
    if override:
        return Path(override)
    return repo_root / "data" / "npz"
```

- **Default**: `<repo>/data/npz` — empty for new clones (gitignored).
- **Override**: set `HFT3_NPZ_ROOT` to an absolute path pointing at the external lake.
  All downstream functions (`npz_path_for`, `scan_existing_npz`, `build_work_units`,
  `load_lake_index`) respect this override automatically.

Pool workers inherit `os.environ` so the override is visible in spawn children without
any explicit propagation.

---

## 4. Manifest

`<npz_root>/manifest.json` is a JSON array of verified NPZ records:

```json
{
    "event_id":    "CPI_2024_09_11_TIGHT",
    "symbol":      "MES.v.0",
    "npz_path":    "<absolute-or-relative path to npz>",
    "event_count": 42,
    "sha256":      "<hex>",
    "created_utc": "2026-01-01T00:00:00+00:00"
}
```

`npz_path` is **absolute** when the lake root is external (HFT3_NPZ_ROOT set);
**repo-relative** (`data/npz/...`) when under the repo clone.

`packages/data_system/src/lake_manifest.py` provides:
- `manifest_path(repo_root)` → `npz_root(repo_root) / "manifest.json"`
- `load_manifest(repo_root)` → `list[dict]`; empty list when absent
- `resolve_npz_path(repo_root, npz_path_str)` → absolute `Path`

Build / rebuild the manifest with:
```bash
python scripts/build_event_lake.py --manifest-only
```

---

## 5. File Naming and Resolution

Source: `packages/data_system/src/npz_resolver.py`.

Canonical file name: `{symbol}_{event_id}_mbo.npz`

Function `npz_path_for(repo_root, event_id, symbol)` constructs the full path
under the resolved root.

Fallback order when requested symbol is absent:
`PDF_PRIMARY_FALLBACK_ORDER = ("ES.v.0", "MNQ.v.0", "NQ.v.0")`.
`resolve_npz_for_event()` iterates candidates and returns the first `.is_file()`
hit, or the primary path with `present=False` if none found.

---

## 6. Acquisition: CME Lane (for missing windows only)

Source: `packages/data_system/src/databento_client.py`
(`DatabentoResearchClient`).

- Dataset: `GLBX.MDP3`, schema: `mbo`, stype: `continuous`.
- `DATABENTO_API_KEY` required; raises `ValueError` if absent.
- Cost check: `metadata.get_cost()` called before any download; `BudgetManager`
  enforces hard limit and operating cap.
- Databento purchases are now only warranted for event types that have
  **no NPZ files** in the external lake (see §2 above for the list).

Conversion: `packages/backtest_pipeline/src/converter.py`
(`DatabentoConverter.convert_file`).

---

## 7. Acquisition: Crypto Lane

Sources in `packages/crypto_lane/src/data_io/`:

| Exchange | Recorder | Converter | Data type |
|----------|----------|-----------|-----------|
| Kraken | `kraken_l3_recorder.py` | `kraken_l3_converter.py` | L3 (true MBO) |
| Coinbase | `coinbase_mbo_recorder.py` | `coinbase_mbo_converter.py` | MBO |
| Bitfinex | `bitfinex_mbo_recorder.py` | `bitfinex_mbo_converter.py` | MBO (R0) |
| Binance | `binance_l2_recorder.py` | `binance_l2_converter.py` | L2 aggregate — NOT MBO |

Binance is L2 aggregate only; it is not an MBO source and is not used for
true-MBO replay.

---

## 8. Symbol Set

Per events.csv `symbols` column:

```
MES.v.0, MNQ.v.0, ES.v.0, NQ.v.0, ZN.v.0, ZB.v.0, RTY.v.0
```

The external lake provides all 7 symbols.

Cross-asset families in the feature engine (slots 16–20 hypotheses):
ES/MES, NQ/MNQ, ZN/ZB pairs; multi-symbol MBO feeds required.

---

## 9. Scope Boundary

The event-window lake is the primary data source. Continuous multi-year tape
acquisition is explicitly deferred — the event-window coverage (4,868 NPZs,
~695 event instances across 7 symbols, 10 event types) provides the required
sample size to evaluate edge hypotheses before committing to continuous-tape
infrastructure.

The 12,147-row events.csv and 30 release-calendar CSVs define the full universe
of candidate event types. Databento purchases are targeted exclusively at
event types with no existing NPZ coverage.
