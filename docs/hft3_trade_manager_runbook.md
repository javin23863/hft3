# HFT3 Trade Manager Runbook (Phase 26)

This runbook documents the Trade Manager layer (Phases 14-23). Phase 14 is implemented as the registry-to-Trade-Manager handoff seam. Phase 15 is implemented as side-effect-free signal ingress. Phase 16 is implemented as an inert Trade Manager order-intent schema. Phase 17 is implemented as a risk-decision layer over inert intents. Phase 18 is implemented as an inert order-state machine. Phase 19 is implemented as an inert execution-boundary config/audit seam. Phases 20-23 remain future state.

## 1. How a registered model is handed to the Trade Manager

When a candidate is PROMOTED by the autonomous runner, the runner writes a YELLOW record to the certification registry (Phase 11). The Trade Manager (Phase 14) now:
1. Reads the registry for latest `PROMOTED` models
2. Validates the required `PromotionRecord` fields
3. Loads the model's configuration from `artifacts/runs/{run_id}/manifest.json`
4. Produces an `ActiveModel` handoff object for future Trade Manager sessions

Live/paper operation is future state and must run on CHI404 only. The development workstation remains offline research/replay only and must not route live/paper market data or orders.

## 1A. How active models accept signals (Phase 15)

Phase 15 adds `packages/trade_manager/signals.py` and the `TradeManager` signal-ingress API:
1. Bind an active model to a side-effect-free `SignalSource`
2. Evaluate the source into a normalized `ModelSignal`
3. Validate registry identity, run identity, symbol allowlist, timestamp, side, strength, confidence, expected edge, and latency profile
4. Store the signal in Trade Manager state for Phase 16 conversion

Signals are not orders. Phase 15 does not create `OrderIntent`, does not size orders, does not call risk, and does not create paper/live/Rithmic adapters.

## 2. Required registry fields for operation

The Trade Manager will require these fields from `PromotionRecord` (Phase 11):
- `model_id`, `candidate_id`, `experiment_id`, `run_id`
- `promotion_status` (must be "PROMOTED")
- `allowed_symbols`, `allowed_instruments`, `allowed_order_types`
- `risk_limits_reference`, `capital_allocation_reference`, `kill_switch_reference`
- `latency_profile`, `execution_assumptions`

## 3. Required risk config

The Trade Manager loads risk limits from `configs/risk/limits.yaml` (Phase 17):
- `max_order_size`, `max_position_size`, `max_gross_exposure`, `max_net_exposure`
- `max_daily_loss`, `max_drawdown`, `max_order_rate`, `max_cancel_rate`
- `max_open_orders`, `symbol_eligibility`, `instrument_eligibility`, `model_eligibility`
- `stale_data_max_ns`, `stale_signal_max_ns`, `duplicate_order_check`
- `price_band_check`, `price_band_ticks`, `liquidity_check`, `spread_check`, `max_spread_ticks`, `kill_switch_status`
- `disconnect_grace_ns`, `max_clock_drift_ns`, `max_position_mismatch_contracts`

Phase 17 invokes `packages/execution/production_safety.py::ProductionSafetyOrchestrator.pre_trade_check()` with a supplied adapter context before static order-level rejects, so fatal safety states such as disconnect and daily-loss breach are not masked by ordinary order rejections. It records a `TradeManagerRiskDecision` and never creates adapters, submits orders, cancels orders, replaces orders, or routes to paper/live/Rithmic.

`TradeManagerRiskContext` defaults to enforced `LIVE` checks. Replay/audit-only monitor behavior must be requested explicitly with `execution_mode="REPLAY"`.

Phase 17 does not support a symbol/instrument split in the order-intent envelope. If `TradeManagerRiskContext.instrument` is provided, it must match the intent `symbol`; divergent instrument context is rejected before adapter-facing production-safety checks.

## 4. Required execution adapter config

The Trade Manager loads mode-aware execution-boundary config from `configs/execution/adapter.yaml` (Phase 19):
- `mode`: "REPLAY" | "PAPER" | "LIVE"
- `adapter`: "hftbacktest_simulated_exchange" | "paper_broker" | "live_broker"
- `live_broker`: "rithmic" (LIVE only; stub today)
- `venue`: derived from registry-allowed instruments and symbols
- `order_routing`: "direct" | "aggregated"
- `reconnect_handling`: "automatic" | "manual"
- `heartbeat_interval_sec`: 30
- `route_enabled`: false (Phase 19 rejects true)
- `host_role`: "dev_workstation" by default

