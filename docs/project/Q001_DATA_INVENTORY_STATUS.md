# MANDATORY ONTOLOGY GATE: Before using this document, operate from the Obsidian vault ontology and the provided mathematics/quantitative-finance/HFT PDFs; do not invent project requirements outside that authority.

# Q001 Data Inventory Status

Date: 2026-06-14

Status: `INVENTORIED_WITH_WARNINGS` (`inventory-with-warnings`, not closed/green)

Source report: `runtime/data_audits/paid_data_inventory.json`

Command run:

```powershell
python scripts\paid_data_inventory.py --dry-run --verify-q001-hashes
```

## Evidence Summary

- Q001 scope is read-only local inventory, not model execution or promotion evidence.
- Hash verification was enabled: `verify_q001_hashes=true`.
- Active NPZ manifest status is `OK`: `record_count=60643`, `date_min=2018-01-01`, `date_max=2026-06-04`, `missing_npz_files=0`, `invalid_sha256_rows=0`, `sha256_content_verified=true`, `sha256_validation_mode=content_verified`.
- MBO pilot status is `completed_with_gaps`: `present_runnable_npz_slots=4829`, `expected_event_symbol_slots=5040`, `missing_or_unavailable_slots=211`, `coverage_pct=95.8135`.
- MBO pilot missing-slot taxonomy is verified: `203` slots are full `no_market_data` windows (`29` windows * `7` symbols) and `8` slots are partial FED_H41 symbol absences after redownload.
- Options `data_doctor` status is `WARN` only due to `options-fixing-mbo-coverage`; study coverage has `gap_count=0`, `dates_covered=784/784`, and `fail_checks=[]`.

## Remaining Warnings

| Source | Warning | Detail |
|---|---|---|
| MBO pilot manifest | Missing or unavailable event-symbol slots | 211 total: `203` full `no_market_data` slots plus `8` FED_H41 partial symbol absences. Event-type totals: `EIA_CRUDE=14`, `EIA_NATGAS=14`, `FED_BEIGE_BOOK=70`, `FED_H41=29`, `FOMC_PRESS=42`, `TREASURY_AUCTION=42`. |
| Options data doctor | Strict MBO quote diagnostic remains WARN | `options-fixing-mbo-coverage` reports `strict_mbo_gap_count=507` and `strict_mbo_stale_gap_count=503`; study coverage remains non-failing with `gap_count=0`. |

## Warning Triage

The `211` MBO pilot gaps are classified but not accepted as successful runnable coverage. Full-universe model runs must treat those event-symbol slots as explicit unavailable data and must skip or reject them with the recorded reason unless a future paid-data fill changes the manifest.

The strict options MBO quote warning is non-blocking only for the narrowed Q001 inventory/study-coverage question because `fixing_study_trade_or_mbo` covers all `784/784` expected dates with `gap_count=0`. It remains blocking evidence for any strict MBO quote reconstruction claim.

## Interpretation

Q001 is no longer an unknown-data question: the active NPZ manifest exists, is readable, has content-verified SHA256 coverage, and has no missing NPZ files or invalid SHA256 rows. It is still not green or closed because the MBO pilot has 211 missing or unavailable event-symbol slots and the options strict MBO quote diagnostic still warns. The warning triage narrows the remaining decision, but it does not convert missing slots into coverage.

This status should be treated as inventory evidence only. It does not prove model readiness, robustness, point-in-time joins, or promotion eligibility.

## Next Gate

Close Q001 only after the 211 MBO pilot missing or unavailable slots are filled or the project owner formally accepts the event/window rejection ledger as sufficient for inventory scope, and the options strict MBO quote warning is cleared or explicitly accepted as non-blocking for the narrowed Q001 scope. Then rerun:

```powershell
python scripts\paid_data_inventory.py --dry-run --verify-q001-hashes
```

Update `OPEN_QUESTIONS_AND_REJECTIONS.md` with the new result; until then Q001 remains `inventory-with-warnings`.
