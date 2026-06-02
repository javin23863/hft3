# HFT3 Trade Manager Runbook (Phase 26)

This runbook documents the Trade Manager layer (Phases 14-23). **These phases are not yet implemented.** This runbook describes the future state.

## 1. How a registered model is handed to the Trade Manager

When a candidate is PROMOTED by the autonomous runner, the runner writes a YELLOW record to the certification registry (Phase 11). The Trade Manager (Phase 14) will:
1. Read the registry for PROMOTED models
2. Load the model's configuration from `artifacts/runs/{run_id}/manifest.json`
3. Activate the model for live/paper trading

## 2. Required registry fields for operation

The Trade Manager will require these fields from `PromotionRecord` (Phase 11):
- `model_id`, `candidate_id`, `experiment_id`, `run_id`
- `promotion_status` (must be "PROMOTED")
- `allowed_symbols`, `allowed_instruments`, `allowed_order_types`
- `risk_limits_reference`, `capital_allocation_reference`, `kill_switch_reference`
- `latency_profile`, `execution_assumptions`

## 3. Required risk config

The Trade Manager will load risk limits from `configs/risk/limits.yaml` (Phase 17):
- `max_order_size`, `max_position_size`, `max_gross_exposure`, `max_net_exposure`
- `max_daily_loss`, `max_drawdown`, `max_order_rate`, `max_cancel_rate`
- `max_open_orders`, `symbol_eligibility`, `instrument_eligibility`, `model_eligibility`
- `stale_data_check`, `stale_signal_check`, `duplicate_order_check`
- `price_band_check`, `liquidity_check`, `spread_check`, `kill_switch_status`

## 4. Required execution adapter config

The Trade Manager will load execution adapter config from `configs/execution/adapter.yaml` (Phase 19):
- `broker`: "rithmic" | "databento" | "simulated"
- `venue`: "CME" | "NYSE" | "NASDAQ"
- `order_routing`: "direct" | "aggregated"
- `reconnect_handling`: "automatic" | "manual"
- `heartbeat_interval_sec`: 30

## 5. Order-intent schema (Phase 16)

The Trade Manager will convert model signals into `OrderIntent` objects with 18 fields:
- `order_intent_id`, `registry_id`, `model_id`, `strategy_id`, `signal_id`
- `timestamp`, `signal_timestamp`, `symbol`, `side`, `quantity`
- `order_type`, `limit_price`, `time_in_force`, `expected_edge`, `holding_period_estimate`
- `risk_budget_id`, `reason_code`, `execution_profile`, `latency_profile`
- `source_features_reference`, `model_version`, `config_hash`

## 6. Order-state machine (Phase 18)

The Trade Manager will track every order through 17 states:
- `CREATED`, `SENT_TO_RISK`, `RISK_REJECTED`, `RISK_APPROVED`
- `SENT_TO_EXECUTION`, `ACKNOWLEDGED`, `PARTIALLY_FILLED`, `FILLED`
- `CANCEL_REQUESTED`, `CANCELLED`, `REPLACE_REQUESTED`, `REPLACED`
- `BROKER_REJECTED`, `EXPIRED`, `ERROR`, `KILLED`

Every state transition will be timestamped and logged. Invalid transitions will fail safely and write an error event.

## 7. Kill-switch configuration (Phase 21)

The Trade Manager will respect 12 kill-switch triggers:
- `max_daily_loss_breach`, `max_drawdown_breach`, `position_limit_breach`
- `runaway_order_rate`, `runaway_cancel_rate`, `stale_market_data`
- `broker_disconnect`, `execution_adapter_failure`, `position_mismatch`
- `fill_reconciliation_failure`, `abnormal_slippage`, `abnormal_latency`
- `risk_layer_failure`

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

**Not yet implemented.** Phase 14-23 tests will be added when the Trade Manager is built.

## 13. Known limitations

- **Trade Manager does not exist yet.** Phases 14-23 are not implemented.
- **Risk layer is not wired.** `packages/execution/production_safety.py` exists but is not called by the Trade Manager (Phase 17).
- **Execution adapter is a stub.** `packages/execution/adapters/live_broker.py` returns `ORDER_REJECTED` with reason `"live_adapter_stub_not_wired"` (Phase 19).
- **Order state machine does not exist.** Phase 18 is not implemented.
- **Kill switch does not exist.** Phase 21 is not implemented.
- **Observer view does not exist.** Phase 22 is not implemented.
- **Session reporting does not exist.** Phase 23 is not implemented.

## 14. Remaining risks

- **Phase 14-23 are large.** The Trade Manager is a new package with ~10 sub-modules (order intent, risk layer, execution adapter, order state machine, position monitoring, kill switch, observer, session reporting, restart recovery).
- **Rithmic live adapter is not implemented.** The C++ `rithmic_gateway` exists but the Python execution adapter is a stub.
- **Risk layer is not wired.** The 5 production safety monitors exist but are not called.
- **Kill switch is not implemented.** The 12 triggers × 7 actions matrix is not built.
- **Observer view is not implemented.** The read-only CLI does not exist.
- **Session reporting is not implemented.** The 16 session files are not written.
- **Restart recovery is not fully tested.** Phase 24 is partially done (checkpoint state.json exists) but crash recovery is not fully tested.

## Implementation plan

The Trade Manager will be built in 4 milestones:

### Milestone 1: Order intent + risk layer (Phases 16, 17)
- `packages/trade_manager/order_intent.py` — OrderIntent dataclass with 18 fields
- `packages/trade_manager/risk_layer.py` — wire `production_safety.py` monitors
- Tests: `test_order_intent_schema.py`, `test_risk_layer_rejects_invalid_order.py`

### Milestone 2: Order state machine + execution adapter (Phases 18, 19)
- `packages/trade_manager/order_state.py` — 17-state machine
- `packages/execution/adapters/live_broker.py` — replace stub with real Rithmic adapter
- Tests: `test_order_state_machine.py`, `test_execution_adapter_boundary.py`

### Milestone 3: Position monitoring + kill switch (Phases 20, 21)
- `packages/trade_manager/monitor.py` — 24 metrics
- `packages/trade_manager/kill_switch.py` — 12 triggers × 7 actions
- Tests: `test_position_reconciliation.py`, `test_kill_switch_daily_loss.py`, `test_kill_switch_market_data_stale.py`

### Milestone 4: Observer + session reporting + restart recovery (Phases 22, 23, 24)
- `apps/observer/` — read-only CLI
- `packages/trade_manager/session.py` — 16 session files
- `packages/trade_manager/restart.py` — crash recovery
- Tests: `test_observer_view_read_only.py`, `test_session_report_generation.py`, `test_trade_manager_restart_recovery.py`
