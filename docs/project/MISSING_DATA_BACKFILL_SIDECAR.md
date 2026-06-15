# MANDATORY ONTOLOGY GATE: Before using this document, operate from the Obsidian vault ontology and the provided mathematics/quantitative-finance/HFT PDFs; do not invent project requirements outside that authority.

# Missing Data Backfill Sidecar

Date: 2026-06-15

Status: `PLANNED_NON_BLOCKING_INTAKE` (no new pipeline, no live routing, no model-readiness claim)

## Purpose

This sidecar records the missing-data upload/backfill queue that can run later
without blocking Q001 available-data research. Strategies, models, or features
that require unavailable data must be sidelined with explicit coverage,
skip, or rejection reasons. Strategies that can run on currently available
data may continue.

## Authority Sources

- Vault: `architecture/Data and Artifacts Layout.md`.
- Vault: `decisions/2026-06-14 Empty NPZ catalog quarantine.md`.
- Vault: `decisions/2026-06-14 Data doctor catalog accounting.md`.
- Vault: `decisions/2026-06-14 Options empty DBN no-data sidecars.md`.
- Repo: `docs/project/Q001_DATA_INVENTORY_STATUS.md`.
- Repo: `docs/project/Q001_MBO_GAP_REJECTION_LEDGER.md`.
- Repo: `docs/project/Q001_OPTIONS_STRICT_MBO_WARNING_LEDGER.md`.
- Repo: `runtime/data_audits/paid_data_inventory.md`.
- Repo: `docs/ops/ws0-4-databento-options-backfill.md`.
- Repo: `packages/features_engine/src/features/vix_features.py`.

## Non-Blocking Rules

1. Missing data is not a global blocker. It blocks only the exact model cells,
   features, or strict reconstruction claims that require it.
2. Unavailable data must never be counted as runnable coverage.
3. Raw DBN files are downloaded backlog until converted, cataloged, and
   accepted by the active manifest/data-doctor checks.
4. Empty, malformed, corrupt, or unreadable NPZ files belong in
   `<npz_root>/catalog_quarantine.json`, not `<npz_root>/manifest.json`.
5. Empty DBN files clear an options gap only with a matching `.doctor.json`
   sidecar that proves vendor no-data status, schema, size, SHA-256, and
   exact window metadata.
6. Any paid pull must start with dry-run/cost-estimate evidence and remain
   budget-gated.

## Missing Data Queues

| Queue | Current missing evidence | Landing zone | Tracking surface | Model treatment until filled |
|---|---:|---|---|---|
| CME futures MBO pilot event-symbol gaps | `211` slots: `203` full no-market slots plus `8` partial FED_H41 symbol absences | `HFT3_NPZ_ROOT` or `data/npz` after conversion to `<symbol>_<event_id>_mbo.npz`; raw uploads may stage under the paid-data source root and sync through `scripts/paid_data_inventory.py` | `scripts/build_lake_catalog.py`, `<npz_root>/manifest.json`, `<npz_root>/catalog_quarantine.json`, `runtime/data_audits/paid_data_inventory.*` | Sidelined only for the affected event-symbol cells in `Q001_MBO_GAP_REJECTION_LEDGER.md`; available-data cells continue |
| Options strict quote-level fixing MBO | `507` strict quote gaps, `503` stale strict quote gaps | `C:\hft3-lake\options\fixing_mbo` or the equivalent `lake_root/options/fixing_mbo` | `scripts/data_doctor.py`, `runtime/data_doctor_report.json`, `Q001_OPTIONS_STRICT_MBO_WARNING_LEDGER.md` | Strict quote reconstruction, strict quote-only features, order-book replay, and options model promotion stay sidelined; study coverage can continue where `fixing_study_trade_or_mbo` is green |
| Options definitions/statistics/OHLCV expansions | Not a Q001 blocker; add only when a study requires wider chain or date coverage | `lake_root/options/definitions`, `lake_root/options/statistics`, `lake_root/options/ohlcv` | `scripts/data_doctor.py`, `runtime/data_doctor_report.json`, options backfill plans under `research_cards/backfill_plans/` | Only models requiring the missing expansion are sidelined |
| VIX/options-derived feature files | No Q001 certification of a complete VIX feature lake | Existing feature output conventions for `packages/features_engine/src/features/vix_features.py`; do not zero-fill missing source data | Feature artifact existence plus the feature module's `VixDataUnavailable` behavior | Models requiring unavailable VIX/options feature inputs are sidelined; missing features are unknown, not zero |

## Upload/Backfill Intake Contract

Use existing surfaces only:

```powershell
# Inventory staged paid files without copying.
python scripts\paid_data_inventory.py --source-root <paid-data-source> --dry-run --verify-q001-hashes

# Copy staged paid files into ignored data roots without deleting source files.
python scripts\paid_data_inventory.py --source-root <paid-data-source> --sync --verify-q001-hashes

# Rebuild the runnable NPZ catalog and quarantine invalid or empty NPZ files.
python scripts\build_lake_catalog.py --full

# Recheck data-doctor and options lane evidence.
python scripts\data_doctor.py

# Refresh the Q001 inventory evidence.
python scripts\paid_data_inventory.py --dry-run --verify-q001-hashes
```

For CME event-window backfill, use the existing workbench catalog command before
any download:

```powershell
python apps\workbench\scripts\backfill_catalog.py --model <MODEL_ID> --symbol <SYM>.v.0 --scope full_universe --dry-run
python apps\workbench\scripts\backfill_catalog.py --model <MODEL_ID> --symbol <SYM>.v.0 --scope full_universe --download-missing --max-cost-usd <CAP>
```

For options fixing windows, use the existing dry-run or estimate path first:

```powershell
python scripts\pull_fixing_windows.py mbo --dry-run
python scripts\pull_fixing_windows.py mbo --estimate-cost
python scripts\pull_fixing_windows.py mbo --download
```

## Completion Gate

A data queue item is filled only when:

- the bytes land in the correct ignored data/lake location;
- the active catalog or data-doctor surface records the artifact;
- invalid or no-data artifacts are quarantined or sidecar-proved;
- `runtime/data_audits/paid_data_inventory.*` is refreshed;
- the relevant Q001 ledger is updated with the new status; and
- dependent model cells stop reporting skip/rejection only for the newly filled
  data.

Until then, the queue remains non-blocking backlog.
