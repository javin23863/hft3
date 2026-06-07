# CME macro MBO data — operator handoff

**Repo:** `C:\Users\MSI\Documents\GitHub\hft3`  
**Snapshot date:** 2026-06-07  
**Numbers source:** `runtime/data_audits/priority_lane_coverage.json` (generated 2026-06-07T08:44 UTC). Re-run `python scripts/report_priority_lane_coverage.py` for a fresh count.

Give this document to the next operator. It explains what the data is for, where it lives, how to download and verify it, what was fixed recently, and what will never fix itself.

---

## 1. Executive summary

- **Goal:** Fill order-book (MBO) data around US macro release windows for seven CME futures symbols, plus VIX options raw data, so economic-event backtests can replay real books.
- **Progress:** **91.1%** of priority MBO slots are complete (valid raw + derived NPZ). **824** slots still need download; **732** slots have raw files but failed validation or NPZ derivation.
- **Backtests:** Prior dry-runs show **~95% readiness** for main equity-index models on **MES** and **ES** macro campaigns — good enough to start testing, not enough to assume full coverage.
- **Downloads:** Run dual keepalive — local machine shard 0, remote **chi404** shard 1, **32 workers** each — via `scripts/run_macro_mbo_download_dual_keepalive.ps1`.
- **Separate problem:** VIX **sensor** files (derived from raw VIX options) are largely broken (all-NaN). Raw VIX may exist; sensor math needs its own fix. **VVIX** is not available from Databento at all.

---

## 2. What this data is for

This lane feeds **macro release order-book backtests** in the hft3 workbench.

For each scheduled macro event (CPI, NFP, FOMC, PCE, GDP, etc.) the system defines a tight time window around the release. For that window it stores:

1. **Raw MBO** from Databento (compressed `.dbn.zst` files)
2. **Derived NPZ** files the backtest engine reads for replay
3. **VIX options raw** (CMBP-1, available from 2023-03-28 onward) and optional **sensor** parquets for cross-asset features

**Seven CME symbols** (priority lane):

| Symbol | Instrument |
|--------|------------|
| MES.v.0 | Micro E-mini S&P 500 |
| ES.v.0 | E-mini S&P 500 |
| MNQ.v.0 | Micro E-mini Nasdaq |
| NQ.v.0 | E-mini Nasdaq |
| RTY.v.0 | E-mini Russell 2000 |
| ZN.v.0 | 10-Year Treasury |
| ZB.v.0 | 30-Year Treasury |

**Priority event types** (2,494 windows total):

CPI, CORE_CPI, NFP, FOMC_PRESS, FOMC_STATEMENT, FOMC_MINUTES, PROP_FLATTEN_TOPSTEP, CORE_PCE, PCE, GDP_ADVANCE, GDP_SECOND, GDP_FINAL, RETAIL_SALES, ISM_MANUFACTURING, ISM_SERVICES, JOLTS, FED_H41, FED_BEIGE_BOOK, TREASURY_AUCTION, TREASURY_REFUNDING, IMPORT_PRICES, EXPORT_PRICES, UNEMPLOYMENT_CLAIMS.

**VIX:** symbol `VIX.OPT` — separate from the seven futures; used for volatility cross-asset sensors.

---

## 3. Current numbers

Refresh anytime:

```powershell
cd C:\Users\MSI\Documents\GitHub\hft3
python scripts/report_priority_lane_coverage.py
```

### MBO (7 symbols × 2,494 windows = 17,458 slots)

| Metric | Count | Notes |
|--------|------:|-------|
| **Complete** | 15,902 | **91.09%** — raw OK and valid NPZ on disk |
| **Not downloaded** | 824 | No raw file yet; keepalive should shrink this |
| **Invalid** | 732 | Raw often present but NPZ missing or validation failed |
| **Incomplete total** | 1,556 | not_downloaded + invalid |

### Backtest readiness (prior dry-runs)

- **MES.v.0** and **ES.v.0** macro campaigns: roughly **~95%** of required event NPZ paths present when running `backfill_catalog --dry-run` for primary models.
- Slot-level MBO fill (~91%) is slightly lower because some invalid slots affect symbols unevenly.
- Always re-check per model before a production run (see section 7).

### VIX options / sensors

