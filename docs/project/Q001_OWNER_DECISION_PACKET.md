# MANDATORY ONTOLOGY GATE: Before using this document, operate from the Obsidian vault ontology and the provided mathematics/quantitative-finance/HFT PDFs; do not invent project requirements outside that authority.

# Q001 Owner Decision Packet

Date: 2026-06-15

Status: `OWNER_DECISION_REQUIRED` (`not-owner-accepted`, not closed/green)

Purpose: give the project owner one auditable decision surface for Q001 without
changing Q001 status. This packet does not accept the ledgers, close Q001, green
the cockpit, or prove model readiness.

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

## Required Owner Decisions

Q001 can move out of `inventory-with-warnings` only after both decisions below
are explicit. An agent must not infer acceptance from silence.

| Decision | Acceptable outcomes | Effect |
|---|---|---|
| MBO pilot gap ledger | Fill missing/unavailable slots; accept [Q001_MBO_GAP_REJECTION_LEDGER.md](Q001_MBO_GAP_REJECTION_LEDGER.md) as non-blocking for inventory scope; or reject the gaps for model scope. | Determines whether the `211` missing/unavailable event-symbol slots can remain as explicit rejected/unavailable coverage for Q001 inventory. |
| Options strict MBO warning ledger | Fill strict quote coverage; accept [Q001_OPTIONS_STRICT_MBO_WARNING_LEDGER.md](Q001_OPTIONS_STRICT_MBO_WARNING_LEDGER.md) as non-blocking for narrowed Q001 inventory/study coverage; or reject strict quote gaps for model scope. | Determines whether the strict quote warning remains a visible non-blocking inventory warning while still blocking strict quote reconstruction and options model promotion. |

## Non-Negotiable Boundaries

- Acceptance is limited to Q001 inventory scope.
- Acceptance does not prove model readiness, PIT joins, robustness, promotion
  eligibility, or options lane readiness.
- The cockpit must not show green from this packet alone.
- Full-universe research must treat rejected or unavailable MBO slots as
  explicit skip/rejection reasons unless the data is filled later.
- Strict options quote reconstruction, strict quote-only MBO features, options
  order-book replay, and options model promotion remain blocked until strict
  quote coverage is filled or separately scoped out.

## Post-Decision Procedure

After the owner records both decisions:

1. Update [Q001_DATA_INVENTORY_STATUS.md](Q001_DATA_INVENTORY_STATUS.md) with
   the owner decision, date, and remaining scope.
2. Update [OPEN_QUESTIONS_AND_REJECTIONS.md](OPEN_QUESTIONS_AND_REJECTIONS.md)
   so Q001 is either still open with a reason or moved to the appropriate
   controlled feature row.
3. Rerun the inventory verifier:

```powershell
python scripts\paid_data_inventory.py --dry-run --verify-q001-hashes
```

4. Treat the rerun output as the authority. If it still reports warnings, keep
   Q001 and the cockpit non-green unless every remaining warning is explicitly
   owner-accepted as non-blocking for Q001 inventory scope and no unaccepted
   blocker remains. Any unaccepted warning keeps Q001 open.

## Decision Record Template

```text
Owner decision date:
MBO pilot gap decision:
Options strict MBO warning decision:
Accepted inventory scope:
Rejected model scope, if any:
Required future data fill, if any:
Post-decision verifier command:
Post-decision verifier result:
Q001 final status after rerun:
```
