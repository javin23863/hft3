# MANDATORY ONTOLOGY GATE: Before using this document, operate from the Obsidian vault ontology and the provided mathematics/quantitative-finance/HFT PDFs; do not invent project requirements outside that authority.

# Q001 MBO Gap Rejection Ledger

Date: 2026-06-15

Status: `ACCEPTED_NON_BLOCKING_INVENTORY_SCOPE` (not model-readiness evidence)

Sources:

- `packages/data_system/config/mbo_pilot_basket_20260605_manifest.json`
- `runtime/data_audits/paid_data_inventory.json`
- [Q001_DATA_INVENTORY_STATUS.md](Q001_DATA_INVENTORY_STATUS.md)
- [../research/MBO_PILOT_BASKET_20260605.md](../research/MBO_PILOT_BASKET_20260605.md)

## Scope

This ledger classifies the `211` missing or unavailable MBO pilot event-symbol
slots for Q001 inventory acceptance. It is not model-readiness evidence, not a
robustness artifact, and not permission to treat unavailable data as successful
runnable coverage.

The project owner accepts these rows for available-data inventory scope only.
Model runners must still skip or reject each listed event-symbol slot unless a
future paid-data fill changes the tracked manifest.

## Slot Arithmetic

| Class | Window count | Slot count | Treatment |
|---|---:|---:|---|
| Full no-market windows | 29 | 203 | Reject/skip all 7 canonical pilot symbols for each window. |
| Partial symbol absences | 2 | 8 | Reject/skip only the listed symbols for the event window. |
| Total missing or unavailable | 31 | 211 | Must not be counted as runnable coverage. |

Canonical pilot symbols: `MES.v.0`, `MNQ.v.0`, `ES.v.0`, `NQ.v.0`, `ZN.v.0`,
`ZB.v.0`, `RTY.v.0`.

## Event-Type Summary

| Event type | No-market windows | No-market slots | Partial slots | Total rejected slots |
|---|---:|---:|---:|---:|
| EIA_CRUDE | 2 | 14 | 0 | 14 |
| EIA_NATGAS | 2 | 14 | 0 | 14 |
| FED_BEIGE_BOOK | 10 | 70 | 0 | 70 |
| FED_H41 | 3 | 21 | 8 | 29 |
| FOMC_PRESS | 6 | 42 | 0 | 42 |
| TREASURY_AUCTION | 6 | 42 | 0 | 42 |
| Total | 29 | 203 | 8 | 211 |

## Full No-Market Window Rejections

These windows are classified as `no_market_data`. Each row rejects all 7
canonical pilot symbols for that event window.

