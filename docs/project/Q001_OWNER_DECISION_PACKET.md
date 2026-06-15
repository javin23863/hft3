# MANDATORY ONTOLOGY GATE: Before using this document, operate from the Obsidian vault ontology and the provided mathematics/quantitative-finance/HFT PDFs; do not invent project requirements outside that authority.

# Q001 Owner Decision Packet

Date: 2026-06-15

Status: `ACCEPTED_AVAILABLE_DATA_SCOPE` (available-data research allowed; model-readiness not proven)

Purpose: record the project owner's Q001 decision without pretending missing
data exists. Available-data models may run with explicit coverage; models that
require the missing data must stay sidelined until filled or separately scoped
out. This packet does not prove model readiness.

## Decision Scope

Q001 asks what exact CME futures/options historical datasets are available for
full-universe research after the lane split. The current inventory has evidence,
but it still has warnings:

| Evidence | Current value | Source |
|---|---:|---|
| Q001 status | `INVENTORIED_WITH_WARNINGS` | `runtime/data_audits/paid_data_inventory.json` |
| Hash verification | `verify_q001_hashes=true` | `runtime/data_audits/paid_data_inventory.json` |
| Active NPZ manifest rows | `60643` | `runtime/data_audits/paid_data_inventory.json` |
| Missing NPZ files | `0` | `runtime/data_audits/paid_data_inventory.json` |
| Invalid SHA256 rows | `0` | `runtime/data_audits/paid_data_inventory.json` |
| MBO pilot missing/unavailable slots | `211` | [Q001_MBO_GAP_REJECTION_LEDGER.md](Q001_MBO_GAP_REJECTION_LEDGER.md) |
| Full no-market slots | `203` | [Q001_MBO_GAP_REJECTION_LEDGER.md](Q001_MBO_GAP_REJECTION_LEDGER.md) |
| Partial FED_H41 symbol absences | `8` | [Q001_MBO_GAP_REJECTION_LEDGER.md](Q001_MBO_GAP_REJECTION_LEDGER.md) |
| Options data-doctor status | `WARN` | [Q001_OPTIONS_STRICT_MBO_WARNING_LEDGER.md](Q001_OPTIONS_STRICT_MBO_WARNING_LEDGER.md) |
| Options study coverage | `784/784` dates, `gap_count=0` | [Q001_OPTIONS_STRICT_MBO_WARNING_LEDGER.md](Q001_OPTIONS_STRICT_MBO_WARNING_LEDGER.md) |
| Options strict quote MBO gaps | `507` gaps, `503` stale | [Q001_OPTIONS_STRICT_MBO_WARNING_LEDGER.md](Q001_OPTIONS_STRICT_MBO_WARNING_LEDGER.md) |

## Recorded Owner Decisions

Q001 has an explicit owner decision. An agent must not widen the accepted scope
from this record.

| Decision | Acceptable outcomes | Effect |
|---|---|---|
| MBO pilot gap ledger | Accepted as non-blocking for available-data inventory scope. | The `211` missing/unavailable event-symbol slots remain explicit rejected/unavailable coverage. Models requiring those slots are sidelined until data is filled. |
| Options strict MBO warning ledger | Accepted as non-blocking for available-data inventory/study coverage. | The strict quote warning remains visible while strict quote reconstruction, strict quote-only features, options order-book replay, and options model promotion stay blocked until data is filled or separately scoped out. |

## Non-Negotiable Boundaries

- Acceptance is limited to Q001 available-data inventory scope.
- Acceptance does not prove model readiness, PIT joins, robustness, promotion
  eligibility, or options lane readiness.
- The cockpit may mark the Q001 available-data inventory gate OK only when the
  accepted decision artifact is present and no unaccepted Q001 warning/failure
  remains; this is not model readiness.
- Full-universe research must treat rejected or unavailable MBO slots as
  explicit skip/rejection reasons unless the data is filled later.
- Strict options quote reconstruction, strict quote-only MBO features, options
  order-book replay, and options model promotion remain blocked until strict
  quote coverage is filled or separately scoped out.

## Operating Procedure

After this decision:

1. Run available-data models only with explicit coverage, skip, or rejection
   reasons.
2. Keep missing-MBO-required models and strict-options-quote-required models
   sidelined until data is filled or separately scoped out.
3. Use the non-executing data-fill setup commands before any paid action:

```powershell
python apps\workbench\scripts\backfill_catalog.py --model HYP_5 --symbol <SYM>.v.0 --dry-run
python scripts\pull_fixing_windows.py --schema mbo --dry-run
```

4. Rerun the inventory verifier after any data fill or scope change:

```powershell
python scripts\paid_data_inventory.py --dry-run --verify-q001-hashes
```

5. Treat the rerun output as the raw inventory authority. Any new unaccepted
   warning or failure keeps the Q001 available-data gate not OK.

## Decision Record Template

```text
Owner decision date: 2026-06-15
MBO pilot gap decision: ACCEPTED_NON_BLOCKING_INVENTORY_SCOPE
Options strict MBO warning decision: ACCEPTED_NON_BLOCKING_INVENTORY_SCOPE
Accepted inventory scope: available-data research may proceed with explicit coverage/skip/rejection reasons
Rejected model scope, if any: missing-MBO-required and strict-options-quote-required models are sidelined until filled or separately scoped out
Required future data fill, if any: futures MBO missing slots and options strict quote MBO gaps are non-blocking side-lane backlog
Post-decision verifier command: python scripts\paid_data_inventory.py --dry-run --verify-q001-hashes
Post-decision verifier result: raw report remains INVENTORIED_WITH_WARNINGS; owner decision is ACCEPTED_AVAILABLE_DATA_SCOPE
Q001 final status after rerun: ACCEPTED_AVAILABLE_DATA_SCOPE for available-data inventory gate only
```
