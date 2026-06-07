# CME macro MBO data lane — operator handoff

Generated for handoff on 2026-06-07. Numbers below come from `runtime/data_audits/priority_lane_coverage.json` (2026-06-07T08:27:16Z) unless you re-run the report.

## What this repo lane is

This workstream in **hft3** builds and fills **CME macro release book (MBO) data** for economic-event backtesting: per-event time windows around Tier 1–3 macro releases (plus UNEMPLOYMENT_CLAIMS), seven CME futures symbols, derived **NPZ** replay files, and **VIX.OPT** CMBP-1 sensor inputs in the same storage layout (`data/mbo_release/` and paid-data mirror).

It is **not** the crypto lane, equities decadal pull, or generic discovery audits — those live elsewhere in the repo.

## Current state

| Metric | Value |
|--------|--------|
| Priority windows | 2,494 |
| CME symbols | MES.v.0, MNQ.v.0, ES.v.0, NQ.v.0, ZN.v.0, ZB.v.0, RTY.v.0 |
| MBO slots (windows × 7) | 17,458 |
| MBO **complete** (valid NPZ) | 15,902 (**91.09%**) |
| **not_downloaded** | 765 |
| **invalid** (raw often present, NPZ/validation failed) | 791 |
| MES.v.0 complete (recomputed) | ~90.2% (2,249 / 2,494) |
| ES.v.0 complete (recomputed) | ~89.8% (2,240 / 2,494) |
| VIX post–CMBP-1 eligible | 879 windows |
| VIX sensor **complete** (finite level in parquet) | 62 (**7.05%** of eligible) |
| VIX derivable (raw present, sensor pending) | 280 |
| VIX invalid (raw ok, sensor all-NaN / bad) | 525 |

**Backtest readiness:** MBO for primary equity index macros (MES/ES) is **high but not finished** (~90% slot fill). Campaign-level readiness (which events each model actually needs) is defined by the workbench catalog — use `backfill_catalog --dry-run` per model (see below). Do not assume 100% until dry-run shows zero missing NPZ for that model’s symbols and walk-forward periods.

Refresh inventory:

```powershell
cd C:\Users\MSI\Documents\GitHub\hft3
python scripts/report_priority_lane_coverage.py
```

Output: `runtime/data_audits/priority_lane_coverage.json` and a short stdout summary.

## What was built

### Unified discovery

- **`packages/data_system/src/event_data_resolver.py`** — single place to resolve MBO NPZ, MBO raw under `mbo_release/`, VIX raw, and VIX sensor parquet; builds priority-lane coverage; sensor “complete” requires **finite `level`** in parquet (honesty fix for all-NaN files).
- **`packages/data_system/src/data_roots.py`** — `HFT3_PAID_DATA_ROOT` and NPZ search paths (repo `data/` + paid root).
- **`packages/data_system/src/npz_resolver.py`** — wired to paid/local NPZ trees (workbench/backtest consumption).
- **`packages/mbo_release_lane/storage.py`** — canonical on-disk layout for macro MBO release lane (raw + metadata).
- **`packages/mbo_release_lane/sensor_adapter.py`** — VIX sensor derivation adapter into the lane.
- **`apps/workbench/src/data/event_catalog.py`**, **`verify_data.py`**, **`backfill_catalog.py`** — catalog and verification use unified resolution.

### Downloads and ops scripts

| Script | Purpose |
|--------|---------|
| `scripts/run_macro_mbo_download_dual_keepalive.ps1` | **Primary:** local shard 0 + chi404 shard 1, both keepalive, priority macro scope |
| `scripts/run_macro_mbo_download_dual.ps1` | Dual-host one-shot variant |
| `scripts/run_macro_mbo_download_keepalive.ps1` | Local-only keepalive |
| `scripts/run_chi404_mbo_download_keepalive.sh` | Remote keepalive (deployed to chi404) |
| `scripts/download_mbo_release_data.py` | CLI entry (referenced by keepalive wrappers) |
| `scripts/merge_chi404_mbo_data.py` | Rsync/merge remote `data/mbo_release` + NPZ into local/paid root |
| `scripts/mbo_monitor_progress.py` | Progress monitor |
| `scripts/monitor_mbo_dual_download.ps1` | Dual-host monitor wrapper |
| `scripts/rebuild_local_manifest.py` | Rebuild local Databento spend manifest |
| `scripts/derive_missing_npz.py` / `scripts/derive_event_sensors.py` | Backfill NPZ/sensors from raw |
| `scripts/mbo_download_cost_accounting.py` | Cost accounting helper |
| `scripts/databento_portal_billing.py` | Portal billing scrape/compare |
| `scripts/report_priority_lane_coverage.py` | Priority lane JSON report |
| `scripts/migrate_vix_options_to_mbo_release.py` | Legacy VIX tree → mbo_release layout |

### Budget / manifest fixes

- **`packages/data_system/src/budget_manager.py`** — operating cap raised to **$650**; spend deduped by **`output_path`** (duplicate manifest rows no longer block downloads); uses **`manifest_io.read_manifest_locked`**.
- **`packages/data_system/src/manifest_io.py`** — locked parquet reads for concurrent downloaders.
- **`packages/mbo_release_lane/download.py`** — download unblock / shard-safe behavior (with databento client updates).

### Tests

- `tests/test_data_system/test_event_data_resolver.py`

VIX CMBP-1 availability starts **2023-03-28** (`CMBP1_START` in resolver). Pre-cutoff VIX windows are **skipped_pre_cmbp1**, not download gaps.