Phase 19 validates this config and exposes `TradeManager.prepare_execution_boundary()` as audit metadata only. It does not call `execution.adapter_factory.create_adapter()`, does not instantiate paper/live/Rithmic adapters, does not submit/cancel/replace orders, and does not transition orders to `SENT_TO_EXECUTION`.

## 5. Order-intent schema (Phase 16)

The Trade Manager converts validated model signals into Phase 16 `TradeManagerOrderIntent` objects with 18 fields. This is not the current adapter-level `packages/execution/interfaces.py::OrderIntent` shape.
- `order_intent_id`, `registry_id`, `model_id`, `strategy_id`, `signal_id`
- `timestamp`, `symbol`, `side`, `quantity`, `order_type`
- `limit_price`, `time_in_force`, `expected_edge`, `risk_budget_id`, `reason_code`
- `execution_profile`, `latency_profile`, `source_features_reference`

Model version and config hash remain in the registry/manifest and are not duplicated in the order intent.

## 5A. Risk-decision layer (Phase 17)

Phase 17 adds `packages/trade_manager/risk_layer.py` and `TradeManager.evaluate_order_intent_risk()`:
1. Requires an active model
2. Requires the exact stored Phase 16 `TradeManagerOrderIntent`
3. Rejects tampered or unknown intent envelopes before risk evaluation
4. Runs production-safety monitors first, then applies configured static risk checks such as kill switch, model/symbol/instrument eligibility, order size, position size, open-order count, stale signal, and spread
5. Converts the Trade Manager-local intent to an adapter-level `execution.interfaces.OrderIntent` only as input to production-safety monitors
6. Stores an inert `TradeManagerRiskDecision` for audit/state handoff

Risk approval is not execution approval. Phase 17 does not create an order state machine, execution adapter, session artifact, observer view, or live/paper route.

## 6. Order-state machine (Phase 18)

The Trade Manager tracks every order through 17 documented states:
- `CREATED`, `SENT_TO_RISK`, `RISK_REJECTED`, `RISK_APPROVED`
- `SENT_TO_EXECUTION`, `ACKNOWLEDGED`, `PARTIALLY_FILLED`, `FILLED`
- `CANCEL_REQUESTED`, `CANCELLED`, `REPLACE_REQUESTED`, `REPLACED`
- `BROKER_REJECTED`, `EXPIRED`, `TIMED_OUT`, `ERROR`, `KILLED`

Phase 18 adds `packages/trade_manager/order_state.py` and stores timestamped `TradeManagerOrderTransition` records in `TradeManager.order_state_transitions`:
1. Phase 16 order-intent creation records `CREATED`
2. Phase 17 risk evaluation records `SENT_TO_RISK`
3. Risk rejection records terminal `RISK_REJECTED`
4. Risk approval records `RISK_APPROVED`, but does not send to execution
5. Re-risking an approved order can safely move `RISK_APPROVED -> SENT_TO_RISK -> RISK_REJECTED` before execution
6. Invalid transitions append an `ERROR` transition and raise `OrderStateTransitionError`

Phase 18 is inert. It does not create adapters, submit orders, cancel orders, replace orders, or route to paper/live/Rithmic. `SENT_TO_EXECUTION` and adapter event states are state-machine vocabulary for future consumers only.

## 6A. Execution boundary (Phase 19)

Phase 19 adds `packages/trade_manager/execution_boundary.py`, `configs/execution/adapter.yaml`, and `TradeManager.prepare_execution_boundary()`:
1. Loads and validates the execution-boundary config fail-closed, including direct dataclass construction and unknown YAML keys
2. Requires a stored Phase 16 order intent through the Trade Manager API
3. Reads the latest Phase 17 risk decision and Phase 18 order-state transition for the intent
4. Produces a `TradeManagerExecutionBoundary` audit payload with `can_route=False`, `route_enabled=False`, `adapter_created=False`, and `adapter_instance=None`
5. Marks risk-approved orders as blocked by `PHASE19_INERT_BOUNDARY` rather than sending to execution

