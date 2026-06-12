# DATA_LAKE.md — HFT3 Data Lake

Version: 2026-06-12.

---

## 1. Canonical Lake — `C:\hft3-lake`

The canonical dev-box lake lives at **`C:\hft3-lake`** (NOT inside any repo
clone, and NOT the retired `C:\Users\MSI\Documents\New project\data\npz`
location cited by earlier versions of this doc).

It is wired up entirely through environment variables — never hardcode the
lake path in code:

| Env var | Canonical value | Resolved by |
|---------|-----------------|-------------|
| `HFT3_NPZ_ROOT` | `C:\hft3-lake\npz` | `packages/data_system/src/npz_resolver.py::npz_root()` |
| `HFT3_FEATURE_ROOT` | `C:\hft3-lake\features` | `packages/data_system/src/feature_store.py::feature_store_root()` |
| `HFT3_MANIFEST_PATH` | `C:\hft3-lake\manifest.parquet` | `DatabentoResearchClient.manifest_path`, `manifest_io.default_manifest_path()` |

The **lake root** is derived as the parent of `HFT3_NPZ_ROOT`
(`npz_resolver.lake_root()`); every lane resolves as `<lake>/<lane>` with a
repo-relative `<repo>/data/<lane>` fallback when the env vars are unset.

### Scale (as of 2026-06)

- **~37,500 NPZ files**, **20 symbols**, events spanning **2018–2026**.

### Directory layout

```
C:\hft3-lake\
├── mbo_release\          # PRIMARY raw slots, one dir per {EVENT_ID}\{SYMBOL}:
│   │                     #   raw.dbn.zst              (vendor raw, billing-grade)
│   │                     #   validation.json          (stream validation report)
│   │                     #   hashes.json              (content hashes)
│   │                     #   release_event_path.json  (slot manifest / provenance)
│   └── ...               # events.jsonl in slots is RE-DERIVABLE from raw.dbn.zst
│                         # and is scheduled for purge — do not depend on it.
├── npz\                  # derived event-window NPZ: {SYMBOL}_{EVENT_ID}_mbo.npz
│                         # fixed hftbacktest event_dtype — replay-ready
├── features\             # fs_v1 derived feature store — fully rebuildable
├── raw\databento_mbo\    # vendor DBN batch downloads (pre-slot staging)
├── manifest.parquet      # SPEND LEDGER (see §4)
└── side lanes:
    ├── crypto\           # crypto venue captures/conversions
    ├── equities\         # equities lane (decadal MBO etc.)
    ├── options\          # options-on-futures / OPRA pulls
    ├── vix_options\      # VIX.OPT cmbp-1 raw (legacy layout)
    ├── sensors\          # derived event sensor parquet ({EVENT_ID}_sensors.parquet)
    ├── replay\           # replay exports
    └── normalized\       # normalized intermediate stores
```

Data-class rules:

- `mbo_release/raw.dbn.zst` is **billing-grade source of truth** — never delete
  without confirming the B2 archive copy (§2).
- `npz/`, `features/`, `sensors/` are **derived and rebuildable** from
  `mbo_release` raws (`scripts/derive_missing_npz.py`,
  `scripts/build_feature_store.py`, `scripts/derive_event_sensors.py`).
- `mbo_release/*/*/events.jsonl` is re-derivable from `raw.dbn.zst` and is
  **scheduled for purge** to reclaim space.

---

## 2. Three-Tier Architecture

| Tier | Location | Role |
|------|----------|------|
| Dev box | `C:\hft3-lake` | Working set: raw slots + derived NPZ/features for local research |
| CHI404 | `/root/hft3/data` | Live capture + NPZ mirror used by gauntlet jobs on the colo box |
| B2 bucket `hft3-lake` | Backblaze B2 | **Permanent archive of everything**: raws, npz, features, capture zstd, ledgers |

- The dev box holds the active working set; it is allowed to prune derived
  artifacts because B2 keeps the permanent copy.
- CHI404 captures market data on-site and mirrors the NPZ subset that gauntlet
  jobs need; it is not the system of record.
- B2 is the system of record. Anything deleted locally (including the
  events.jsonl purge in §1) must already exist in B2.

---

## 3. `HFT3_NPZ_ROOT` Mechanism

The NPZ lake root is resolved by `packages/data_system/src/npz_resolver.py`:

```python
def npz_root(repo_root: Path) -> Path:
    override = os.environ.get("HFT3_NPZ_ROOT", "").strip()
    if override:
        return Path(override)
    return repo_root / "data" / "npz"

def lake_root(repo_root: Path) -> Path:
    override = os.environ.get("HFT3_NPZ_ROOT", "").strip()
    if override:
        return Path(override).parent
    return repo_root / "data"
```

- **Default**: `<repo>/data/npz` — empty for new clones (gitignored).
- **Override**: `HFT3_NPZ_ROOT=C:\hft3-lake\npz` points all consumers at the
  external lake. `lake_root()` (its parent) anchors the sibling lanes:
  `mbo_release_lane.storage.mbo_release_root()` → `<lake>/mbo_release`,
  `feature_store_root()` → `<lake>/features` (unless `HFT3_FEATURE_ROOT` set),
  sensor resolution → `<lake>/sensors`.
- `data_system.src.event_data_resolver` exposes the unified search order
  (lake first, repo-relative fallback) for NPZ, raw slots, and sensors.

