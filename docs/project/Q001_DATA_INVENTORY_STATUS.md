# MANDATORY ONTOLOGY GATE: Before using this document, operate from the Obsidian vault ontology and the provided mathematics/quantitative-finance/HFT PDFs; do not invent project requirements outside that authority.

# Q001 Data Inventory Status

Date: 2026-06-15

Status: `ACCEPTED_AVAILABLE_DATA_SCOPE` (available-data research allowed; model-readiness not proven)

Source report: `runtime/data_audits/paid_data_inventory.json`

Source report status: `INVENTORIED_WITH_WARNINGS`

MBO gap ledger: [Q001_MBO_GAP_REJECTION_LEDGER.md](Q001_MBO_GAP_REJECTION_LEDGER.md)
(`ACCEPTED_NON_BLOCKING_INVENTORY_SCOPE`)

Options strict MBO warning ledger:
[Q001_OPTIONS_STRICT_MBO_WARNING_LEDGER.md](Q001_OPTIONS_STRICT_MBO_WARNING_LEDGER.md)
(`ACCEPTED_NON_BLOCKING_INVENTORY_SCOPE`)

Owner decision packet:
[Q001_OWNER_DECISION_PACKET.md](Q001_OWNER_DECISION_PACKET.md)
(`ACCEPTED_AVAILABLE_DATA_SCOPE`)

Owner decision artifact: `docs/project/q001_owner_decision.json`

Command run:

```powershell
python scripts\paid_data_inventory.py --dry-run --verify-q001-hashes
```

## Evidence Summary

- Q001 scope is read-only local inventory and available-data research gating, not model execution or promotion evidence.
- Hash verification was enabled: `verify_q001_hashes=true`.
- Active NPZ manifest status is `OK`: `record_count=60643`, `date_min=2018-01-01`, `date_max=2026-06-04`, `missing_npz_files=0`, `invalid_sha256_rows=0`, `sha256_content_verified=true`, `sha256_validation_mode=content_verified`.
- MBO pilot status is `completed_with_gaps`: `present_runnable_npz_slots=4829`, `expected_event_symbol_slots=5040`, `missing_or_unavailable_slots=211`, `coverage_pct=95.8135`.
- MBO pilot missing-slot taxonomy is verified: `203` slots are full `no_market_data` windows (`29` windows * `7` symbols) and `8` slots are partial FED_H41 symbol absences after redownload.
- The event/window rejection ledger is owner-accepted for inventory scope; affected model cells must remain explicit skips or rejections until data is filled.
- Options `data_doctor` status is `WARN` only due to `options-fixing-mbo-coverage`; study coverage has `gap_count=0`, `expiry_coverage.dates_covered=784/784`, and `fail_checks=[]`.
- The strict options MBO warning ledger is owner-accepted for available-data inventory scope; strict quote-only options models remain sidelined until strict quote coverage is filled or separately scoped out.

## Remaining Warnings

| Source | Warning | Detail |
|---|---|---|
| MBO pilot manifest | Missing or unavailable event-symbol slots | 211 total: `203` full `no_market_data` slots plus `8` FED_H41 partial symbol absences. Event-type totals: `EIA_CRUDE=14`, `EIA_NATGAS=14`, `FED_BEIGE_BOOK=70`, `FED_H41=29`, `FOMC_PRESS=42`, `TREASURY_AUCTION=42`. |
| Options data doctor | Strict MBO quote diagnostic remains WARN | `options-fixing-mbo-coverage` reports `strict_mbo_gap_count=507` and `strict_mbo_stale_gap_count=503`; study coverage remains non-failing with `gap_count=0`. |

## Warning Triage

The `211` MBO pilot gaps are classified and owner-accepted as unavailable data for available-data inventory scope, not as successful runnable coverage. Full-universe model runs must treat those event-symbol slots as explicit unavailable data and must skip or reject them with the recorded reason unless a future paid-data fill changes the manifest.

The strict options MBO quote warning is non-blocking only for the available-data Q001 inventory/study-coverage question because `fixing_study_trade_or_mbo` has `expiry_coverage.dates_covered=784/784` with `gap_count=0`. It remains blocking evidence for any strict MBO quote reconstruction claim, strict quote-only options feature, options order-book replay, or options model promotion.

## Event/Window Rejection Ledger

[Q001_MBO_GAP_REJECTION_LEDGER.md](Q001_MBO_GAP_REJECTION_LEDGER.md) lists the exact `29` full no-market windows and `2` partial FED_H41 windows with reason codes, rejected symbols, and slot totals. Its status is `ACCEPTED_NON_BLOCKING_INVENTORY_SCOPE`; it is not permission to count unavailable slots as runnable coverage.

## Options Strict MBO Warning Ledger

[Q001_OPTIONS_STRICT_MBO_WARNING_LEDGER.md](Q001_OPTIONS_STRICT_MBO_WARNING_LEDGER.md) records the exact strict quote-only MBO warning boundary: `507` strict quote gaps, `503` stale strict quote gaps, `expiry_coverage.dates_covered=784/784`, and `0` study gaps. Its status is `ACCEPTED_NON_BLOCKING_INVENTORY_SCOPE`; it is not options model-readiness evidence.

## Owner Decision Packet

[Q001_OWNER_DECISION_PACKET.md](Q001_OWNER_DECISION_PACKET.md) records the owner decision for Q001: available-data models may run with explicit coverage, while missing-MBO-required models and strict-options-quote-required models are sidelined until data is filled or separately scoped out. Its status is `ACCEPTED_AVAILABLE_DATA_SCOPE`; it does not prove model readiness.

## Interpretation

Q001 is no longer an unknown-data question and no longer blocks available-data research: the active NPZ manifest exists, is readable, has content-verified SHA256 coverage, and has no missing NPZ files or invalid SHA256 rows. The raw report still has warnings because the MBO pilot has 211 missing or unavailable event-symbol slots and the options strict MBO quote diagnostic still warns. The owner decision converts those warnings into explicit model-scope skips, not into runnable coverage.

This status should be treated as inventory evidence only. It does not prove model readiness, robustness, point-in-time joins, or promotion eligibility.

## Operating Gate

Available-data models may proceed only when they emit explicit coverage, skip, or rejection reasons. Strategies that require missing MBO slots or strict options quote reconstruction stay sidelined until data is filled or separately scoped out.

Non-blocking data fill setup:

```powershell
python apps\workbench\scripts\backfill_catalog.py --model HYP_5 --symbol <SYM>.v.0 --dry-run
python scripts\pull_fixing_windows.py --schema mbo --dry-run
```

After any data fill or scope change, rerun:

```powershell
python scripts\paid_data_inventory.py --dry-run --verify-q001-hashes
```
