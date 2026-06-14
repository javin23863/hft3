# MANDATORY ONTOLOGY GATE: Before using this document, operate from the Obsidian vault ontology and the provided mathematics/quantitative-finance/HFT PDFs; do not invent project requirements outside that authority.

# Q001 Options Strict MBO Warning Ledger

Date: 2026-06-15

Status: `PROPOSED_WARNING_LEDGER` (`not-owner-accepted`, not closed/green)

Sources:

- `runtime/data_audits/paid_data_inventory.json`
- `runtime/data_doctor_report.json`
- `scripts/data_doctor.py`
- `tests/test_data_doctor_options.py`
- `apps/cockpit/backend/aggregate/system.py`
- `apps/cockpit/backend/aggregate/alerts.py`
- [Q001_DATA_INVENTORY_STATUS.md](Q001_DATA_INVENTORY_STATUS.md)

## Scope

This ledger classifies the Q001 options warning named
`options-fixing-mbo-coverage`. It does not clear the warning, does not make Q001
green, and does not prove options model readiness. It records the boundary
between the study-coverage gate and the stricter MBO quote reconstruction
diagnostic.

The options lane remains first-class: strict quote-level MBO coverage is still
blocking evidence for any claim that reconstructs the options order book or uses
strict quote-only MBO features. The warning is non-blocking only for the
narrowed Q001 inventory/study-coverage question where trades or active NPZ
manifest coverage are allowed by the existing data-doctor mode.

## Current Evidence

| Field | Value | Meaning |
|---|---:|---|
| Q001 options status | `WARN` | One warning remains in the options data-doctor surface. |
| Warn check | `options-fixing-mbo-coverage` | Strict quote-only MBO diagnostic. |
| Fail checks | `0` | No options fail checks are present in the Q001 report. |
| Study coverage mode | `fixing_study_trade_or_mbo` | Study coverage may use quote files, trade files, or validated active NPZ manifest coverage. |
| Expected expiry dates | `784` | Rule-based options expiry calendar expectation. |
| Expiry coverage dates covered | `784/784` | Union coverage is complete for Q001 inventory scope after allowed alternate active NPZ coverage is counted. |
| Raw fixing MBO study file dates | `782` | Dates covered directly by fixing MBO quote/trade files before active NPZ manifest coverage is counted. |
| Covered elsewhere | `3` | Expected dates covered by validated active NPZ manifest evidence. |
| Covered-elsewhere net-new dates | `2` | Dates added to the study coverage union: `2024-09-18`, `2025-06-20`. |
| Covered-elsewhere overlap | `1` | Date already present in raw fixing MBO study coverage: `2023-09-15`. |
| Study gap count | `0` | No missing dates under `fixing_study_trade_or_mbo`. |
| Study stale gap count | `0` | No stale study-coverage gaps. |
| Strict quote files | `275` | Dates covered by strict quote-level fixing MBO files. |
| Strict quote gap count | `507` | Expected dates not covered by strict quote-level fixing MBO quotes or alternate active NPZ coverage. |
| Strict quote stale gap count | `503` | Strict quote gaps older than the vendor-lag grace window. |
| Trade files | `507` | Dates with trade-only fixing MBO files. |
| Trade-only dates | `507` | Trade coverage that satisfies study coverage but not strict quote reconstruction. |
| Invalid fixing files | `0` | No invalid fixing files in the report. |

First strict quote gaps recorded by the report:

```text
2024-06-04
2024-06-05
2024-06-06
2024-06-07
2024-06-10
2024-06-11
2024-06-12
2024-06-13
2024-06-14
2024-06-17
```

## Code Boundary

`scripts/data_doctor.py` computes two separate checks:

| Check | Mode | Gate behavior |
|---|---|---|
| `options-fixing-coverage` | `fixing_study_trade_or_mbo` | Fails if study coverage has gaps. |
| `options-fixing-mbo-coverage` | `strict_mbo_quotes` | `warn_only=True`; warns if strict quote-only MBO has gaps. |

Cockpit aggregation treats `options-fixing-mbo-coverage` as advisory, while
mandatory options data checks still include `options-fixing-coverage`,
`options-fixing-mbo`, `options-ohlcv`, `options-definitions`, and
`options-statistics`.

## Acceptance Boundary

If the project owner accepts this ledger for Q001 inventory scope, the accepted
meaning is limited to:

- Q001 inventory/study coverage is not blocked by strict quote gaps because
  `fixing_study_trade_or_mbo` has `expiry_coverage.dates_covered=784/784` and
  `gap_count=0`. This is union coverage: `782` raw fixing MBO study dates plus
  `2` net-new active-NPZ dates, with `1` covered-elsewhere date overlapping raw
  study coverage.
- The strict quote warning remains visible and must not be hidden from cockpit,
  data-doctor, or handoff docs.
- Strict options quote reconstruction, strict quote-only MBO features, options
  order-book replay, and options model promotion remain blocked until strict
  quote coverage is filled or separately scoped out.
- This acceptance does not promote, certify, or green any options model.

If accepted, update [Q001_DATA_INVENTORY_STATUS.md](Q001_DATA_INVENTORY_STATUS.md)
and [OPEN_QUESTIONS_AND_REJECTIONS.md](OPEN_QUESTIONS_AND_REJECTIONS.md) with
the owner decision and rerun:

```powershell
python scripts\paid_data_inventory.py --dry-run --verify-q001-hashes
```