Pool workers inherit `os.environ`, so the override is visible in spawned
children without explicit propagation.

---

## 4. Manifests and Ledgers

Two distinct manifests exist — do not confuse them:

### 4.1 Spend ledger — `manifest.parquet` (`HFT3_MANIFEST_PATH`)

Canonical: `C:\hft3-lake\manifest.parquet`. One row per billed Databento
download (`event_id`, symbols, window, `cost`, `output_path`, `dataset`,
`schema`, `download_time`, ...). It is the budget-gating input for
`BudgetManager` and the audit trail for paid data.

- Appends go through `packages/data_system/src/manifest_io.py::append_manifest_record`
  — atomic (file lock + tmp-file `os.replace`), cross-process safe on Windows
  and POSIX. `DatabentoResearchClient._record_manifest` uses it.
- `scripts/rebuild_local_manifest.py` backfills ledger rows from existing
  `mbo_release` raw slots; `scripts/merge_manifest_ledgers.py` merges stray
  per-checkout ledgers into the canonical one.

### 4.2 NPZ lake index — `<npz_root>/manifest.json`

JSON array of verified NPZ records (`event_id`, `symbol`, `npz_path`,
`event_count`, `sha256`, `created_utc`). Maintained by
`packages/data_system/src/lake_manifest.py`:
- `manifest_path(repo_root)` → `npz_root(repo_root) / "manifest.json"`
- `load_manifest(repo_root)` → `list[dict]`; empty list when absent
- `resolve_npz_path(repo_root, npz_path_str)` → absolute `Path`

Rebuild with `python scripts/build_event_lake.py --manifest-only`.

---

## 5. File Naming and Resolution

Source: `packages/data_system/src/npz_resolver.py`.

- Canonical NPZ name: `{SYMBOL}_{EVENT_ID}_mbo.npz` (`npz_filename()`).
- `npz_path_for(repo_root, event_id, symbol)` builds the path under the
  resolved root; NPZ payload uses the fixed hftbacktest `event_dtype`.
- Raw slot path: `<lake>/mbo_release/{EVENT_ID}/{SYMBOL}/raw.dbn.zst`
  (`mbo_release_lane.storage.release_slot_dir`).
- Symbol fallback when the requested symbol is absent:
  `PDF_PRIMARY_FALLBACK_ORDER = ("ES.v.0", "MNQ.v.0", "NQ.v.0")`;
  `resolve_npz_for_event()` returns the first hit or the primary path with
  `present=False`.

---

## 6. Acquisition: CME MBO Release Lane

Sources: `packages/data_system/src/databento_client.py`
(`DatabentoResearchClient`) + `packages/mbo_release_lane/` (download
orchestration, import pipeline, validation, NPZ/sensor derivation).

- Dataset `GLBX.MDP3`, schema `mbo`, stype `continuous` (schema/stype are
  validated fail-closed against `_SUPPORTED_SCHEMAS` / `_SUPPORTED_STYPES`).
- `DATABENTO_API_KEY` required; cost check via `metadata.get_cost()` before
  any download; `BudgetManager` enforces hard limit and operating cap; every
  billed pull is appended to the spend ledger (§4.1).
- Source priority: Rithmic API first when entitled (`source_priority.py`),
  Databento fallback.
- Conversion to NPZ: `packages/backtest_pipeline/src/converter.py`
  (`DatabentoConverter.convert_file`), orchestrated by
  `mbo_release_lane/npz_adapter.py` and `scripts/derive_missing_npz.py`.
- VIX.OPT cmbp-1 sensor derivation: `mbo_release_lane/sensor_adapter.py` and
  `scripts/derive_event_sensors.py` → `<lake>/sensors`.

Databento purchases are only warranted for event windows with no existing raw
slot or NPZ in the lake.

---

## 7. Acquisition: Side Lanes

The crypto and equities lane acquisition code moved to the
**hft3-crypto-lane** and **hft3-equities-lane** repos (split tag
`pre-lane-split-20260612`); their existing data lanes in `<lake>` are
unchanged and remain where they are.

Options (`packages/options_lane/`, CME futures options — stays in this repo)
writes into its `<lake>` side dir; CHI404 capture lands as zstd batches that
are archived to B2 and selectively mirrored locally.

---

## 8. Symbol Set

Core CME symbol universe (events.csv `symbols` column /
`economic_event_universe.registry.default_cme_symbols()`):

```
MES.v.0, MNQ.v.0, ES.v.0, NQ.v.0, ZN.v.0, ZB.v.0, RTY.v.0
```

The lake's 20 symbols extend this core with side-lane instruments (crypto,
equities, options, VIX.OPT). Cross-asset families in the feature engine
(slots 16–20 hypotheses): ES/MES, NQ/MNQ, ZN/ZB pairs; multi-symbol MBO feeds
required.

---

## 9. Scope Boundary

The event-window lake is the primary data source: ~37.5k NPZs across 20
symbols and 2018–2026 provide the sample size to evaluate edge hypotheses
without continuous-tape acquisition, which remains explicitly deferred.

The events.csv universe and the 30 release-calendar CSVs
(`packages/data_system/config/release_calendars/`) define the candidate event
types. Databento spend is targeted exclusively at genuinely missing windows,
gated by the spend ledger (§4.1).
