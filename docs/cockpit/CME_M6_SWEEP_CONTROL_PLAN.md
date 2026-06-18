# MANDATORY ONTOLOGY GATE: Before every interaction in this project, operate from the Obsidian vault ontology and the provided mathematics/quantitative-finance/HFT PDFs; do not invent codebases, pipelines, models, or methodology outside that authority.

# CME M6 Sweep Control Plan

Date: 2026-06-14. Scope: smallest cockpit control needed to launch the existing CME M6 execution-realism gate through the existing durable job queue after a first-class VectorBT/workbench screening artifact exists.

This plan is research-only. It does not arm autonomy, does not create a new pipeline, does not touch live/paper routing, and does not claim GREEN unless the resulting artifact passes the existing fail-closed cockpit gates.

2026-06-16 correction: the broad Vast `run_event_universe` sweep was the wrong path for discovery. M6 must not be used as the first discovery sweep, and `run_event_universe`, `replay_matrix`, `ReplaySession`, and `run_event_replay` are retired for this implementation. M6 consumes a first-class VectorBT/workbench screening artifact, then enters the new official-HftBacktest-backed realism runner defined in `docs/project/HFTBACKTEST_REALISM_ENGINE_SPEC.md`.

## Ontology Constraints

- Source of authority: Obsidian vault hot cache, `AGENTS.md`, `specs/PIPELINE.md`, `specs/LATENCY.md`, `docs/LATENCY_BASELINE.md`, and the checked-in runtime latency artifacts.
- Existing source-of-truth libraries only: consume the workbench/VectorBT screening artifact first, then use official HftBacktest APIs through the new realism runner. Do not route new work through `packages/backtest_pipeline/src/replay_matrix.py`, `packages/replay/replay_session.py`, `scripts/run_event_replay.py`, or `scripts/run_event_universe.py`.
- Windows workstation remains offline research only. Live/paper Rithmic order paths belong on CHI404 and stay out of this change.
- No invented research lifecycle, no new model promotion path, no registry writes, no live readiness claims.
- No paid or rented replay compute may launch until a VectorBT screening pilot artifact exists, validates, and records the required screening fields below.

## Latency Evidence

Keep these clocks separate. Do not compare milliseconds to microseconds without explicit conversion, and do not use paper ack latency as internal engine speed.

| Measurement | Value | Source | Use |
|---|---:|---|---|
| M6 research injection band / **live** ack p99 | `9810.777 us` = `9.810777 ms` | `runtime/latency_reports/latency_summary.json` (`live_order_latency.authoritative`) | `--bands 9.810777` for live-lane replay |
| M6 research injection band / paper ack p99 | `6255.764 us` = `6.255764 ms` | `runtime/latency_reports/order_ack_distribution.json`, paper section | paper-lane replay only |
| Offensive engine loop | `15.3 us/event` | `runtime/latency_reports/latency_truth.json`, `specs/LATENCY.md` | capability context only |
| Offensive **live** tick-to-send p50 / p99 | `27.291 us` / `60.894 us` | `reports/latency_baselines/live_r01_chicago_baseline.json` | live offensive tactic spacing |
| Offensive paper baseline tick-to-send | `23.314 us` | `reports/latency_baselines/current_baseline.json` | paper baseline context |
| Offensive paper baseline decision-to-send | `22.572 us` | `reports/latency_baselines/current_baseline.json` | paper baseline context |
| Latest 1000-order decision-to-send p50 / p99 | `12.404 us` / `38.693 us` | `reports/latency_baselines/order_ack_campaign_20260611T072116Z_summary.json` | paper offensive SDK-return context |
| Defensive **live** cancel-to-send p50 / p99 | `13.074 us` / `18.906 us` | `reports/latency_baselines/live_r01_chicago_baseline.json` | live defensive fire speed |
| Defensive paper cancel-to-send sample | `14.677 us` | `data/latency_baselines/2026-06-11/order_ack_campaign_20260611T071952Z.jsonl` | paper defensive send context |
| Defensive cancel ack | `UNMEASURED` (`cancel_to_ack_us=null`, timeout) | live + paper cancel samples | must remain non-GREEN if required |

## Target Behavior

Add one manual, audited cockpit control to launch a queued selected CME M6 execution-realism gate from the Pipeline page:

- Job name: `cme_m6_universe_sweep`.
- Host: laptop for the cockpit control path; rented CPU host for one-time M6
  recovery/offload runs after screening is complete.