Phase 19 is not a live or paper execution adapter. It deliberately leaves `TradeManager.submit_order()`, `cancel_order()`, and `replace_order()` undefined.

## 7. Kill-switch configuration (Phase 21)

The Trade Manager will respect 12 kill-switch triggers:
- `max_daily_loss_breach`, `max_drawdown_breach`, `position_limit_breach`
- `runaway_order_rate`, `runaway_cancel_rate`, `stale_market_data`
- `broker_disconnect`, `execution_adapter_failure`, `position_mismatch`
- `fill_reconciliation_failure`, `abnormal_slippage`, `abnormal_latency`

Configured kill-switch actions (7):
- `stop_new_orders`, `cancel_open_orders`, `flatten_positions_if_configured`
- `disable_affected_model`, `log_event`, `create_incident_report`, `update_observer_state`

## 8. Session artifact path (Phase 23)

Each trading session will produce artifacts under `artifacts/sessions/{session_id}/`:
- `session_manifest.json`, `active_models.json`, `registry_references.json`
- `risk_limits.json`, `order_intents.jsonl`, `order_state_transitions.jsonl`
- `risk_rejections.jsonl`, `fills.jsonl`, `positions.jsonl`, `pnl_timeseries.jsonl`
- `latency_metrics.json`, `slippage_metrics.json`, `incident_log.jsonl`
- `kill_switch_events.jsonl`, `session_metrics.json`, `session_report.md`

## 9. Observer view instructions (Phase 22)

The observer view will be a read-only CLI (not Streamlit):
```bash
python -m hft3.observer view --session-id SESSION_ID
```

The observer will display:
- Active registered models, active symbols, current positions, open orders
- Recent fills, rejected orders, realized PnL, unrealized PnL, total PnL
- Drawdown, risk-limit usage, latency, slippage
- Broker connection status, market-data status, kill-switch status
- Model status, last signal/order/fill timestamps
- Incident log, audit trail, current registry reference

The system will continue operating according to configured rules without requiring observer approval.

## 10. Restart/recovery procedure (Phase 24)

The Trade Manager will support:
- Checkpointing (state.json per session)
- Stage status tracking
- Resumable runs
- Idempotent reruns
- Failed-stage retry
- Artifact validation
- Registry locking (atomic writes via Phase 11)
- Crash-safe promotion
- Safe cleanup of incomplete runs
- Clear error logging
- Safe Trade Manager restart
- Position reconciliation after restart
- Order-state recovery after reconnect

A crash will not corrupt the registry. A crash will not leave order state unknown without creating an incident or reconciliation requirement.

## 11. Test command

```bash
python -m pytest tests/test_trade_manager_*.py -v
```

## 12. Test results

Phase 14: `tests/test_trade_manager_phase14.py` has 6/6 passing tests.
Phase 15: `tests/test_trade_manager_phase15.py` has 9/9 passing tests.
Phase 16: `tests/test_trade_manager_phase16.py` has 10/10 passing tests.
Phase 17: `tests/test_trade_manager_phase17.py` has 41/41 passing tests.
Phase 18: `tests/test_trade_manager_phase18.py` has 23/23 passing tests.
Phase 19: `tests/test_trade_manager_phase19.py` has 22/22 passing tests.

Phases 20-23 tests will be added as monitoring, kill-switch, observer, and session modules are built.

## 13. Known limitations

- **Trade Manager handoff exists.** Phase 14 validates promoted registry records and run manifests but does not route orders.
- **Signal ingress exists.** Phase 15 validates and stores `ModelSignal` envelopes but does not create orders.
- **Order-intent schema exists.** Phase 16 creates inert `TradeManagerOrderIntent` envelopes but does not call risk or execution.
- **Risk-decision layer exists.** Phase 17 calls `production_safety.py` through a supplied adapter context and stores decisions, but it does not route execution.
- **Order-state machine exists.** Phase 18 records inert state transitions and error events, but it does not route execution.
- **Execution boundary exists.** Phase 19 validates config and produces an inert audit payload, but it does not create adapters or route execution.
- **Execution adapter is a stub.** `packages/execution/adapters/live_broker.py` returns `ORDER_REJECTED` with reason `"live_adapter_stub_not_wired"` (Phase 19).
- **Kill switch does not exist.** Phase 21 is not implemented.
- **Observer view does not exist.** Phase 22 is not implemented.
- **Session reporting does not exist.** Phase 23 is not implemented.

