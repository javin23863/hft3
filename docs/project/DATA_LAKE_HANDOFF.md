# Data Lake Handoff — Portable Reference for Future Repos

Written 2026-07-05, at closure of the hft3 research program. All numbers below were
measured on that date directly from `C:\hft3-lake` and this repo. This document is
self-contained: a future repo needs only this file, the lake directory (or its B2
mirror), and a Databento API key to reuse or extend the purchased data.

---

## 1. The one limitation to know before anything else

**Every purchased tape is a TIGHT window: −60 s before the scheduled release to
+10 s after.** The post-event tape ends 10 seconds after the release. Any strategy
that needs to observe the market beyond +10 s (drift, reversion, minute-scale
follow-through) **cannot be tested on this data** — it requires buying WIDE windows
(see §8).

Window definitions live in `packages/data_system/config/events.csv`
(12,148 lines = 12,147 event rows + header). Each row:

```
event_id,event_type,release_date,release_time,timezone,window_name,start_offset_seconds,end_offset_seconds,symbols,priority,source,source_url,effective_date,notes,row_status
ADP_EMPLOYMENT_2018_01_03_TIGHT,ADP_EMPLOYMENT,2018-01-03,08:15:00,America/New_York,TIGHT,-60,10,"MES.v.0,MNQ.v.0,ES.v.0,NQ.v.0,ZN.v.0,ZB.v.0,RTY.v.0",50,ADP,...,SOURCED
```

- `event_id` pattern: `{TYPE}_{YYYY_MM_DD}_TIGHT`
- Offsets: `start_offset_seconds=-60`, `end_offset_seconds=10`, relative to
  `release_time` in `timezone` (mostly `America/New_York`)
