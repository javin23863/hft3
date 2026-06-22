# Trade Manager Restart Recovery Specification

Status: v0.1 (Slices 5–6). Inert/read-only recovery probe — no adapter calls, no
cancel/flatten/route. Parent contract:
[TRADE_MANAGER_LIFECYCLE_WORKSTATION_SPEC.md](TRADE_MANAGER_LIFECYCLE_WORKSTATION_SPEC.md).

## Purpose

After Trade Manager or workstation process restart, produce a **recovery report**
from Phase 23 session artifacts without submitting orders.

## Module

- `packages/trade_manager/restart.py`
- Tests: `tests/test_trade_manager_restart_recovery.py`

## Inputs (read-only)

Under `artifacts/sessions/{session_id}/` (via `session.resolve_session_path`):

| Artifact | Required for OK |
|---|---|
| `session_manifest.json` | yes |
| `order_state_transitions.jsonl` | yes |
| `positions.jsonl` | yes for position OK |
| `order_intents.jsonl` | recommended |
| `kill_switch_events.jsonl` | optional |

Optional lifecycle registry (`runtime/lifecycle/model_lifecycle.json`) for
`lifecycle_registry_ok` when `lifecycle_dir` is supplied.

## Report fields

| Field | Values |
|---|---|
| `status` | `OK`, `INCIDENT_REQUIRED`, `UNKNOWN` |
| `open_orders_unknown` | bool — any order latest state non-terminal |
| `position_reconciliation_status` | `OK`, `MISMATCH`, `UNKNOWN` |
| `lifecycle_registry_ok` | bool or null when not checked |
| `required_operator_actions` | string[] |
| `safe_to_resume_signals` | bool — **always false** on dev workstation |
| `session_id` | str |

## Rules

1. Path traversal on `session_id` → `RestartRecoveryError`.
2. Malformed JSON/JSONL → `UNKNOWN`.
3. Missing required artifacts → `INCIDENT_REQUIRED` or `UNKNOWN`.
4. Non-terminal latest order state → `INCIDENT_REQUIRED`, `open_orders_unknown=true`.
5. Missing position snapshot → position `UNKNOWN`, incident required.
6. Kill-switch engaged in latest event → `safe_to_resume_signals=false`.
7. Never invoke broker adapters.

## Operator actions (examples)

- `reconcile_open_orders` — non-terminal order states remain
- `reconcile_positions` — position snapshot missing or mismatch
- `review_kill_switch` — kill switch not clear
- `review_lifecycle_registry` — registry load failed