## What is broken / permanent gaps

1. **~791 MBO `invalid` slots** — often **raw.dbn.zst present** but NPZ missing or validation failed (`validation_status: invalid`). Needs re-derive (`--derive-npz`) or re-download; not all are vendor-unavailable.
2. **~765 `not_downloaded`** — still in queue or blocked by budget/network; keepalive + merge from chi404.
3. **VIX sensors** — many events show **`invalid` with `raw_ok: true, sensor_ok: false`** (all-NaN or empty sensor parquet). Resolver now marks these incomplete; run `derive_event_sensors.py` after raw is good.
4. **chi404 history** — remote host hit the old **$325 cap** and duplicate manifest rows before fixes; local cap is 650 with dedupe. Merge script reconciles data; **portal invoice** is billing truth (`scripts/databento_portal_billing.py`).
5. **Vendor-hard gaps** — some symbology/422 cases in unrelated options research audits; **VVIX** called out in coverage JSON as unavailable on Databento (not a local bug).

## How to run downloads

1. Ensure repo root `.env` has **`DATABENTO_API_KEY`**.
2. Optional: set **`HFT3_PAID_DATA_ROOT`** if NPZ/raw live outside repo `data/` (defaults to repo `data/` when unset in many paths).
3. From repo root:

```powershell
.\scripts\run_macro_mbo_download_dual_keepalive.ps1 -Workers 32 -ShardCount 2 -LocalShard 0 -RemoteShard 1
```

This stops duplicate local download PIDs, syncs packages to **chi404**, starts remote keepalive shard 1, starts local keepalive shard 0 writing `runtime/data_downloads/macro_releases_local.log`.

**Monitor:**

```powershell
Get-Content runtime\data_downloads\macro_releases_local.log -Tail 40
python scripts/mbo_monitor_progress.py
ssh chi404 "tail -f /root/hft3/repo/runtime/data_downloads/macro_releases_chi404.log"
```

After chi404 runs, merge:

```powershell
python scripts/merge_chi404_mbo_data.py
```

## How to verify backtest readiness

Per **model** and symbol set (workbench campaign):

```powershell
python apps/workbench/scripts/backfill_catalog.py --model <MODEL_SLUG> --dry-run
```

Lists missing NPZ paths per walk-forward period without downloading.

Broader matrix (all registry models × default CME symbols):

```powershell
python runtime/audit_all_models_symbols_backtest_ready.py
```

(writes JSON under `runtime/data_audits/` when configured — check script output.)

Also: `python apps/workbench/src/verify_data.py` for catalog-level checks.

## Known blockers fixed (do not re-break)

| Issue | Fix |
|-------|-----|
| Duplicate keepalive / double shard on same index | Dual script kills existing `download_mbo_release_data` PIDs; distinct **LocalShard** vs **RemoteShard** |
| Budget stuck at $325 / false “over cap” | Cap **650**; manifest cost **dedupe by output_path** |
| Manifest corruption / concurrent write | `read_manifest_locked` in budget + rebuild script |
| chi404 data not on workstation | **`merge_chi404_mbo_data.py`** |
| False “complete” VIX sensors | Resolver checks **finite level** in sensor parquet |

## Do NOT do

- **Do not** run full-repo discovery audits (`audit_all_research_data.py`, etc.) **instead of** keepalive downloads when the goal is filling MBO gaps.
- **Do not** sum raw **`manifest.parquet` `cost`** for billing — duplicate rows and retries inflate totals; use **Databento portal** / `databento_portal_billing.py`.
- **Do not** commit or wipe **`data/mbo_release/`**, **`data/npz/`**, or **`runtime/data_downloads/*.log`** into git — they are local/paid runtime artifacts.
- **Do not** force-push **`main`**; branch tracks **`origin/feat/mbo-release-lane`**.

## File map

| Path | Role |
|------|------|
| `packages/data_system/src/event_data_resolver.py` | Unified asset resolution + coverage builder |
| `packages/data_system/src/budget_manager.py` | Download credit gate |
| `packages/data_system/src/manifest_io.py` | Locked manifest I/O |
| `packages/data_system/src/data_roots.py` | Paid data root / NPZ dirs |
| `packages/mbo_release_lane/` | Download, storage, validate, NPZ derive |
| `packages/economic_event_universe/` | Macro windows, calendars, scope |
| `data/mbo_release/` | Raw MBO + VIX lane files (local) |
| `data/npz/` | Derived event NPZ for replay |
| `data/sensors/` | VIX sensor parquets |
| `runtime/data_audits/priority_lane_coverage.json` | Latest priority lane snapshot |
| `runtime/data_downloads/` | Download logs, monitor state, local manifest copies |
| `scripts/run_macro_mbo_download_dual_keepalive.ps1` | Main dual-host operator entry |

## Contacts / environment

| Item | Notes |
|------|--------|
| **`DATABENTO_API_KEY`** | Required in repo `.env`; dual script copies line to chi404 `/root/hft3/.env` |
| **`HFT3_PAID_DATA_ROOT`** | Optional; when set, resolver searches `{root}/mbo_release`, `{root}/npz`, `{root}/sensors` |
| **chi404** | SSH host alias for QuantVPS remote downloader; repo path `/root/hft3/repo` |
| **Git remote** | `https://github.com/javin23863/hft3.git` |
| **Branch** | Local `main` tracks `origin/feat/mbo-release-lane` |

---

Questions on model-specific gaps: run `backfill_catalog --dry-run` for that model first, then triage with `priority_lane_coverage.json` incomplete samples.
