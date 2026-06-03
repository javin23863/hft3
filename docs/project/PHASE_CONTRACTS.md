# HFT3 Phase Contracts

Contracts are the shared interfaces between parallel workstreams. A phase branch may implement its own contract, but cross-phase consumers must not depend on unreviewed internals.

## Phase 20: Position Monitor

Owned module: `packages/trade_manager/monitor.py`

Required types:

| Type | Required Fields | Notes |
|---|---|---|
| `PositionSnapshot` | `timestamp_ns`, `source`, `positions`, `account_state` | Read-only observed state |
| `ExpectedPosition` | `symbol`, `quantity`, `source_order_intent_ids` | Derived from fills/session state |
| `PositionReconciliationResult` | `timestamp_ns`, `status`, `mismatches`, `max_abs_mismatch` | `status` is `OK`, `MISMATCH`, or `UNKNOWN` |
| `PositionMonitorConfig` | `max_position_mismatch_contracts`, `stale_position_max_ns` | May reference Phase 17 limits |

Hard requirements:

1. No adapter creation.
2. No position flattening.
3. No order submit/cancel/replace.
4. Unknown or stale position data must not pass silently.

## Phase 21: Kill Switch

Owned module: `packages/trade_manager/kill_switch.py`

Trigger families:

| Trigger | Source |
|---|---|
| `max_daily_loss_breach` | risk/account state |
| `max_drawdown_breach` | risk/account state |
| `position_limit_breach` | Phase 20 |
| `runaway_order_rate` | order-state/session metrics |
| `runaway_cancel_rate` | order-state/session metrics |
| `stale_market_data` | production safety context |
| `broker_disconnect` | production safety context |
| `execution_adapter_failure` | future execution boundary/event state |
| `position_mismatch` | Phase 20 |
| `fill_reconciliation_failure` | Phase 23/session state |
| `abnormal_slippage` | Phase 23/session state |
| `abnormal_latency` | latency metrics/session state |

Allowed actions are decisions only until execution is explicitly authorized:

| Action | Phase 21 Behavior |
|---|---|
| `stop_new_orders` | emit decision flag only |
| `cancel_open_orders` | emit requested action only, no adapter call |
| `flatten_positions_if_configured` | emit requested action only, no adapter call |
| `disable_affected_model` | emit model-state decision only |
| `log_event` | write event/audit record |
| `create_incident_report` | write incident artifact request |
| `update_observer_state` | expose read-model event |

## Phase 22: Observer CLI

Owned path: `apps/observer/`

Contract:

1. Reads session artifacts and current read-model snapshots.
2. Displays active models, symbols, positions, order states, risk decisions, kill-switch status, incidents, latency, and PnL.
3. Never mutates Trade Manager state.
4. Never creates adapters.
5. Never calls live/paper/Rithmic paths.

## Phase 23: Session Reporting

Owned module: `packages/trade_manager/session.py`

Required artifacts under `artifacts/sessions/{session_id}/`:

| Artifact | Source |
|---|---|
| `session_manifest.json` | session config and identifiers |
| `active_models.json` | Phase 14 active models |
| `registry_references.json` | promotion references |
| `risk_limits.json` | Phase 17 limits |
| `order_intents.jsonl` | Phase 16 intents |
| `order_state_transitions.jsonl` | Phase 18 transitions |
| `risk_rejections.jsonl` | Phase 17 decisions |
| `fills.jsonl` | future execution/fill events |
| `positions.jsonl` | Phase 20 snapshots |
| `pnl_timeseries.jsonl` | account/session metrics |
| `latency_metrics.json` | latency summary |
| `slippage_metrics.json` | execution quality metrics |
| `incident_log.jsonl` | Phase 21 events |
| `kill_switch_events.jsonl` | Phase 21 decisions |
| `session_metrics.json` | aggregate metrics |
| `session_report.md` | human-readable report |

## Phase 24: Resumability And Failure Safety

Existing phase definition: autonomous-runner resumability and failure safety. This phase is already partial because checkpoint `state.json` exists, but full crash recovery is not tested.

Owned implementation paths:

| Path | Role |
|---|---|
| `packages/hft3/research/run_autonomous.py` | existing checkpoint/resumability implementation |
| `tests/test_autonomous_runner.py` | existing resumability test surface |
| future recovery tests | crash/corruption/idempotency coverage |

Contract:

1. Load checkpoint state and registry evidence safely.
2. Resume or retry idempotently without corrupting registry state.
3. Reject corrupted, partial, or time-regressing artifacts.
4. Produce recovery decision: `SAFE_TO_RESUME`, `MANUAL_REVIEW_REQUIRED`, or `UNRECOVERABLE`.
5. Never route orders during recovery.
6. Future Trade Manager restart work may consume Phase 23 session artifacts, but it does not close Phase 24 unless the autonomous-runner recovery obligations are also satisfied.

## Phase 25: Required Tests And Validation Matrix

Owned files: `docs/project/VALIDATION_MATRIX.md`, validation scripts, missing required tests, and test-support helpers under `tests/`.

Contract:

1. Every phase has targeted tests.
2. Every integration merge has scoped and broad tests.
3. Every skipped test has a documented blocker.
4. Merge-ready status is false if reviewer, tests, graph, or blocker documentation is missing.
