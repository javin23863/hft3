# MANDATORY ONTOLOGY GATE: Before using this document, operate from the Obsidian vault ontology and the provided mathematics/quantitative-finance/HFT PDFs; do not invent project requirements outside that authority.

# Q001 Data Inventory Status

Date: 2026-06-14

Status: `INVENTORIED_WITH_WARNINGS` (`inventory-with-warnings`, not closed/green)

Source report: `runtime/data_audits/paid_data_inventory.json`

MBO gap ledger: [Q001_MBO_GAP_REJECTION_LEDGER.md](Q001_MBO_GAP_REJECTION_LEDGER.md)
(`PROPOSED_REJECTION_LEDGER`, not owner-accepted)

Options strict MBO warning ledger:
[Q001_OPTIONS_STRICT_MBO_WARNING_LEDGER.md](Q001_OPTIONS_STRICT_MBO_WARNING_LEDGER.md)
(`PROPOSED_WARNING_LEDGER`, not owner-accepted)

Owner decision packet:
[Q001_OWNER_DECISION_PACKET.md](Q001_OWNER_DECISION_PACKET.md)
(`OWNER_DECISION_REQUIRED`, not owner-accepted)

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
- The event/window rejection ledger is drafted in [Q001_MBO_GAP_REJECTION_LEDGER.md](Q001_MBO_GAP_REJECTION_LEDGER.md), but it is not owner-accepted and does not close Q001 by itself.
- Options `data_doctor` status is `WARN` only due to `options-fixing-mbo-coverage`; study coverage has `gap_count=0`, `expiry_coverage.dates_covered=784/784`, and `fail_checks=[]`.
- The strict options MBO warning ledger is drafted in [Q001_OPTIONS_STRICT_MBO_WARNING_LEDGER.md](Q001_OPTIONS_STRICT_MBO_WARNING_LEDGER.md), but it is not owner-accepted and does not close Q001 by itself.

## Remaining Warnings

| Source | Warning | Detail |
|---|---|---|
| MBO pilot manifest | Missing or unavailable event-symbol slots | 211 total: `203` full `no_market_data` slots plus `8` FED_H41 partial symbol absences. Event-type totals: `EIA_CRUDE=14`, `EIA_NATGAS=14`, `FED_BEIGE_BOOK=70`, `FED_H41=29`, `FOMC_PRESS=42`, `TREASURY_AUCTION=42`. |
| Options data doctor | Strict MBO quote diagnostic remains WARN | `options-fixing-mbo-coverage` reports `strict_mbo_gap_count=507` and `strict_mbo_stale_gap_count=503`; study coverage remains non-failing with `gap_count=0`. |

## Warning Triage

The `211` MBO pilot gaps are classified but not accepted as successful runnable coverage. Full-universe model runs must treat those event-symbol slots as explicit unavailable data and must skip or reject them with the recorded reason unless a future paid-data fill changes the manifest.

The strict options MBO quote warning is non-blocking only for the narrowed Q001 inventory/study-coverage question because `fixing_study_trade_or_mbo` has `expiry_coverage.dates_covered=784/784` with `gap_count=0`. It remains blocking evidence for any strict MBO quote reconstruction claim.

## Event/Window Rejection Ledger

[Q001_MBO_GAP_REJECTION_LEDGER.md](Q001_MBO_GAP_REJECTION_LEDGER.md) lists the exact `29` full no-market windows and `2` partial FED_H41 windows with reason codes, rejected symbols, and slot totals. Its status is `PROPOSED_REJECTION_LEDGER`; it is acceptance-ready evidence for the project owner, not a completed acceptance decision.

## Options Strict MBO Warning Ledger

[Q001_OPTIONS_STRICT_MBO_WARNING_LEDGER.md](Q001_OPTIONS_STRICT_MBO_WARNING_LEDGER.md) records the exact strict quote-only MBO warning boundary: `507` strict quote gaps, `503` stale strict quote gaps, `expiry_coverage.dates_covered=784/784`, and `0` study gaps. Its status is `PROPOSED_WARNING_LEDGER`; it is acceptance-ready evidence for the project owner, not a completed acceptance decision.

## Owner Decision Packet

[Q001_OWNER_DECISION_PACKET.md](Q001_OWNER_DECISION_PACKET.md) consolidates the two required owner decisions for Q001: the MBO pilot gap ledger and the options strict MBO warning ledger. Its status is `OWNER_DECISION_REQUIRED`; it does not accept either ledger, close Q001, or prove model readiness.

## Interpretation

Q001 is no longer an unknown-data question: the active NPZ manifest exists, is readable, has content-verified SHA256 coverage, and has no missing NPZ files or invalid SHA256 rows. It is still not green or closed because the MBO pilot has 211 missing or unavailable event-symbol slots and the options strict MBO quote diagnostic still warns. The warning triage narrows the remaining decision, but it does not convert missing slots into coverage.

This status should be treated as inventory evidence only. It does not prove model readiness, robustness, point-in-time joins, or promotion eligibility.

## Next Gate

Close Q001 only after the 211 MBO pilot missing or unavailable slots are filled or the project owner formally accepts [Q001_MBO_GAP_REJECTION_LEDGER.md](Q001_MBO_GAP_REJECTION_LEDGER.md) as sufficient for inventory scope, and the options strict MBO quote warning is cleared or the project owner formally accepts [Q001_OPTIONS_STRICT_MBO_WARNING_LEDGER.md](Q001_OPTIONS_STRICT_MBO_WARNING_LEDGER.md) as non-blocking for the narrowed Q001 inventory scope. Then rerun:

```powershell
python scripts\paid_data_inventory.py --dry-run --verify-q001-hashes
```

Update `OPEN_QUESTIONS_AND_REJECTIONS.md` with the new result; until then Q001 remains `inventory-with-warnings`.