- Queue: existing durable job queue and worker.
- Execution default: off unless explicitly launched with cockpit control execution enabled.
- Frontend: Pipeline page button, latest job state, live log tail, and latency evidence panel.
- Local laptop default: `--workers 12`.
- Rented 256-vCPU recovery host target: `--workers 230` or higher if the host remains stable. Do not use `--workers 192` on a 256-vCPU rental without measured CPU, memory, filesystem, or Python-startup evidence proving that more workers reduce throughput.
- Launch precondition: a validated screening pilot artifact must be present and hash-pinned. Without it, the cockpit control must remain disabled or visibly blocked.

Required screening artifact fields:

```text
screening_backend=vectorbt
vectorbt_version
vectorbt_engine=rust|numba|auto
screening_artifact_hash
candidate_ids
candidate_reasons
promoted_ids
promoted_reasons
rejected_ids
rejected_reasons
no_lookahead_signal_shift_proof
license_review
workbench_run_id
feature_set_id
events_csv_hash
lake_manifest_hash
created_at_utc
```

Replay command shape:

- Not authorized yet. The valid command must be created by the new
  official-HftBacktest-backed realism runner, not by `run_event_universe`.
- Until that runner exists, the cockpit M6 launch remains blocked.
- Any paid/Vast run launched through retired hft3 replay entrypoints is invalid,
  even when restricted to promoted IDs.

Paid-compute hard stop:

- Do not start Vast or other rented replay compute for discovery.
- Do not run a broad all-candidate `run_event_universe` sweep as a substitute
  for VectorBT/workbench screening.
- Do not launch expensive replay until the screening pilot artifact exists,
  validates its schema, and records `screening_artifact_hash`,
  `no_lookahead_signal_shift_proof`, and `license_review`.
- `replay_matrix`, `ReplaySession`, `run_event_replay`, and
  `run_event_universe` are retired for this implementation and must not be used
  as the HftBacktest realism runner.

## Backend Changes

- Do not register or launch `cme_m6_universe_sweep` through the retired
  `run_event_universe` path.
- Preserve local-origin rejection and `COCKPIT_CONTROL_EXEC` gating for future
  controls.
- The next backend job must be the new official-HftBacktest-backed realism
  runner after the VectorBT screening artifact exists.
- Add cockpit controls only after the runner writes the required source-lock,
  data-validation, latency-model, fill/queue-model, and replay-summary artifacts.

## Pipeline Aggregation

- Require the screening artifact before treating `universe_M6_full` as an M6
  candidate source. If the screening artifact is missing, stale, or hash-mismatched,
  the pipeline must report `BLOCKED_SCREENING_ARTIFACT_REQUIRED`.
- Prefer `research_cards/universe_M6_full/universe_result.json` for Gauntlet B/M6 when present.
- Fall back to the current smoke artifacts when the full artifact is absent.
- Keep fail-closed blockers non-GREEN:
  - missing VectorBT/workbench screening pilot artifact;
  - missing or mismatched screening artifact hash;
  - missing no-lookahead signal shift proof;
  - missing license review;
  - smoke/bounded scope;
  - missing explicit symbols;
  - non-canonical events CSV;
  - coverage skips;
  - invalid PBO;
  - insufficient CSCV;
  - stale certification;
  - malformed thresholds;
  - missing latency evidence;
  - defensive ack marked required but `UNMEASURED`.

## Frontend Changes

Add a small Pipeline page control panel:

- Show `COCKPIT_CONTROL_EXEC` from `/api/control/status`.
- Show latest `cme_m6_universe_sweep` job state.
- Launch button posts `{ "name": "cme_m6_universe_sweep", "confirm": true }`.
- Poll `/api/control/job/{job_id}/logs` and display live tail.
- Disable launch when execution is gated off or a sweep is pending/running.

Add a latency evidence panel:

- `ack_p99_us=6255.764`.
- `m6_band_ms=6.255764`.
- `offensive_engine_us=15.3`.
- `offensive_baseline_tick_to_send_us=23.314`.
- `offensive_latest_decision_to_send_p99_us=38.693`.
- `defensive_cancel_to_send_us=14.677`.
- `defensive_cancel_ack_status=UNMEASURED`.
- Label `6.255764 ms` as the paper ack p99 research injection band, not internal runtime.

## Tests

Backend:

- `cme_m6_universe_sweep` appears in `/api/control/status`.
- Job command requires a validated VectorBT/workbench screening artifact.
- Job command includes the screening artifact hash and promoted IDs source.
- Job command rejects a bare Stage A survivor file as the only scope source.
- Job command does not include smoke filters or broad-discovery flags.
- Remote-origin control rejection remains unchanged.
- Pipeline prefers `universe_M6_full` when present.
- Pipeline falls back to smoke when full artifact is absent.
- Latency evidence reports defensive ack as `UNMEASURED`.
- Any required defensive ack readiness gate stays non-GREEN.

Frontend/build:

- Pipeline page renders with control exec off.
- Pipeline page renders pending/running job state.
- Pipeline page renders log tail.
- Pipeline page renders latency evidence without claiming live readiness.

Verification commands:

```powershell
python -B -m pytest -q apps\cockpit\backend\tests\test_cockpit.py -p no:cacheprovider
npm run build --prefix apps\cockpit\frontend
git diff --check
```

## Acceptance Checklist

- [ ] No new pipeline code invented.
- [ ] Existing durable job queue reused.
- [ ] VectorBT/workbench screening pilot artifact exists before any paid replay run.
- [ ] Screening artifact records `screening_backend=vectorbt`.
- [ ] Screening artifact records `vectorbt_version` and `vectorbt_engine=rust|numba|auto`.
- [ ] Screening artifact records candidate, promoted, and rejected IDs with reasons.
- [ ] Screening artifact records `screening_artifact_hash`.
- [ ] Screening artifact records `no_lookahead_signal_shift_proof`.
- [ ] Screening artifact records `license_review`.
- [ ] Replay command is selected/promoted scope, not broad discovery scope.
- [ ] Sweep command is unsharded unless a later accepted checkpoint/reconciliation design says otherwise.
- [ ] Control execution remains opt-in.
- [ ] Full artifact preferred only when present.
- [ ] Smoke fallback remains visibly non-GREEN.
- [ ] Latency units are explicit.
- [ ] Defensive ack is not fabricated.
- [ ] Live/paper routing remains untouched.
- [ ] Tests/build/diff checks pass.

## 2026-06-16 Scope Correction

This M6 control launches an event-window universe sweep. It is not a continuous
intraday opportunity search and it is not a context-feature uplift run unless
the artifact explicitly contains context-ablation rows. Use
[../project/OPPORTUNITY_RESEARCH_SPEC.md](../project/OPPORTUNITY_RESEARCH_SPEC.md)
for the broader product research contract.

Available-data rule:

- Missing data blocks only the dependent event-symbol, feature, model, or
  opportunity unit.
- Missing options fixing dates or strict quote gaps do not block CME futures
  event-window units that do not use those options rows.
- The cockpit must show skips/rejections as dependency-scoped, not as a global
  stop, unless a selected run declares the missing data mandatory for all units.

Expensive-run lessons from the Vast sweep:

- [ ] Broad M6 `run_event_universe` was the wrong discovery path and is retired
  for this implementation.
- [ ] Discovery must start with a first-class VectorBT/workbench screening
  artifact; M6 replay must use the new official-HftBacktest-backed realism
  runner.
- [ ] Paid compute is blocked until the VectorBT screening pilot artifact and
  official-HftBacktest realism runner both exist.
- [ ] Add a preflight artifact before launch: unit count, reusable/new count,
  expected scope hashes, ETA, host CPU count, reserved core count, worker count,
  wall-clock cap, and abort rule.
- [ ] Use rented compute aggressively. On the 256-vCPU Vast host, the planned
  worker count is `230`; lower counts require measured bottleneck evidence or
  explicit owner acceptance.
- [ ] Add a scope contract: symbols, events CSV hash, Stage A hash, lake
  manifest hash, active run id, expected units, skipped units, and shard
  reconciliation.
- [ ] Add durable `progress.json`: run id, expected/completed/reused/skipped/
  errored counts, ETA, checkpoint hash, and active job id.
- [ ] Add a stall rule for no row advance while workers are present.
- [ ] If the run stalls with workers alive, either document the bottleneck or
  restart/resume with the 256-vCPU worker target after checkpoint validation.
- [ ] Treat `--workers` as execution topology, not scientific identity, but keep
  source-hash mismatches fail-closed. If code must be synced during an active
  checkpoint, first back up checkpoint/context files and document why the change
  is metadata/control-only before allowing reuse.
- [ ] Label Python-only replay output as research-candidate evidence until the
  relevant C++ parity and certification stamps are valid.