| Metric | Count | Notes |
|--------|------:|-------|
| Total windows | 2,494 | |
| Pre–CMBP-1 cutoff (skipped) | 1,615 | Before 2023-03-28; not a download gap |
| Eligible post-cutoff | 879 | |
| VIX raw complete | 292 | 33% of eligible |
| VIX sensor complete (finite values) | 292 | Same as raw-complete with good sensors |
| VIX invalid (raw OK, sensor bad) | 525 | Sensor parquet all-NaN or empty |
| VIX not downloaded | 12 | |
| VVIX | N/A | Not sold on Databento |

---

## 4. Repo layout

All paths relative to repo root unless `HFT3_PAID_DATA_ROOT` points elsewhere (see section 11).

```
hft3/
├── data/
│   ├── mbo_release/          # Raw MBO + VIX raw (canonical lane layout)
│   │   └── {event_id}/
│   │       └── {symbol}/     # e.g. CPI_2024_09_11_TIGHT/MES.v.0/raw.dbn.zst
│   ├── npz/                  # Derived replay NPZ per event × symbol
│   └── sensors/              # VIX sensor parquets ({event_id}_sensors.parquet)
├── runtime/
│   ├── data_audits/          # Coverage JSON, inventory reports
│   └── data_downloads/       # Download logs, monitor state, manifest copies
├── packages/
│   ├── data_system/src/      # Resolver, budget, manifest I/O
│   └── mbo_release_lane/     # Download, storage, NPZ derive, sensors
└── scripts/                  # Operator entry points (see section 5)
```

**Paid data root (`HFT3_PAID_DATA_ROOT`):** Optional env var pointing at a larger disk (e.g. external drive). When set, tools also look under:

- `{HFT3_PAID_DATA_ROOT}/mbo_release/`
- `{HFT3_PAID_DATA_ROOT}/npz/`
- `{HFT3_PAID_DATA_ROOT}/sensors/`

Repo `data/` is always searched too. NPZ and raw can live split across repo + paid root.

---

## 5. Scripts cheat sheet

Run all from repo root (`C:\Users\MSI\Documents\GitHub\hft3`).

| Task | Command |
|------|---------|
| **Coverage report** | `python scripts/report_priority_lane_coverage.py` |
| **Download MBO (manual one-shot)** | `python scripts/download_mbo_release_data.py --download --derive-npz --scope macro_releases --priority-events --workers 32` |
| **Dual keepalive (recommended)** | `.\scripts\run_macro_mbo_download_dual_keepalive.ps1 -Workers 32` |
| **Derive missing NPZ from raw** | `python scripts/derive_missing_npz.py` |
| **Derive VIX sensors from raw** | `python scripts/derive_event_sensors.py` |
| **Audit all research data** | `python scripts/audit_all_research_data.py` |
| **Backfill dry-run (per model)** | `python apps/workbench/scripts/backfill_catalog.py --model MODEL_NAME --symbol MES.v.0 --dry-run` |
| **Merge chi404 downloads to local** | `python scripts/merge_chi404_mbo_data.py` |
| **Monitor progress** | `python scripts/mbo_monitor_progress.py` |
| **Rebuild spend manifest** | `python scripts/rebuild_local_manifest.py` |
| **Portal billing check** | `python scripts/databento_portal_billing.py` |
| **Migrate old VIX tree** | `python scripts/migrate_vix_options_to_mbo_release.py` |

**Logs to tail during downloads:**

- Local: `runtime/data_downloads/macro_releases_local.log`
- chi404: `ssh chi404 "tail -f /root/hft3/repo/runtime/data_downloads/macro_releases_chi404.log"`

---

## 6. How to start downloads

### Prerequisites

1. `.env` in repo root must contain `DATABENTO_API_KEY=...`
2. SSH alias **chi404** must work (remote QuantVPS downloader)
3. Stop any stray duplicate downloaders first (see section 10)

### Start dual keepalive

```powershell
cd C:\Users\MSI\Documents\GitHub\hft3
.\scripts\run_macro_mbo_download_dual_keepalive.ps1 -Workers 32 -ShardCount 2 -LocalShard 0 -RemoteShard 1
```

What this does:

