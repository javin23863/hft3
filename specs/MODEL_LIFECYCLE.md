# Model Lifecycle Governance + Autonomous Maintenance

Single source of truth for how a model is tracked from candidate to retirement,
how degradation is detected and acted on, and the rails that make the autonomous
maintenance loop survivable. Implemented in `packages/model_metrics/`,
`packages/autonomy/`, `packages/lifecycle_orchestrator/`, surfaced in
`apps/cockpit/`.

## Build status (honest — what RUNS vs what remains gated)

BUILT + tested (90 tests) + safe-by-default — the loop is now end-to-end wired:
- **Decay driver** `lifecycle_orchestrator.src.run_lifecycle_eval` — evaluates
  observations vs the frozen envelope, always annotates `last_revalidation`
  (detection is always on), and auto-demotes ONLY when autonomy `demote` is enabled.
- **Submit-gate call site** — `trade_manager.risk_layer._static_reject` consults
  `model_metrics.submit_gate` (mtime-cached). A tracked RED/offline model is now
  REJECTED in the real pre-trade path; untracked models pass through (fail-open),
  any error fails open. State is now *enforced*, not just enforce-able.
- **Job worker** `lifecycle_orchestrator.src.worker` — claims pending jobs and runs
  them; routes materialize REAL `run_event_universe --from-stage-a <stub>` commands
  (no placeholders) when the model's cell metadata is present.
- **Scheduler** `scripts/orchestrator_nightly.ps1` + `register_orchestrator_task.ps1`
  (`HFT3ModelMaintenanceNightly`, 05:50, DRY-RUN default).
- **Scratch-registry hook** in `get_active_hypotheses()` (env `HFT3_SCRATCH_HYP_REGISTRY`,
  ids ≥900, default OFF → production set unchanged); F3 proposer `intake.enabled: true`.

STILL GATED / not done (deliberate):
- **Heavy command execution** runs only with `HFT3_ORCH_EXEC=1` (and the gauntlet
  needs NPZ data); a tick is otherwise a record-only no-op.
- **CHI404 dispatch** — the worker runs jobs LOCALLY; SSH+taskset offload to the
  colo box is described in the command dict but not yet executed remotely.
- **No live book** — `EXECUTION_MODE=REPLAY`; "arm" = a registry LIVE transition +
  audit, not a real deployment flip (that stays with the deployment validator).
- Autonomy master enable is OFF by default and the defect-ledger gate fails closed
  (prop-i..iv,g,h OPEN), so nothing auto-arms live today — intended posture.

## Lifecycle states (the registry is the SoT)

`runtime/lifecycle/model_lifecycle.json` (materialized snapshot) +
`transitions.jsonl` (append-only, SHA-256 hash-chained audit) +
`envelopes/<id>.json` (frozen certified envelopes). Mutated only via
`model_metrics.lifecycle.apply_transition` (legal-edge enforced).

```
CANDIDATE → SCREENING → GAUNTLET → CERTIFIED → SHADOW → LIVE
                                                   ↘ DEGRADED ↗ (recover)
DEGRADED ─route→ { ARCHIVED_PAUSED | GAUNTLET | SCREENING | RETIRED }
any non-terminal ─→ QUARANTINED (manual halt / kill / defect)
RETIRED = terminal sink
```

## How degradation is tracked + routed

`model_metrics.decay_detector.evaluate(frozen_envelope, observation)` reuses
`state_engine.classify_model_state` (GREEN/YELLOW/RED) and maps the emitted
trigger names to a route:

| Triggers (real names) | Route | Target | Meaning |
|---|---|---|---|
| ONLY `blocked_regime`/`unapproved_regime` | regime_shift | ARCHIVED_PAUSED | edge intact; auto re-arm when regime returns |
| `slippage_drift`/`fill_rate`/`latency_drift`/… | param_tweak | GAUNTLET | re-optimize params, re-screen |
| unexplained alpha-shape drift | hypothesis_tweak | SCREENING | F3 proposes a variant |
| `drawdown_*`/`feature_training_domain`/… | edge_gone | RETIRED (confirmed by re-validation) | thesis dead |