## 14. Remaining risks

- **Phase 20-23 remain large.** The Trade Manager still needs sub-modules for position monitoring, kill switch, observer, session reporting, and restart recovery.
- **Rithmic live adapter is not implemented.** The C++ `rithmic_gateway` exists but the Python execution adapter is a stub.
- **Risk/state/boundary decisions are not execution.** Phases 17-19 record approvals/rejections/state transitions and boundary audit metadata only; no adapter lifecycle or session artifacts exist yet.
- **Kill switch is not implemented.** The 12 triggers × 7 actions matrix is not built.
- **Observer view is not implemented.** The read-only CLI does not exist.
- **Session reporting is not implemented.** The 16 session files are not written.
- **Restart recovery is not fully tested.** Phase 24 is partially done (checkpoint state.json exists) but crash recovery is not fully tested.

## Implementation plan

The Trade Manager will be built in 4 milestones:

### Phase 14 complete: Registry handoff
- `packages/trade_manager/manager.py` — validates latest `PROMOTED` records, required operational registry fields, and manifest evidence
- Tests: `tests/test_trade_manager_phase14.py`

### Phase 15 complete: Signal ingress
- `packages/trade_manager/signals.py` — `ModelSignal`, `SignalSource`, and deterministic `StaticSignalSource`
- `packages/trade_manager/manager.py` — bind/evaluate/ingest signal APIs with active-model validation
- Tests: `tests/test_trade_manager_phase15.py`

### Phase 16 complete: Order-intent schema
- `packages/trade_manager/order_intent.py` — 18-field `TradeManagerOrderIntent` schema and pure signal-to-intent conversion
- `packages/trade_manager/manager.py` — `create_order_intent()` stores inert order-intent envelopes only after Phase 15 signal ingestion
- Tests: `tests/test_trade_manager_phase16.py`

### Phase 17 complete: Risk-decision layer
- `packages/trade_manager/risk_layer.py` — configured static checks plus `production_safety.py` pre-trade monitor invocation through supplied context
- `packages/trade_manager/manager.py` — `evaluate_order_intent_risk()` stores inert risk decisions after exact intent-envelope validation
- `configs/risk/limits.yaml` — documented Phase 17 limit config
- Tests: `tests/test_trade_manager_phase17.py`

### Phase 18 complete: Order state machine
- `packages/trade_manager/order_state.py` — 17-state enum, transition validation, terminal states, audit-ready transition records
- `packages/trade_manager/manager.py` — records `CREATED`, `SENT_TO_RISK`, `RISK_APPROVED`, `RISK_REJECTED`, and invalid-transition `ERROR` records without execution routing
- Tests: `tests/test_trade_manager_phase18.py`

### Phase 19 complete: Execution boundary config/audit seam
- `packages/trade_manager/execution_boundary.py` — fail-closed config validation and inert `TradeManagerExecutionBoundary` audit payload
- `packages/trade_manager/manager.py` — `prepare_execution_boundary()` reads stored intent, latest risk decision, and latest order state without routing
- `configs/execution/adapter.yaml` — default `REPLAY` config with `route_enabled: false`
- Tests: `tests/test_trade_manager_phase19.py`

### Future milestone: Real execution adapter routing
- `packages/execution/adapters/live_broker.py` — replace stub with real CHI404-only Rithmic adapter when explicitly authorized
- Tests: `test_execution_adapter_boundary.py` and CHI404 safety gates

### Milestone 3: Position monitoring + kill switch (Phases 20, 21)
- `packages/trade_manager/monitor.py` — 24 metrics
- `packages/trade_manager/kill_switch.py` — 12 triggers × 7 actions
- Tests: `test_position_reconciliation.py`, `test_kill_switch_daily_loss.py`, `test_kill_switch_market_data_stale.py`

### Milestone 4: Observer + session reporting + restart recovery (Phases 22, 23, 24)
- `apps/observer/` — read-only CLI
- `packages/trade_manager/session.py` — 16 session files
- `packages/trade_manager/restart.py` — crash recovery
- Tests: `test_observer_view_read_only.py`, `test_session_report_generation.py`, `test_trade_manager_restart_recovery.py`