| Event type | Event ID | Release date | Reason | Rejected slots | Rejected symbols |
|---|---|---|---|---:|---|
| EIA_CRUDE | `EIA_CRUDE_2024_12_25_TIGHT` | 2024-12-25 | `no_market_data` | 7 | all 7 canonical pilot symbols unavailable |
| EIA_CRUDE | `EIA_CRUDE_2025_01_01_TIGHT` | 2025-01-01 | `no_market_data` | 7 | all 7 canonical pilot symbols unavailable |
| EIA_NATGAS | `EIA_NATGAS_2024_12_25_TIGHT` | 2024-12-25 | `no_market_data` | 7 | all 7 canonical pilot symbols unavailable |
| EIA_NATGAS | `EIA_NATGAS_2025_01_01_TIGHT` | 2025-01-01 | `no_market_data` | 7 | all 7 canonical pilot symbols unavailable |
| FED_BEIGE_BOOK | `FED_BEIGE_BOOK_2023_10_07_TIGHT` | 2023-10-07 | `no_market_data` | 7 | all 7 canonical pilot symbols unavailable |
| FED_BEIGE_BOOK | `FED_BEIGE_BOOK_2024_01_07_TIGHT` | 2024-01-07 | `no_market_data` | 7 | all 7 canonical pilot symbols unavailable |
| FED_BEIGE_BOOK | `FED_BEIGE_BOOK_2024_04_07_TIGHT` | 2024-04-07 | `no_market_data` | 7 | all 7 canonical pilot symbols unavailable |
| FED_BEIGE_BOOK | `FED_BEIGE_BOOK_2024_07_07_TIGHT` | 2024-07-07 | `no_market_data` | 7 | all 7 canonical pilot symbols unavailable |
| FED_BEIGE_BOOK | `FED_BEIGE_BOOK_2024_09_07_TIGHT` | 2024-09-07 | `no_market_data` | 7 | all 7 canonical pilot symbols unavailable |
| FED_BEIGE_BOOK | `FED_BEIGE_BOOK_2024_12_07_TIGHT` | 2024-12-07 | `no_market_data` | 7 | all 7 canonical pilot symbols unavailable |
| FED_BEIGE_BOOK | `FED_BEIGE_BOOK_2025_06_07_TIGHT` | 2025-06-07 | `no_market_data` | 7 | all 7 canonical pilot symbols unavailable |
| FED_BEIGE_BOOK | `FED_BEIGE_BOOK_2025_09_07_TIGHT` | 2025-09-07 | `no_market_data` | 7 | all 7 canonical pilot symbols unavailable |
| FED_BEIGE_BOOK | `FED_BEIGE_BOOK_2025_12_07_TIGHT` | 2025-12-07 | `no_market_data` | 7 | all 7 canonical pilot symbols unavailable |
| FED_BEIGE_BOOK | `FED_BEIGE_BOOK_2026_03_07_TIGHT` | 2026-03-07 | `no_market_data` | 7 | all 7 canonical pilot symbols unavailable |
| FED_H41 | `FED_H41_2024_12_25_TIGHT` | 2024-12-25 | `no_market_data` | 7 | all 7 canonical pilot symbols unavailable |
| FED_H41 | `FED_H41_2025_01_01_TIGHT` | 2025-01-01 | `no_market_data` | 7 | all 7 canonical pilot symbols unavailable |
| FED_H41 | `FED_H41_2025_12_24_TIGHT` | 2025-12-24 | `no_market_data` | 7 | all 7 canonical pilot symbols unavailable |
| FOMC_PRESS | `FOMC_PRESS_2023_07_15_TIGHT` | 2023-07-15 | `no_market_data` | 7 | all 7 canonical pilot symbols unavailable |
| FOMC_PRESS | `FOMC_PRESS_2024_09_15_TIGHT` | 2024-09-15 | `no_market_data` | 7 | all 7 canonical pilot symbols unavailable |
| FOMC_PRESS | `FOMC_PRESS_2024_12_15_TIGHT` | 2024-12-15 | `no_market_data` | 7 | all 7 canonical pilot symbols unavailable |
| FOMC_PRESS | `FOMC_PRESS_2025_03_15_TIGHT` | 2025-03-15 | `no_market_data` | 7 | all 7 canonical pilot symbols unavailable |
| FOMC_PRESS | `FOMC_PRESS_2025_11_15_TIGHT` | 2025-11-15 | `no_market_data` | 7 | all 7 canonical pilot symbols unavailable |
| FOMC_PRESS | `FOMC_PRESS_2026_03_15_TIGHT` | 2026-03-15 | `no_market_data` | 7 | all 7 canonical pilot symbols unavailable |
| TREASURY_AUCTION | `TREASURY_AUCTION_2024_01_13_TIGHT` | 2024-01-13 | `no_market_data` | 7 | all 7 canonical pilot symbols unavailable |
| TREASURY_AUCTION | `TREASURY_AUCTION_2024_04_13_TIGHT` | 2024-04-13 | `no_market_data` | 7 | all 7 canonical pilot symbols unavailable |
| TREASURY_AUCTION | `TREASURY_AUCTION_2024_07_13_TIGHT` | 2024-07-13 | `no_market_data` | 7 | all 7 canonical pilot symbols unavailable |
| TREASURY_AUCTION | `TREASURY_AUCTION_2024_10_13_TIGHT` | 2024-10-13 | `no_market_data` | 7 | all 7 canonical pilot symbols unavailable |
| TREASURY_AUCTION | `TREASURY_AUCTION_2025_04_13_TIGHT` | 2025-04-13 | `no_market_data` | 7 | all 7 canonical pilot symbols unavailable |
| TREASURY_AUCTION | `TREASURY_AUCTION_2025_07_13_TIGHT` | 2025-07-13 | `no_market_data` | 7 | all 7 canonical pilot symbols unavailable |

## Partial Window Symbol Rejections

These windows have runnable coverage for some symbols, but the listed symbols
remain unavailable after redownload and must be rejected only for those symbols.

| Event type | Event ID | Release date | Reason | Rejected slots | Rejected symbols |
|---|---|---|---|---:|---|
| FED_H41 | `FED_H41_2024_06_19_TIGHT` | 2024-06-19 | `symbol_absent_in_raw_after_redownload` | 3 | `ES.v.0`, `ZB.v.0`, `RTY.v.0` |
| FED_H41 | `FED_H41_2024_07_03_TIGHT` | 2024-07-03 | `symbol_absent_in_raw_after_redownload` | 5 | `MES.v.0`, `MNQ.v.0`, `ES.v.0`, `NQ.v.0`, `RTY.v.0` |

## Acceptance Decision Recorded

Owner acceptance means:

- The `203` full no-market slots are accepted as unavailable data, not missing repo files.
- The `8` partial symbol absences are accepted as symbol-specific unavailable data.
- Future model-universe runners must keep these rows as explicit skips or rejections.
- This does not certify model readiness, robustness, PIT joins, or promotion eligibility.

After any future data fill, update [Q001_DATA_INVENTORY_STATUS.md](Q001_DATA_INVENTORY_STATUS.md)
and rerun:

```powershell
python scripts\paid_data_inventory.py --dry-run --verify-q001-hashes
```
