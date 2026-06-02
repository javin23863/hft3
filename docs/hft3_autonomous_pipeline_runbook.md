# HFT3 Autonomous Pipeline Runbook (Phase 26)

This runbook documents the autonomous research runner and the 10 completed phases of the 26-phase hardening spec.

## 1. Exact CLI command for autonomous research run

```bash
python hft3-research.py --config configs/research/autonomous_hft3.yaml
```

Or, after `pip install -e .`:

```bash
python -m hft3.research.run_autonomous --config configs/research/autonomous_hft3.yaml
```

## 2. Config file used

`configs/research/autonomous_hft3.yaml` — example campaign config with:
- `campaign_id`: unique identifier for the run
- `data`: dataset_id, source, requested/resolved data classes (Phase 6), symbol_universe, event_windows
- `latency_profile`: decision_to_send_us, send_to_ack_us, ack_to_fill_us, fill_model, slippage_bps, fees_per_side_usd, idealized
- `features`: feature_set_id
- `models`: alpha, defensives, structurals (Phase 7)
- `robustness`: monte_carlo, walk_forward
- `scoring`: min_sharpe, max_drawdown
- `registry`: promote_on (PROMOTE/REJECT/QUARANTINE)
- `output`: artifacts_dir
- `research_input`: optional path to Phase 3 intake bundle

## 3. Example research input path

`research_inputs/cpi-mean-reversion/` — a Phase 3 intake bundle with 14 files (source_document_path, extracted_text.md, extracted_equations.json, extracted_tables.json, thesis_summary.json, assumptions.json, required_data.json, required_features.json, proposed_signal_logic.json, proposed_execution_logic.json, parameter_ranges.json, failure_modes.json, testable_hypotheses.json, experiment_translation_notes.json).

## 4. Example run_id

`RUN-20260602T153000Z` — auto-generated if not specified via `--run-id`.

## 5. Artifact directory path

`artifacts/runs/{run_id}/` — 19 required files per Phase 12.

## 6. Example manifest path

`artifacts/runs/RUN-20260602T153000Z/manifest.json` — contains run_id, campaign_id, git_sha, started_at, last_updated_at, completed_stages, artifacts, schema_version, bundle_validation.

## 7. Example report path

`artifacts/runs/RUN-20260602T153000Z/report.md` — 22 sections per Phase 13.

## 8. Example robustness_gates.json path

`artifacts/runs/RUN-20260602T153000Z/robustness_gates.json` — Phase 8 GateResult list with 17 categories.

## 9. Example walk_forward_correlation.json path

`artifacts/runs/RUN-20260602T153000Z/walk_forward_correlation.json` — Phase 10 DoubleWfResult.

## 10. Example promotion_decision.json path

`artifacts/runs/RUN-20260602T153000Z/promotion_decision.json` — decision (PROMOTE/REJECT/QUARANTINE), reason, blocking_gates.

## 11. Example registry update path (if promoted)

`artifacts/runs/RUN-20260602T153000Z/registry_update.json` — decision, promoted_to_certification_registry (bool), certification_status (YELLOW if promoted), reason.

## 12. Example rejected candidate

A candidate is REJECTED when:
- Any BLOCKING gate fails (e.g. data_resolution INELIGIBLE, double_wf BELOW_MIN, artifact_bundle INCOMPLETE)
- The runner's `stage_score_and_decide` sets `decision = "REJECT"`

## 13. Example quarantined candidate

A candidate is QUARANTINED when:
- The runner is in scaffolded mode (Phase 5 backtest integration not yet wired)
- The runner's `stage_score_and_decide` sets `decision = "QUARANTINE"` with reason "Autonomous runner scaffolding: WorkbenchEngine integration is not yet wired."

## 14. Example promoted candidate

A candidate is PROMOTED when:
- All BLOCKING gates pass
- The runner's `stage_score_and_decide` sets `decision = "PROMOTE"`
- The runner's `stage_registry_update` writes a YELLOW record to the certification registry (YELLOW = promoted by autonomous research but not yet certified by T2 backtester certification)

## 15. Test command

```bash
python -m pytest tests/test_autonomous_runner.py tests/test_runner_honesty.py -v
```

## 16. Test results

16/16 passing (11 runner tests + 5 honesty guard tests).

## 17. Known limitations

- **Scaffolded mode**: The runner does not yet invoke the real WorkbenchEngine for backtest, robustness, or walk-forward. All PENDING gates have `pass_fail=False, severity=BLOCKING` so the runner cannot PROMOTE until Phase 5 lands.
- **Single-WF only**: The double-WF correlator (Phase 10) exists but is not yet wired into the campaign runner. The runner emits a PENDING stub.
- **No real scoring**: The runner defaults to QUARANTINE. Real scoring requires Phase 5 backtest metrics.

## 18. Remaining risks

- **Phase 5 (backtest 33-timestamp)**: Not yet implemented. The runner's `stage_backtest` writes stub metrics.
- **Phase 9 (25 robustness checks)**: Most exist in the workbench; ~8 missing.
- **Phase 14-23 (Trade Manager + risk + execution + monitoring + kill switch + observer + sessions)**: All new packages, not yet implemented.
- **Phase 24 (resumability)**: Partially done (checkpoint state.json exists); crash recovery not fully tested.
- **Phase 25 (22 required tests)**: Most exist; ~5 missing.

## Completed phases (10 of 26)

| Phase | Status | Commit |
|---|---|---|
| 1 — Audit | ✅ DONE | `docs/hft3_pipeline_audit.md` |
| 2 — Autonomous runner | ✅ DONE (scaffold) | `9d2eeb5` |
| 3 — Intake 14-file | ✅ DONE | `0656b1d` |
| 4 — LLM boundary | ✅ DONE | `0656b1d` (upgraded in `7340d58`) |
| 6 — L3 data-resolution | ✅ DONE | `4d17e94` |
| 7 — DefensiveModel ABC | ✅ DONE | `e5cc1ef` |
| 8 — Gate schema | ✅ DONE | `6380cc4` |
| 10 — Double-WF correlator | ✅ DONE | `541e631` |
| 11 — Atomic registry | ✅ DONE | `9fdab5f` (extended in `7340d58`) |
| 12 — Artifact bundle | ✅ DONE | `86f5d03` |
| 13 — Reporting 22 sections | ✅ DONE | (in Phase 2 runner) |

## Test scoreboard

**142/142 passing** across 12 test files:
- `tests/test_autonomous_runner.py` (11 tests)
- `tests/test_runner_honesty.py` (5 tests)
- `tests/test_research_intake.py` (11 tests)
- `tests/test_extractors.py` (14 tests)
- `tests/test_defensive_model.py` (12 tests)
- `tests/test_gate_schema.py` (12 tests)
- `tests/test_certification_registry_hardening.py` (19 tests)
- `tests/test_backtester_certification_governance.py` (8 tests)
- `tests/test_promotion_record.py` (12 tests)
- `tests/test_data_class.py` (17 tests)
- `tests/test_workbench/test_double_wf.py` (10 tests)
- `tests/test_artifact_bundle.py` (11 tests)