- `symbols` column defines the per-event universe (the 7-symbol core:
  MES/MNQ/ES/NQ/ZN/ZB/**RTY** — note RTY is in the event universe even though it
  was rarely the traded symbol)
- Event-type distribution (top): FACTORY_ORDERS 1,983; FED_SPEAKER 479;
  UNEMPLOYMENT_CLAIMS 422; EIA_CRUDE 418; EIA_NATGAS 417; BAKER_HUGHES_RIG 417;
  FED_H41 416; CPI 106; NFP 100; PPI 100; FOMC-family, GDP, RETAIL_SALES, etc.

## 2. Local lake inventory — `C:\hft3-lake`

Top-level (sizes per `du`, 2026-07-05):

| Path | Size | Contents |
|---|---|---|
| `npz/` | 11 GB (10.63e9 bytes), **63,446 files** | Derived hftbacktest NPZ tapes (the working set) + `manifest.json` + `catalog_quarantine.json` |
| `raw/` | 10 GB, 7,571 files | Primary Databento `.dbn.zst` raws (see §2.3) |
| `features/` | 33 GB | Derived feature store (rebuildable from npz) |
| `options/` | 16 GB | Options lane data |
| `mbo_release/` | 4.7 GB | Per-event/symbol raw slots (`{event_id}/{symbol}/raw.dbn.zst`) |
| `equities/` | 2.9 GB | Equities lane (parked lane, still synced) |
| `crypto/` | 599 MB | Crypto lane (parked lane) |
| `replay/` | 150 MB | Replay captures |
| `quarantine/` | 34 MB | Quarantined bad files |
| `sensors/` | 3.9 MB | Sensor outputs |
| `vix_options/` | 3.5 MB | VIX options (NPZ key exception, see §3) |
| `normalized/`, `capture_staging/` | ~0 | Empty/staging |
| `manifest.parquet` | 5.8 MB | **Canonical spend ledger** (see §6) |
| `npz_lake.tar` | 10.7 GB | Tar of npz/ (was staged for Vast upload; redundant with npz/) |

Also at root: `manifest.pre_merge_*.parquet` (pre-merge ledger backups, 2026-06-12)
and two `manifest.parquet.corrupt.*.bak` files — historical, keep or discard.

### 2.1 NPZ tapes per symbol root

Filename convention: `{SYMBOL}.v.0_{EVENT_ID}_mbo.npz` (e.g.
`MES.v.0_CPI_2023_06_13_TIGHT_mbo.npz`). `v.0` = Databento continuous front contract.

| Symbol | Files | Size | Symbol | Files | Size |
|---|---|---|---|---|---|
| RTY | 8,008 | 0.73 GB | ZT | 650 | 0.16 GB |
| ES | 7,993 | 1.55 GB | M2K | 428 | 0.14 GB |
| MES | 7,975 | 1.15 GB | MCL | 774 | 0.11 GB |
| ZB | 7,962 | 0.39 GB | SR3 | 395 | 0.09 GB |
| MNQ | 7,949 | 2.70 GB | YM | 291 | 0.06 GB |
| NQ | 7,947 | 1.87 GB | UB | 309 | 0.05 GB |
| ZN | 7,934 | 0.76 GB | ZQ | 653 | 0.01 GB |
| CL | 1,225 | 0.17 GB | NG | 133 | 0.01 GB |
| ZF | 678 | 0.23 GB | MGC | 575 | <0.01 GB |
| MYM | 656 | 0.23 GB | GC | 567 | <0.01 GB |
| VIX | 342 | 0.21 GB | | | |

### 2.2 Per-year coverage (tape counts, all symbols / core symbols)

All tapes by year: 2018: 7,216 · 2019: 7,363 · 2020: 7,122 · 2021: 6,791 ·
2022: 6,556 · 2023: 8,378 · 2024: 9,352 · 2025: 9,270 · 2026: 1,396.

Core 7 symbols each cover 2018–2026 at ~930–1,070 tapes/year (94 in partial 2026).
Example MES: 941/955/932/980/940/1,039/1,064/1,030/94 for 2018→2026. ES, NQ, MNQ,
ZN, ZB, RTY are within ±3% of the same profile. The non-core symbols
(CL/MCL/ZF/MYM/ZQ/ZT/MGC/GC/M2K/SR3/VIX/UB/YM/NG) are 2023+ "hot universe" adds.

### 2.3 Zero-event stub files

**2,661 NPZ files are 263-byte stubs containing a valid but empty `data` array
(0 events)** — measured by `find npz/ -size 263c`. These are windows where the
purchase returned no events (quiet symbol, early-years illiquidity, e.g.
`MES.v.0_CORE_CPI_2018_01_12_TIGHT_mbo.npz`). Loaders raise on them
(`npz_feed.load_npz_events` raises `NoOHLCVDataError`); campaign runners record
them as `data_blocker:EVENT_ARRAY_EMPTY` (Stage C run summary
`runtime/stagec1/box_pull/hbt_stagec3_a326db8f/campaign_run_summary.json` counted
1,050 such row-blocks). Filter by `event_count > 0` in the manifest before batching.

### 2.4 NPZ manifest + quarantine catalog

`C:\hft3-lake\npz\manifest.json` — 22.5 MB, **60,783 entries** (list of dicts),
one per verified NPZ. Example entry:

```json
{"event_id": "CPI_2023_06_13_TIGHT", "symbol": "CL.v.0",
 "npz_path": "C:\\hft3-lake\\npz\\CL.v.0_CPI_2023_06_13_TIGHT_mbo.npz",
 "event_count": 78275,
 "sha256": "6bdbdf3190b4768ab08fe91c0b8d3ee7e74a40f95457b8f5efed062343f98bc5",
 "created_utc": "2026-06-19T12:13:01.389656+00:00",
 "size": 841204, "mtime": 1780745803.418277}
```

`catalog_quarantine.json` (1.06 MB) holds entries for corrupt/empty/malformed
files. Rebuild both with `scripts/build_lake_catalog.py` or
`python scripts/build_event_lake.py --manifest-only` (no network, no key).

### 2.5 Raw Databento files — `C:\hft3-lake\raw\databento_mbo`

Raw `.dbn.zst` is the **primary**; NPZ is derived and re-derivable
(hftbacktest `convert()`). 7,571 files, 10 GB:

| Subdir | Files | Size | Naming |
|---|---|---|---|
| `mbo_pilot_basket_20260605/` | 720 | 7.6 GB | `{EVENT_ID}_mbo.dbn.zst` (multi-symbol basket) |
| `mbo_hot_universe_batch1/` | 3,422 | 1.6 GB | `{EVENT_ID}_{SYMBOL}_mbo.dbn.zst` |
| `mbo_hot_universe_batch2/` | 3,421 | 858 MB | `{EVENT_ID}_{SYMBOL}_mbo.dbn.zst` |
| `legacy/` | 8 | 9.7 MB | `{EVENT_ID}_mbo.dbn.zst` |

Additional per-event raw slots live in `C:\hft3-lake\mbo_release\{event_id}/{symbol}/raw.dbn.zst`
(4.7 GB locally; the CHI404 prod box held the most complete slot set at 17,439 —
that box's retention is governed by the lane-split docs). Databento's own portal
also retains purchase history — re-downloading previously bought windows is free
within their re-download policy; check current terms before assuming.

## 3. NPZ format (hftbacktest event tape)

Every tape is a `.npz` with a single structured array under key **`data`**:

```
dtype = [('ev', '<u8'), ('exch_ts', '<i8'), ('local_ts', '<i8'),
         ('px', '<f8'), ('qty', '<f8'), ('order_id', '<u8'),
         ('ival', '<i8'), ('fval', '<f8')]      # 64 bytes/event
```

- `px` is in **price units** (e.g. MES 4346.5), NOT ticks.
- `exch_ts` / `local_ts` are epoch **nanoseconds**.
- `ev` uses hftbacktest flag conventions: high bits `EXCH_EVENT = 1<<31`,
  `LOCAL_EVENT = 1<<30`, `BUY_EVENT = 1<<29`, `SELL_EVENT = 1<<28`; base event
  type in the low byte (`ev & 0xFF`): `TRADE_EVENT = 2`, `ADD_ORDER_EVENT = 10`,
  `CANCEL_ORDER_EVENT = 11`, `MODIFY_ORDER_EVENT = 12`, `FILL_EVENT = 13`.
  (Import the constants from `hftbacktest.types` rather than hardcoding —
  produced with hftbacktest **2.4.2** `convert()`.)
- Verified sample: `MES.v.0_CPI_2023_06_13_TIGHT_mbo.npz` → 66,792 events,
  first row `(0xE000000B, 1686659370008618240, 1686659370008722176, 4346.5, 10.0, 6844419640698, 130, 0.0)`
  = EXCH|LOCAL|BUY CANCEL.
- **Exception:** VIX `*_quotes.npz` files use key `quotes`, not `data`.

Reference loader in this repo: `packages/features_engine/src/features/npz_feed.py`
(`load_npz_events()` validates key/dtype and raises on zero events;
`iter_mbo_events()` maps rows to add/cancel/modify/trade objects). Minimal
standalone load (no repo code needed):

```python
import numpy as np
with np.load(r"C:\hft3-lake\npz\MES.v.0_CPI_2023_06_13_TIGHT_mbo.npz") as z:
    ev = z["data"]            # structured array, fields as above
side = np.where(ev["ev"] & (1 << 29), 1, -1)      # BUY_EVENT / SELL_EVENT
kind = ev["ev"] & 0xFF                             # 2/10/11/12/13
```

## 4. Where the copies live

| Copy | Location | Status |
|---|---|---|
| **Workstation (working set)** | `C:\hft3-lake` | Full lake as inventoried above |
| **B2 (system of record)** | Backblaze B2 bucket **`Hft3repo`** — `Hft3repo/lake/{npz,raw,mbo_release,features,...}`, `Hft3repo/ledgers/`, `Hft3repo/capture/rithmic/` | Keep-everything archive, ~$6/TB/mo. rclone remote name: `hft3-b2` |
| **Vast.ai box** | `root@ssh2.vast.ai:31686` — `/data/lake_npz` (MES subset) + `/data/lake_npz_leaders` (**18,999 ES/NQ/ZN/ZB 2018–2022 tapes**) | **EPHEMERAL — will be destroyed with the instance. Do not treat as a copy.** |

B2 tooling (all in this repo, portable):

- Sync up: `pwsh scripts/sync_lake_b2.ps1 [-Stage all|primaries|derived]` —
  idempotent `rclone copy` of ledger, mbo_release (excl. `events.jsonl`), raw,
  npz, features, side lanes to `hft3-b2:Hft3repo/lake/...`.
- Restore/verify: `python scripts/b2_restore_drill.py --n 50 --remote hft3-b2:Hft3repo/lake/npz`
  (random sha256 spot-check against `manifest.json`).
- Health: `python scripts/data_doctor.py` (B2 remote overridable via
  `HFT3_B2_REMOTE`, default `hft3-b2:Hft3repo`).
- Nightly automation on this workstation: scheduled task `hft3-lake-nightly`
  (02:30) → `scripts/nightly_lake_maintenance.ps1` (catalog → B2 sync → capture
  archive → doctor). **Disable or port this when the repo is retired.**
- Restore everything into a fresh machine:
  `rclone copy hft3-b2:Hft3repo/lake C:\hft3-lake --transfers 16`.
- B2 credentials live in the keystore (§7); rclone remote `hft3-b2` must be
  configured on any new machine (`rclone config`).

Historical note: `events.jsonl` intermediates (~118 GB) were deliberately purged
everywhere — byte-rederivable from `raw.dbn.zst` (proven by
`scripts/verify_jsonl_rederivation.py`, 12/12). Do not go looking for them.

## 5. Deeper docs in this repo

- `specs/DATA_LAKE.md` — authoritative lake spec.
- `docs/vault/DATA_LAKE_3TIER.md` — 3-tier ops baseline (2026-06-12): tiers,
  env resolution, automation, integrity, measured facts.
- `packages/mbo_release_lane/` — download/convert/verify lane (sequence-gap
  checks, book-reconstruction smoke, sha256 sidecars).

## 6. Spend record

Canonical spend ledger: **`C:\hft3-lake\manifest.parquet`** — one row per
Databento download. Measured 2026-07-05: **202,827 rows, cumulative `cost` sum
$3,142.93** (was $2,093.65 / ~197k rows at the 2026-06-12 baseline; the delta is
the June leaders-basket and Stage C purchases). Schema:

```
event_id, symbols, requested_symbol, resolved_symbol, start_utc, end_utc,
cost, duration_seconds, cost_per_symbol_minute, output_path, dataset (GLBX.MDP3),
schema (mbo), download_time, stype_in
```

Appends go through `packages/data_system/src/manifest_io.py` (atomic-locked);
merges via `scripts/merge_manifest_ledgers.py`. Ledger is mirrored to
`Hft3repo/lake/manifest.parquet` and receipts to `Hft3repo/ledgers/receipts`.

Budget gate: `packages/data_system/src/budget_manager.py` — constants
`INITIAL_CREDIT=$125.00`, `OPERATING_CAP=$112.50`, `RESERVE=$12.50`, per-request
`SOFT_LIMIT=$5` / `HARD_LIMIT=$10`. **Known caveat:** it compares the cap against
*lifetime* ledger spend, so with $3.1k lifetime the cap-gated path always fails
closed — every bulk purchase ran with `--override-operating-cap`. A future repo
should either fix the semantics (cap per campaign, not lifetime) or keep using
the override consciously. The real budget state is simply the ledger sum plus
whatever credit the Databento account currently holds — check the account, not
this repo.

## 7. Reuse recipe for a fresh repo

Minimum viable reuse needs only three things: the lake directory, one env var,
and numpy.

1. **Point at the lake** (User-scope env vars on this workstation; set fresh in
   any new repo/shell):

   ```
   HFT3_NPZ_ROOT      = C:\hft3-lake\npz        (lake root is inferred as its parent)
   HFT3_FEATURE_ROOT  = C:\hft3-lake\features   (optional, feature store)
   HFT3_MANIFEST_PATH = C:\hft3-lake\manifest.parquet   (spend ledger)
   ```

2. **Resolver** (if porting code from this repo):
   `packages/data_system/src/npz_resolver.py` — tiny, dependency-free, worth
   copying verbatim. `npz_root()` honors `HFT3_NPZ_ROOT`, `lake_root()` is its
   parent, `npz_filename(symbol, event_id)` = `{symbol}_{event_id}_mbo.npz`,
   `resolve_npz_for_event()` adds ES/MNQ/NQ fallback when the requested symbol's
   file is missing.

3. **Load a tape** — the §3 numpy snippet, or with repo code:

   ```python
   import os
   os.environ["HFT3_NPZ_ROOT"] = r"C:\hft3-lake\npz"
   from features_engine.src.features.npz_feed import load_npz_events, iter_mbo_events
   raw = load_npz_events(r"C:\hft3-lake\npz\MES.v.0_CPI_2023_06_13_TIGHT_mbo.npz")
   events = list(iter_mbo_events(raw))   # MBOEvent(timestamp_ns, order_id, action, side, price, size)
   ```

4. **Batch selection**: iterate `npz/manifest.json`, filter
   `event_count > 0`, join on `event_id` against `events.csv` for release
   metadata. Don't glob the directory blind — you'll hit the 2,661 stubs.

5. **Credentials**: single master file **`~/Desktop/keys.env`** (plain
   `KEY=VALUE`), loaded by `packages/data_system/src/keystore.py`
   (resolution: `os.environ` → `$HFT3_KEYS_ENV` → `~/Desktop/keys.env` →
   `<repo>/.env`). Databento key name: `DATABENTO_API_KEY`. B2/rclone creds are
   in the same file / rclone config. Copy `keystore.py` to any new repo — it is
   self-contained.

## 8. Buying more data later (incl. WIDE windows)

Purchase machinery: **`scripts/build_event_lake.py`** — events.csv-driven,
**dry-run by default**, spends money only with `--confirm-purchase`.

```
python scripts/build_event_lake.py --dry-run                     # cost table, no download
python scripts/build_event_lake.py --dry-run --event-type CPI --symbols MES.v.0,ES.v.0
python scripts/build_event_lake.py --confirm-purchase [--override-operating-cap] [--override-hard-limit]
python scripts/build_event_lake.py --manifest-only               # rebuild manifest.json from disk, no key
```

Flags: `--events-csv` (default `packages/data_system/config/events.csv`),
`--symbols`, `--event-type`, `--dry-run`, `--confirm-purchase`,
`--manifest-only`, `--override-hard-limit`, `--override-operating-cap`.
It skips (event, symbol) pairs whose clean NPZ already exists, so it is safe to
re-run and it only prices the gap. Requires `DATABENTO_API_KEY` (dataset
`GLBX.MDP3`, schema `mbo`); every purchase appends to the ledger (§6).

**WIDE windows** (fixes the +10 s limitation, §1): a staged WIDE csv existed at
`C:\tmp\events_wide.csv` (4,656 lines, −120/+120 s) — **`C:\tmp` is not durable;
regenerate rather than trust it.** Regeneration recipe:

1. Copy the rows you want from `packages/data_system/config/events.csv`.
2. Rename `event_id` suffix `_TIGHT` → `_WIDE` and `window_name` `TIGHT` → `WIDE`.
3. Set `start_offset_seconds=-120`, `end_offset_seconds=120` (or whatever window
   the new research needs — cost scales linearly with symbol-minutes).
4. `python scripts/build_event_lake.py --events-csv path\to\events_wide.csv --dry-run`
   to price it, then `--confirm-purchase`.

Verified staged-file header row:

```
ADP_EMPLOYMENT_2018_01_03_WIDE,ADP_EMPLOYMENT,2018-01-03,08:15:00,America/New_York,WIDE,-120,120,"MES.v.0,MNQ.v.0,ES.v.0,NQ.v.0,ZN.v.0,ZB.v.0,RTY.v.0",50,ADP,https://adpemploymentreport.com/,2018-01-01,ADP_EMPLOYMENT from release calendar,SOURCED
```

---

*All counts/sizes measured 2026-07-05 on the workstation. The lake outlives this
repo; the B2 bucket `Hft3repo` is the system of record.*