**Enforcement (graduated):** `decay_detector.submit_allowed(lifecycle_state, model_state)`
is the hard order gate — only LIVE (full) and DEGRADED (YELLOW = 0.5× size) trade;
RED forces flat; QUARANTINED/PAUSED/RETIRED are flat. Fail-closed.

## Autonomous maintenance loop

`lifecycle_orchestrator` scans DEGRADED models and dispatches each route to the
EXISTING entrypoints (gauntlet `run_event_universe.py --from-stage-a`, promotion
gate, shadow) via a durable job queue (`runtime/lifecycle/jobs/`, CHI404 offload
under `taskset`, capture-collision guard). The proposer (slow-tier F3 + the
deterministic `param_proposer`) only PROPOSES; the gauntlet + gate chain dispose.
The quarantine→gauntlet bridge keeps the production registry clean (scratch ids
≥900, env-gated; production registration happens only in `rearm.py`).

## Safety rails (`packages/autonomy/`) — autonomy is subordinate to everything

- **Two-key master enable, OFF by default:** env `HFT3_AUTONOMY_ENABLED=1` AND
  `configs/autonomy.yaml` `enabled:true` + per-action flags; live arm needs
  `rearm.allow_live:true` too. Any parse/IO error ⇒ disabled (fail-closed).
- **Mandatory arm-gate chain** (`gates.AUTONOMY_REQUIRED_GATES`, all BLOCKING):
  master-enable, breaker-closed, GREEN-cert-not-stale, gauntlet, promotion,
  **empty defect ledger**, shadow, embargo, determinism, kill-switch drill. A
  *missing* required gate ⇒ refuse + trip breaker (anti-bypass).
- **Circuit breaker** (`autonomy/breaker.py`): auto-freeze on N failed arms /
  thrash / correlated mass-demotion; **human-only clear**. Unreadable state ⇒ frozen.
- **Global autonomy kill** (`HFT3_AUTONOMY_KILL`), separate from the trading
  kill-switch — stops the LOOP, never the book.
- **Hash-chained `autonomy_audit.jsonl`** — every decision reproducible from the log.

**Current posture:** defect-ledger items prop-i..iv, g, h are OPEN, so the
empty-ledger gate fails closed ⇒ autonomy cannot arm anything live today.

## Cockpit

`/api/lifecycle` (state-lane board) + `/api/autonomy` (master switch, breaker,
gate state, job counts, audit tail). Alerts emit CRIT on QUARANTINED / RETIRED /
AUTONOMY_FROZEN / audit-chain-break. Emergency controls: `POST /api/control/autonomy/stop`
(always allowed — trips breaker), `/unfreeze` (gated + confirm).

## Four-subsystem hardening

| Subsystem | Wired now | Pending (documented) |
|---|---|---|
| R&D | quarantine inert; scratch registry env-gated; fail-closed `defect_ledger_empty` reader | F3 taxonomy+feature-allow-list verifier; single data-access embargo chokepoint |
| Testing/model-dev | gauntlet DSR/PBO/bootstrap/fee-x2 reader fail-closed; gate chain | determinism + staleness re-run AS gates at arm; min-non-zero/min-trades-per-OOS non-degeneracy |
| Implementation | rearm reuses deployment-validator contract; never edits `current` symlink directly | wire `verify_cpp_parity.py` + one-source grep into pre-arm checklist |
| Training | reproducibility fields recorded; eval-gate consumed | cap auto-corroborated golden fraction; per-regime eval required |

The pending items are gate hooks the orchestrator's worker invokes when heavy
backends run; they do not change the safe-by-default posture (autonomy stays OFF
and arm fails closed until each is satisfied).
