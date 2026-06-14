# Data lake — 3-tier baseline (2026-06-12)

Reorg executed 2026-06-12. Authoritative spec: [`specs/DATA_LAKE.md`](../../specs/DATA_LAKE.md). This note is the ops baseline: where data lives, what maintains it, and the invariants the tooling enforces.

## Tiers

| Tier | Location | Holds | Headroom (2026-06-12) |
|------|----------|-------|-----------------------|
| Dev workstation | `C:\hft3-lake` | npz (hot), features (hot), mbo_release raws + sidecars, vendor dbn, side lanes, canonical ledger | 152 GB free / 476 GB |
| CHI404 (trading) | `/root/hft3/data` | live capture (30-day rolling), mbo_release raws (most complete copy: 17,439 slots), partial npz | 845 GB free / 911 GB |
| B2 bucket `Hft3repo` | `Hft3repo/lake/...`, `/ledgers/`, `/capture/rithmic/` | EVERYTHING (system of record): raws, npz, features, capture zstd, ledgers, catalogs | ~$6/TB/mo, keep-everything policy |

## Canonical resolution (no hardcoded paths anywhere)

- `HFT3_NPZ_ROOT=C:\hft3-lake\npz` → `npz_resolver.npz_root()`; lake root = its parent (`npz_resolver.lake_root()`)
- `HFT3_FEATURE_ROOT=C:\hft3-lake\features` → `feature_store.feature_store_root()`
- `HFT3_MANIFEST_PATH=C:\hft3-lake\manifest.parquet` → spend ledger; `databento_client` falls back to `<lake>/manifest.parquet` when only `HFT3_NPZ_ROOT` is set
- Env vars are User-scope on the workstation. **Already-running shells don't see them** — set `$env:` explicitly when launching jobs by hand.

## Spend ledger

Single canonical `C:\hft3-lake\manifest.parquet` (~197k rows, **$2,093.65 lifetime Databento spend**), atomic-locked appends via `data_system.src.manifest_io`. Merge tool: `scripts/merge_manifest_ledgers.py`. Caveat: BudgetManager's $112.50 operating-cap math compares against lifetime spend — cap-gated paths fail closed; bulk scripts run with `override_operating_cap`.

## Integrity + cleaning

- Cleaning happens in **hftbacktest 2.4.2 `convert()`** (symbol filter, event-flag mapping, snapshot ts rewrite, latency + event-order correction, monotonicity check). Richer validation (sequence gaps, book-reconstruction smoke, sha256 sidecars) is `packages/mbo_release_lane/` (ported from the retired second clone 2026-06-12).
- NPZ schema is uniform: one `data` array, fixed 8-field 64-byte `event_dtype`. VIX `*_quotes.npz` are the exception (key `quotes`).
- Hash catalog: `scripts/build_lake_catalog.py` → `<npz_root>/manifest.json` (+ `catalog_quarantine.json`). Empty `data`/`quotes` arrays, corrupt NPZs, and malformed filenames are quarantine entries, not runnable manifest coverage. Data doctor catalog coverage compares top-level NPZ file identities against manifest+quarantine `npz_path` identities; unaccounted/stale files warn, while malformed JSON, missing/invalid `npz_path`, duplicate accounting, or manifest/quarantine overlap fails closed. 2026-06-12 run: 37.6k records, found 205 corrupt NPZ; `scripts/remediate_corrupt_npz.py` re-derived 204 from slot raws, zero re-purchase.
- `events.jsonl` is **purged** on both machines (~118 GB): byte-rederivable from `raw.dbn.zst`, proven by `scripts/verify_jsonl_rederivation.py` (12/12) after pinning databento `rtype` enum→int and LF newlines in the lane. Do not recreate them except ad-hoc via the lane parser.
- Downloaders run `--keep-dbn` since 2026-06-12: raw `.dbn.zst` is the primary and ships to B2; NPZ is derived.

## Automation (workstation-driven; CHI404 holds zero cloud credentials)

Scheduled task **`hft3-lake-nightly`** (02:30 daily) → `scripts/nightly_lake_maintenance.ps1`:
1. `build_lake_catalog.py` (incremental sha256 catalog)
2. `sync_lake_b2.ps1` (idempotent rclone copy to `Hft3repo`; excludes `events.jsonl`, nested dups)
3. `archive_chi404_capture.ps1` — **pull-based** capture archival: sftp-pull closed-trade-date `.cap` from CHI404 → zstd-10 → B2 `capture/rithmic/{CONTRACT}/` → size-verify → prune CHI404 copies >30 days old (only when B2-confirmed)
4. `data_doctor.py` → `runtime/data_doctor_report.json` → cockpit alerts zone (problem-only; disk-free FAIL escalates crit)

rclone remotes on workstation: `hft3-b2` (B2), `chi404-sftp` (pull from prod, key `~/.ssh/hft3_chi404`).

## Measured facts that corrected assumptions

- CHI404 capture (`hft3-capture.service`, CC2 binary, trades+BBO only — depth callback is a no-op) measured **~82 MB total / ~1–2 GB/week**, not the assumed 40 GB/week. `hft3-rithmic-trial.service` is inactive.
- CHI404's `mbo_release` is **more complete** than the workstation's (17,439 vs 15,510 slots) — gap-pull runs via `chi404-sftp`.
- Live trading needs no lake data: engine reads config + `weights.bin` only.

## Open items

- Restore drill (50-file sha256 vs catalog) once npz/features finish first B2 sync.
- Nested `npz/npz/` adjudication + 823 double-named renames (scripts committed, first run 2026-06-12 evening).
- Scoped B2 app key (`hft3-lake-archive`, bucket-restricted) still worth creating in the console for key hygiene; crypto-lane key currently authorized for lake ops.
- BudgetManager cap semantics vs lifetime ledger need a rework before re-enabling cap-gated downloads.