1. Kills existing local `download_mbo_release_data` Python processes (avoids duplicate shards)
2. Copies download scripts and packages to chi404 (`/root/hft3/repo`)
3. Syncs `DATABENTO_API_KEY` to chi404 `/root/hft3/.env`
4. Starts **chi404 shard 1** (keepalive loop, 32 workers)
5. Starts **local shard 0** (keepalive loop, 32 workers)
6. Each shard processes half the job queue (`--shard-index` / `--shard-count`)

Both hosts auto-restart after each batch (30 second pause). Leave them running until `not_downloaded` in the coverage report is near zero.

### After chi404 finishes a batch

Pull remote files to the workstation:

```powershell
python scripts/merge_chi404_mbo_data.py
```

Then re-run coverage and optional NPZ derive pass.

---

## 7. How to verify backtests are ready

Backtest readiness is **per model and symbol**, not just global slot counts.

```powershell
python apps/workbench/scripts/backfill_catalog.py --model BOOK_PRESSURE --symbol MES.v.0 --dry-run
python apps/workbench/scripts/backfill_catalog.py --model BOOK_PRESSURE --symbol ES.v.0 --dry-run
```

Replace `BOOK_PRESSURE` with the model slug you care about. Output lists each walk-forward period and marks events **OK** or **MISSING** for NPZ and sensors.

**Ready** = dry-run shows no **MISSING** NPZ for the symbols and date ranges that model uses.

Broader sweep (all registered models × default CME symbols):

```powershell
python runtime/audit_all_models_symbols_backtest_ready.py
```

Also useful: `python apps/workbench/src/verify_data.py` for catalog-level sanity checks.

---

## 8. What was fixed this session

| Fix | What it means for operators |
|-----|----------------------------|
| **Unified resolver wiring** | One module (`event_data_resolver.py`) decides if MBO/VIX raw, NPZ, and sensors exist. Workbench and coverage reports use the same rules. |
| **VIX moved to mbo_release layout** | VIX raw now lives under `data/mbo_release/{event_id}/VIX.OPT/` like the futures symbols. Old `data/vix_options/` tree is legacy; migration script available. |
| **Manifest lock 120s** | Concurrent downloaders no longer corrupt `manifest.parquet`. Lock waits up to 120 seconds, then clears stale locks from dead processes. |
| **Budget cap raised to $650** | `OPERATING_CAP = 650` in `budget_manager.py`. Old $325 cap was blocking chi404 mid-run. |
| **Spend dedup by output path** | Retries and duplicate manifest rows no longer count twice toward the cap. |
| **Duplicate keepalive fix** | Dual keepalive script kills existing download PIDs before starting; local and remote use **different shard indices** (0 vs 1). |
| **chi404 CRLF fix** | Remote shell script gets `sed -i 's/\r$//'` so Windows line endings do not break bash on chi404. |
| **VIX sensor honesty** | Coverage marks sensors incomplete when parquet exists but every `level` value is NaN (no false "complete"). |

Key files changed: `budget_manager.py`, `manifest_io.py`, `event_data_resolver.py`, `backfill_catalog.py`, download keepalive scripts, `merge_chi404_mbo_data.py`.

---

## 9. Permanent gaps (will not fix by re-downloading alone)

### ~732 invalid MBO slots (was ~791 earlier in session)

These have a raw file on disk but NPZ is missing or validation marked the file **invalid**. Common causes:

- Vendor returned an empty or truncated file
- Window had no trading activity
- Derive step failed once and was not retried

**Action:** Try `python scripts/derive_missing_npz.py` first. Re-download only if raw is corrupt. Some slots will **never** become valid — accept as permanent holes.

### ~824 not downloaded (was ~765 before latest pass started)

These simply have no raw file yet. Keepalive downloads should reduce this number. After chi404 runs, merge locally.

### VIX sensors effectively broken for most events

525 eligible windows have raw VIX data but **sensor parquets are all-NaN**. Root cause: **ATM strike derivation** in the sensor adapter produces no usable strike/level. This is a **code fix**, not a download fix.

- Do not block MBO backtests on VIX sensors unless the model explicitly requires them.
- Fix lives in `packages/mbo_release_lane/sensor_adapter.py` / `derive_event_sensors.py` — separate task.

### VVIX

**VVIX index is not available on Databento.** Documented in coverage JSON. Not a local gap.

### Pre-2023-03-28 VIX windows

1,615 windows are **skipped_pre_cmbp1** — CMBP-1 data did not exist yet. Expected, not a failure.

---

