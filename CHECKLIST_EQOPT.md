# Checklist: Equities + Options Lane Integrity (PLAN_EQOPT)

Track each item. Mark `[x]` only when done and verified.

## 1. Doc drift reconciliation

- [ ] Update `AGENTS.md` §"Low-float equities lane" — mark as operational; keep
      "data isolation" wording; remove the gate-blocking implication.
- [ ] Update `docs/research/LOW_FLOAT_RUNNER.md` — mark as operational runbook;
      replace "quarantined" with "isolated from production CME `data/npz/`".
- [ ] Verify `docs/research/EQUITY_OPTIONS_DATA_MAP.md` paths are unchanged.

## 2. Ontology objects (small extension)

- [ ] `equities_lane/src/ontology/session_context.py` — `EquitySessionContext`.
- [ ] `equities_lane/src/ontology/option_snapshot.py` — `OptionChainSnapshotAtDecision`,
      `OptionContractAtDecision`.
- [ ] `equities_lane/src/ontology/payoff.py` — `StockOptionPayoffComparison`,
      `StockOptionRouteDecision`.
- [ ] `equities_lane/src/ontology/citations.py` — claim id + PDF citation sidecar
      helpers (reuse existing OpenFoundry style if any).
- [ ] Tests for ontology object construction + validation.

## 3. Point-in-time leakage filter

- [ ] `equities_lane/src/integrity/pit_filter.py` — filters equity ticks and
      option quotes to `ts_ns <= decision_ts_ns`.
- [ ] Float metadata `as_of_date` must be `<= session_date`.
- [ ] Reject contaminated runs with explicit `rejection_reason`.
- [ ] Tests proving future-dated option/equity data is rejected.

## 4. Route comparison layer

- [ ] `equities_lane/src/route/comparator.py` — computes stock EV, option EV,
      combined EV, and selects route.
- [ ] All four routes reachable in tests with deterministic inputs.
- [ ] Decision record includes all required fields per developer prompt.

## 5. Float metadata as_of check

- [ ] `equities_lane/src/integrity/float_check.py` — verify
      `data/equities/metadata/float_pit.csv` rows have valid as_of date.

## 6. Workbench status display

- [ ] Audit workbench UI for any stale "quarantined" wording in equities
      pipeline status display.
- [ ] Confirm UI reads from canonical manifest/report files.

## 7. Tests

- [ ] `test_route_reachability.py` — stock_only, option_only, stock_and_option,
      no_trade all reachable.
- [ ] `test_pit_filter.py` — future option rejected, future equity rejected,
      float as_of violation rejected.
- [ ] `test_ontology_validation.py` — ungrounded claims rejected.
- [ ] `test_doc_parity.py` — AGENTS.md / LOW_FLOAT_RUNNER.md do not contain
      stale "blocked" / "cannot run" wording.

## 8. Operational experiment

- [ ] Run full equities+options experiment across all 13 sessions.
- [ ] Produce per-session report with route decision, lineage, leakage status.
- [ ] Aggregate to `runtime/data_audits/equities_options_real_run.json`.
- [ ] Engineering report committed.

## 9. Final delivery

- [ ] All commits pushed.
- [ ] Engineering report in `runtime/data_audits/eqopt_engineering_report.md`.
