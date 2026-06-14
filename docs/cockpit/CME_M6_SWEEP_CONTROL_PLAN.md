# MANDATORY ONTOLOGY GATE: Before every interaction in this project, operate from the Obsidian vault ontology and the provided mathematics/quantitative-finance/HFT PDFs; do not invent codebases, pipelines, models, or methodology outside that authority.

# CME M6 Sweep Control Plan

Date: 2026-06-14. Scope: smallest cockpit control needed to launch the existing CME M6 universe backtest through the existing durable job queue.

This plan is research-only. It does not arm autonomy, does not create a new pipeline, does not touch live/paper routing, and does not claim GREEN unless the resulting artifact passes the existing fail-closed cockpit gates.

## Ontology Constraints

- Source of authority: Obsidian vault hot cache, `AGENTS.md`, `specs/PIPELINE.md`, `specs/LATENCY.md`, `docs/LATENCY_BASELINE.md`, and the checked-in runtime latency artifacts.
- Existing pipeline only: use `scripts/run_event_universe.py` and the current durable control/job-runner path.
- Windows workstation remains offline research only. Live/paper Rithmic order paths belong on CHI404 and stay out of this change.
- No invented research lifecycle, no new model promotion path, no registry writes, no live readiness claims.

## Latency Evidence

Keep these clocks separate. Do not compare milliseconds to microseconds without explicit conversion, and do not use paper ack latency as internal engine speed.

| Measurement | Value | Source | Use |
|---|---:|---|---|
| M6 research injection band / paper ack p99 | `6255.764 us` = `6.255764 ms` | `runtime/latency_reports/order_ack_distribution.json`, `runtime/latency_reports/latency_summary.json` | `--bands 6.255764` |
| Offensive engine loop | `15.3 us/event` | `runtime/latency_reports/latency_truth.json`, `specs/LATENCY.md` | capability context only |
| Offensive accepted baseline tick-to-send | `23.314 us` | `reports/latency_baselines/current_baseline.json` | baseline context |
| Offensive accepted baseline decision-to-send | `22.572 us` | `reports/latency_baselines/current_baseline.json` | baseline context |
| Latest 1000-order decision-to-send p50 / p99 | `12.404 us` / `38.693 us` | `reports/latency_baselines/order_ack_campaign_20260611T072116Z_summary.json` | latest offensive SDK-return context |
| Latest defensive cancel-to-send sample | `14.677 us` | `data/latency_baselines/2026-06-11/order_ack_campaign_20260611T071952Z.jsonl` | defensive send context |
| Defensive cancel ack | `UNMEASURED` (`cancel_to_ack_us=null`, timeout) | 2026-06-11 cancel samples | must remain non-GREEN if required |

## Target Behavior

Add one manual, audited cockpit control to launch a queued full CME M6 sweep from the Pipeline page:

- Job name: `cme_m6_universe_sweep`.
- Host: `laptop`.
- Queue: existing durable job queue and worker.
- Execution default: off unless explicitly launched with cockpit control execution enabled.
- Frontend: Pipeline page button, latest job state, live log tail, and latency evidence panel.

Command shape:

```powershell
scripts/run_event_universe.py `
  --lane cme `
  --bands 6.255764 `
  --symbols MES.v.0,MNQ.v.0,ES.v.0,NQ.v.0,ZN.v.0,ZB.v.0,RTY.v.0 `
  --events-csv packages/data_system/config/events.csv `
  --from-stage-a research_cards/stage_a_full/stage_a_survivors.json `
  --out research_cards/universe_M6_full `
  --workers 12
```

Forbidden command filters:

- No `--max-events`.
- No `--event-type`.
- No `--cells`.
- No `--shard`.

## Backend Changes

- Register `cme_m6_universe_sweep` in the existing cockpit control job catalog.
- Preserve local-origin rejection and `COCKPIT_CONTROL_EXEC` gating.
- Ensure `/api/control/status` lists the job.
- Ensure `POST /api/control/job` accepts `{ "name": "cme_m6_universe_sweep", "confirm": true }`.
- Ensure `/api/control/job/{job_id}/logs` continues to stream the durable worker logfile.
- Add explicit `scripts/run_cockpit.ps1 -EnableControlExec` support that sets `COCKPIT_CONTROL_EXEC=1`; default remains safe/off.

## Pipeline Aggregation

- Prefer `research_cards/universe_M6_full/universe_result.json` for Gauntlet B/M6 when present.
- Fall back to the current smoke artifacts when the full artifact is absent.
- Keep fail-closed blockers non-GREEN:
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
- Job command contains the exact full-scope args above.
- Job command does not include smoke filters.
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
- [ ] Sweep command exactly full-scope and unsharded.
- [ ] Control execution remains opt-in.
- [ ] Full artifact preferred only when present.
- [ ] Smoke fallback remains visibly non-GREEN.
- [ ] Latency units are explicit.
- [ ] Defensive ack is not fabricated.
- [ ] Live/paper routing remains untouched.
- [ ] Tests/build/diff checks pass.