## 10. Known pitfalls

| Pitfall | What happens | What to do |
|---------|--------------|------------|
| **Trusting manifest raw spend sum for billing** | Duplicate rows and retries inflate totals; looks like you spent more than Databento charged | Use Databento web portal or `python scripts/databento_portal_billing.py` |
| **Running two downloaders on the same shard** | Windows **Access denied (WinError 5)** writing the same files; manifest fights | Use dual keepalive script; never start a second local shard 0 by hand |
| **chi404 still on old $325 cap logic** | Remote half of lane stops while local keeps going | Ensure latest `budget_manager.py` is synced to chi404 (dual script does this) |
| **Invalid slots retried forever** | Wastes API credits re-fetching vendor-empty windows | Triage with coverage JSON; skip known-bad event×symbol pairs |
| **Assuming 91% MBO = 100% backtest ready** | Model may need specific years/symbols with worse fill | Always `backfill_catalog --dry-run` for your model |
| **Committing data/ to git** | Huge binary blobs | Keep `data/mbo_release/`, `data/npz/`, logs out of git |
| **Full-repo audit instead of keepalive** | `audit_all_research_data.py` reports gaps but does not download MBO | Use keepalive for filling gaps; audit for inventory only |

---

## 11. Environment

| Item | Value / location |
|------|------------------|
| **Repo path** | `C:\Users\MSI\Documents\GitHub\hft3` |
| **Git remote** | `https://github.com/javin23863/hft3.git` |
| **Branch** | Local branch name is `main`; tracks **`origin/feat/mbo-release-lane`** |
| **API key** | `DATABENTO_API_KEY` in repo `.env` (required) |
| **Paid data root** | `HFT3_PAID_DATA_ROOT` in `.env` if NPZ/raw live outside repo (optional) |
| **Remote downloader** | SSH host **`chi404`**, repo at `/root/hft3/repo`, env at `/root/hft3/.env` |
| **Python** | Run scripts from repo root so `hft3_bootstrap` resolves paths |
| **Budget cap** | $650 operating cap (`packages/data_system/src/budget_manager.py`) |
| **Manifest lock** | 120 second timeout (`packages/data_system/src/manifest_io.py`) |

Load env before manual runs:

```powershell
# .env is auto-loaded by most scripts; or:
Get-Content .env | Where-Object { $_ -match '^DATABENTO_API_KEY=' }
```

---

## 12. Next operator TODO

1. **Check keepalive is still running** on both local and chi404. If stopped, re-run `.\scripts\run_macro_mbo_download_dual_keepalive.ps1 -Workers 32`.
2. **Finish the ~824 not_downloaded slots** — monitor `python scripts/report_priority_lane_coverage.py` until `not_downloaded` is minimal.
3. **Merge chi404 data** after remote batches: `python scripts/merge_chi404_mbo_data.py`.
4. **Derive NPZ** for any new raw: `python scripts/derive_missing_npz.py`.
5. **Re-verify backtests:** `backfill_catalog --model <YOUR_MODEL> --symbol MES.v.0 --dry-run` (and ES if needed).
6. **Optional:** Merge chi404 NPZ into paid root if using `HFT3_PAID_DATA_ROOT`.
7. **Separate task:** Fix VIX sensor ATM strike algorithm — do not expect downloads alone to fix VIX sensors.
8. **Do not** run competing download processes on the same shard index.

---

## Quick reference — key code paths

| File | Role |
|------|------|
| `packages/data_system/src/event_data_resolver.py` | Finds raw/NPZ/sensors; builds coverage report |
| `packages/data_system/src/budget_manager.py` | $650 cap, spend dedup |
| `packages/data_system/src/manifest_io.py` | Locked manifest reads/writes |
| `packages/data_system/src/data_roots.py` | `HFT3_PAID_DATA_ROOT` resolution |
| `packages/mbo_release_lane/download.py` | Databento download worker |
| `packages/mbo_release_lane/storage.py` | On-disk path layout |
| `scripts/run_macro_mbo_download_dual_keepalive.ps1` | Main operator entry for downloads |
| `runtime/data_audits/priority_lane_coverage.json` | Latest inventory snapshot |

---

*End of handoff. Questions on a specific model: run `backfill_catalog --dry-run` first, then look up failing event IDs in `priority_lane_coverage.json`.*
