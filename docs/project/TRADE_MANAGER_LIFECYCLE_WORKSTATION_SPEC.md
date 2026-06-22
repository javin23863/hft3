# MANDATORY ONTOLOGY GATE: Before using this document, operate from the Obsidian vault ontology and the provided mathematics/quantitative-finance/HFT PDFs; do not invent lifecycle methodology outside that authority.

# Trade Manager Lifecycle Workstation Specification

Status: v0.1 repo-facing contract (Slice 1). Defines operator-visible lifecycle
behavior, Cockpit data contracts, submit-gate policy, re-entry routes, restart
recovery expectations, and workstation boundaries. This is not evidence that every
downstream UI/API slice is implemented yet.

Parent plan: vault `operations/2026-06-22 Trade Manager lifecycle coding execution plan.md` (Slice 1).

## Purpose

After robustness testing and promotion, a model enters a governed lifecycle. The
**lifecycle workstation** is the operator surface (Cockpit + Trade Manager read
paths) that answers:

1. What state is each model in?
2. May it submit orders right now, and at what size?
3. What evidence caused the last transition?
4. What gate must pass before re-arm?
5. What happens after process restart?

This specification connects the persisted lifecycle registry
(`packages/model_metrics/lifecycle.py`), decay/submit policy
(`packages/model_metrics/decay_detector.py`, `packages/model_metrics/submit_gate.py`),
the decay driver (`packages/lifecycle_orchestrator/src/run_lifecycle_eval.py`),
re-arm gates (`packages/lifecycle_orchestrator/src/rearm.py`), Cockpit aggregation
(`apps/cockpit/backend/aggregate/lifecycle.py`), and Trade Manager risk
(`packages/trade_manager/risk_layer.py`).

## Source hierarchy

| Layer | Authority | Use |
|---|---|---|
| Vault ontology | `library/14 Model Lifecycle and Governance.md`, `wiki/hot.md`, autonomy doctrine notes | Inventory, monitoring, action limits, demotion/quarantine, no live tweak after holdout failure |
| HFT3 authority PDFs | `docs/references/` (see vault library note) | Filtration, walk-forward, no lookahead, topology |
| Code (current truth) | `lifecycle.py`, `decay_detector.py`, `submit_gate.py`, `rearm.py`, `run_lifecycle_eval.py` | States, transitions, routing, enforcement. Note: `decay_detector.py` module header STATUS line is stale — submit gate + decay driver are wired; header update deferred to Slice 2. |
| Trade Manager runbook | `docs/hft3_trade_manager_runbook.md` | Phases 14–23 inert boundaries, session artifacts, future restart work |
| Execution plan | vault `operations/2026-06-22 Trade Manager lifecycle coding execution plan.md` | Slice sequencing; Slice 2+ extend this contract in code |

## Topology and no-live-routing boundary

Per `BLUEPRINT.md` §4 and `docs/hft3_trade_manager_runbook.md`:

| Host | Role |
|---|---|
| **CHI404** | CME lane: live/paper market data, order submit, capture, tuning |
| **Dev workstation** | Offline research, pytest, git, SSH/sync, Cockpit read of local artifacts |

**Non-negotiable workstation contract:**

1. Cockpit and Workbench on the dev workstation are **read-only observers** of
   lifecycle registry and session artifacts unless explicitly running REPLAY mode.
2. No dev workstation path may create paper/live/Rithmic adapters, submit orders,
   cancel orders, flatten positions, or flip live-engine symlinks.
3. `TradeManager.prepare_execution_boundary()` remains inert (`can_route=False`,
   `route_enabled=False`) until an explicit future phase authorizes routing on CHI404.
4. Lifecycle state changes from autonomy (`demote`, `rearm`) require explicit
   autonomy rails enabled; detection/annotation may run without state moves.
5. UI must never imply green/live routing when `host_role=dev_workstation` or when
   execution boundary audit shows `PHASE19_INERT_BOUNDARY`.

## Persistence layout

Authoritative store: `runtime/lifecycle/` (override: `HFT3_LIFECYCLE_DIR` in tests).

| Artifact | Role |
|---|---|
| `transitions.jsonl` | Append-only, SHA-256 hash-chained audit log (**source of truth**) |
| `model_lifecycle.json` | Materialized snapshot (rebuildable from log via `rebuild_registry_from_log()`) |
| `envelopes/<id>.json` | Frozen certified `ModelBehaviorEnvelope` snapshots (ML2) |
| `transitions.head.json` | O(1) tail pointer for append |

Writes are atomic (temp+replace) and guarded by a token lock file so Cockpit can
read mid-write without tearing.

## Lifecycle states — definitions

Schema: `model_lifecycle.schema.v1`. Terminal state: `RETIRED` only.

| State | Operator meaning | Trade Manager may | Cockpit shows |
|---|---|---|---|
| **CANDIDATE** | Registered; not yet in screening | Nothing (untracked or pre-promotion) | Grey / unknown funnel |
| **SCREENING** | VectorBT or broad cheap prefilter in progress | Nothing live | Running (blue) |
| **GAUNTLET** | Robustness / DSR-PBO / fee-stress gates | Nothing live | Running (blue) |
| **CERTIFIED** | Passed promotion gates; not yet shadow/live | Nothing live | OK (green dot) |
| **SHADOW** | Paper/shadow evidence collection before live arm | Shadow observation only; no live submit | Running (blue) |
| **LIVE** | Armed for live/paper submit on CHI404 (when routing authorized) | Submit if submit-gate allows | OK (green); health counts |
| **DEGRADED** | Lifecycle demotion from LIVE; still trade-capable if model_state allows | Submit only if model_state allows (see submit gate); lifecycle alone does not cap size | Stale/amber |
| **QUARANTINED** | Manual halt, kill-switch, defect, or infra fault; flat | **No submit** | Fail/red |
| **ARCHIVED_PAUSED** | Pulled offline; edge may be intact (regime shift) | **No submit** | Stale/amber |
| **RETIRED** | Terminal; thesis dead or operator retired | **No submit** | Missing/grey |

**Model state** (orthogonal to lifecycle state): `GREEN` / `YELLOW` / `RED` from
`state_engine.classify_model_state` against the **frozen certified envelope**.
Stored on the record as `last_revalidation.model_state`. Drives submit size even
when lifecycle state is still `LIVE` or `DEGRADED`.

## Legal transitions

From `packages/model_metrics/lifecycle.py` (`LEGAL_TRANSITIONS` + global quarantine edge):

```text
CANDIDATE     -> SCREENING | RETIRED
SCREENING     -> GAUNTLET | RETIRED
GAUNTLET      -> CERTIFIED | RETIRED | GAUNTLET (re-tune) | SCREENING
CERTIFIED     -> SHADOW
SHADOW        -> LIVE
LIVE          -> DEGRADED
DEGRADED      -> LIVE | GAUNTLET | SCREENING | ARCHIVED_PAUSED | RETIRED
QUARANTINED   -> ARCHIVED_PAUSED | GAUNTLET | SCREENING | CERTIFIED | RETIRED
ARCHIVED_PAUSED -> SHADOW | LIVE | RETIRED
RETIRED       -> (none)

Any non-terminal state -> QUARANTINED   (manual halt / kill-switch / defect)
New model creation     -> CANDIDATE only
```

**Forbidden shortcuts (policy):**

- `SHADOW -> LIVE`, `DEGRADED -> LIVE`, or `ARCHIVED_PAUSED -> LIVE` without
  `rearm.attempt_rearm()` G0–G8 gate chain (decay driver must not direct-arm;
  `LEGAL_TRANSITIONS` lists these edges but re-arm is the only authorized arm path).
- Holdout-failure **live parameter tweak** — route to `GAUNTLET` or `SCREENING`, not
  silent `LIVE` (Arnott/Harvey backtesting protocol; vault `library/14`).
- Auto-retire from a single observation — `edge_gone` target is provisional; orchestrator
  re-validates on model regime before `RETIRED`.

**Annotation (no state change):** `lifecycle.annotate()` updates fields such as
`last_revalidation` with an audit entry. Used by `run_lifecycle_eval` so submit gate
reads fresh model_state without moving lifecycle state.

## Submit-gate behavior

Call site: `model_metrics.submit_gate.model_submit_decision(model_id)` →
`(allowed, size_factor, reason)`.

Consulted from `packages/trade_manager/risk_layer.py` before static risk checks.
Reject reason code: `LIFECYCLE_STATE_BLOCKS_TRADE`.

**Enforcement gap (current code):** `risk_layer.py` uses only the `allowed`
boolean from `model_submit_decision`; it discards `size_factor` (`allowed, _size,
reason = …`). YELLOW therefore blocks at RED but does **not** yet scale order
quantity to 0.5×. Slice 6+ must wire `size_factor` into sizing or document
REPLAY-only until CHI404 live path hardens (vault `library/14` action limits).

### By lifecycle state

| Lifecycle state | allowed | size_factor | Notes |
|---|---:|---:|---|
| Untracked (not in registry) | yes | 1.0 | reason=`untracked` |
| LIVE | yes* | 1.0* | *subject to model_state |
| DEGRADED | yes* | 1.0* | *size follows model_state only (GREEN 1.0, YELLOW 0.5 policy — sizing not wired in risk_layer yet) |
| QUARANTINED, ARCHIVED_PAUSED, RETIRED, CANDIDATE, SCREENING, GAUNTLET, CERTIFIED, SHADOW | no | 0.0 | Flat |
| Unknown state | no | 0.0 | Fail-closed in `submit_allowed` |

### By latest model_state (within LIVE/DEGRADED)

From `decay_detector.enforcement_for_state`:

| model_state | action | size_factor | raise_threshold |
|---|---|---:|---|
| GREEN | allow | 1.0 | no |
| YELLOW | size_down | 0.5 | yes |
| RED | flatten_offline | 0.0 | yes |

Combined rule (`submit_allowed(lifecycle_state, model_state)`):

```text
if lifecycle_state not in {LIVE, DEGRADED}: return (False, 0.0)
enf = enforcement_for_state(model_state)
return (enf.size_factor > 0.0, enf.size_factor)
```

Reason string format: `lifecycle=<state> model_state=<GREEN|YELLOW|RED>`.

**Detection vs demotion:** `run_lifecycle_eval` **always** annotates
`last_revalidation` (submit gate enforces immediately). Auto **state** demotion
occurs only when autonomy action `demote` is enabled.

**Risk-layer error policy (current):** if `model_submit_decision` raises, risk
layer catches and **passes through** (fail-open). Workstation must surface registry
read errors in Cockpit health; Slice 6+ may tighten to fail-closed on CHI404 live path.

## Demotion routes and re-entry

Routes (`lifecycle.ROUTES`): `regime_shift`, `param_tweak`, `hypothesis_tweak`, `edge_gone`.

Trigger classification: `decay_detector.route_demotion(triggers)` from
`state_engine` trigger names.

| Route | Typical triggers | Target state | Operator meaning | Re-entry path |
|---|---|---|---|---|
| **regime_shift** | Only `blocked_regime`, `unapproved_regime` | ARCHIVED_PAUSED | Edge intact; wrong regime — pause, await re-arm | Re-arm when regime approved + gates pass |
| **param_tweak** | Execution drift (slippage, fill_rate, latency_*, placement_*, trade_count_*, order_reject_rate, …) | GAUNTLET | Re-fit parameters; not a thesis change | GAUNTLET self-loop or forward to CERTIFIED→SHADOW→LIVE |
| **hypothesis_tweak** | Unexplained alpha-shape / non-execution drift | SCREENING | New hypothesis variant (F3 path) | SCREENING→GAUNTLET→… |
| **edge_gone** | drawdown_*, feature_training_domain, high_confidence_wrong, book_correlation | RETIRED (provisional) | Thesis may be dead | Orchestrator confirms; may reroute to GAUNTLET/SCREENING instead |
| **(none — infra)** | Only infra triggers (`async_ack_stale_state`, `data_freshness`, `low_latency_execution_path_audit`, `envelope_inactive`) | QUARANTINED (via driver) | Ops fault, not research | Manual quarantine review |

**Mixed-trigger precedence** (`route_demotion`): empty triggers → no route; if
any `_EDGE_GONE` trigger present → `edge_gone` (wins over execution/hypothesis);
else if only regime triggers → `regime_shift`; else if only infra → `None`
(quarantine); else if any execution trigger → `param_tweak`; else →
`hypothesis_tweak`.

Record fields:

- `demotion`: `{reason, from_state, …}` on transition
- `reentry_routing`: `{route, decided_at, …}`
- `last_revalidation`: `{model_state, route, triggers[], ts}`

## Re-arm evidence requirements (G0–G8)

Only `rearm.attempt_rearm()` may transition `SHADOW` / `DEGRADED` / `ARCHIVED_PAUSED` → `LIVE`.
Requires autonomy master enable + per-model `rearm.allow_live`.

| Gate ID | Name | Evidence |
|---|---|---|
| G0 | `master_enable` | Autonomy master on; `rearm.allow_live` for model |
| G0′ | `breaker_closed` | Circuit breaker not tripped |
| G1 | `green_cert_not_stale` | Certification GREEN, not stale, promotion_eligible |
| G2 | `gauntlet_pass` | DSR/PBO/bootstrap/fee×2 + Holm |
| G3 | `promotion_gate` | PromotionGate pass + lane capability profile OK |
| G4 | `defect_ledger_empty` | No OPEN/unknown defect ledger rows (lane-scoped options ledger too) |
| G5 | `shadow_pass` | Shadow window evidence (2026 window §4.4) |
| G6 | `embargo_clean` | No ≥2026 data in fitting |
| G7 | `determinism` | Byte-identical replay |
| G8 | `kill_switch_drill` | Kill-switch drill halt ≤1s |

Missing required gate → refuse + trip breaker (anti-bypass). TOCTOU re-check before arm.

Recovery from `DEGRADED` on clean GREEN read: decay driver calls `_attempt_recovery_via_rearm`;
direct `DEGRADED→LIVE` transition is forbidden.

## Cockpit data contract

### Current endpoint: `GET /api/lifecycle`

Builder: `apps/cockpit/backend/aggregate/lifecycle.py`.

Top-level response:

| Field | Type | Meaning |
|---|---|---|
| `zone` | `"lifecycle"` | Zone identifier |
| `generated_utc` | ISO-8601 | Build timestamp |
| `health` | `GREEN` \| `AMBER` \| `RED` | `RED` if any QUARANTINED; `AMBER` if DEGRADED+ARCHIVED_PAUSED; else GREEN |
| `total_models` | int | Registry size |
| `funnel` | map state→count | Counts for funnel display |
| `live` | int | Count in LIVE |
| `rows` | array | Per-model rows (sorted by id) |
| `registered` | bool | Registry non-empty |
| `note` | string \| null | Absent-registry hint |

Per-row fields (Slice 2 baseline):

| Field | Source |
|---|---|
| `id` | `model_lifecycle_id` |
| `hypothesis_id` | record |
| `symbol` | record |
| `state` | `current_state` |
| `dot` | UI status mapping (`_STATE_DOT`) |
| `since` | `current_state_since` |
| `route` | `reentry_routing.route` |
| `demotion_reason` | `demotion.reason` |
| `last_revalidation` | `last_revalidation.model_state` (legacy alias) |
| `envelope_id` | `current_envelope_id` |
| `submit_allowed` | `decay_detector.submit_allowed(state, model_state)` from loaded row |
| `submit_size_factor` | same |
| `submit_reason` | `lifecycle=<state> model_state=<GREEN\|YELLOW\|RED>` |
| `latest_model_state` | `last_revalidation.model_state` |
| `latest_revalidation_ts` | `last_revalidation.ts` |
| `latest_revalidation_triggers` | `last_revalidation.triggers` |
| `route_decided_at` | `reentry_routing.decided_at` |
| `next_required_gate` | state-derived (e.g. `rearm G0-G8`, `gauntlet`, `operator review`) |
| `evidence_links` | `research_card_links`, `governance_links`, envelope path |
| `last_transition` | last jsonl row for model (`ts`, `from_state`, `to_state`, …) |
| `transition_count` | count of jsonl rows for model |

Top-level aggregate fields (Slice 2):

| Field | Meaning |
|---|---|
| `blocked_count` | rows with `submit_allowed=false` |
| `degraded_count` | funnel `DEGRADED` |
| `needs_rearm_count` | `SHADOW` + `DEGRADED` + `ARCHIVED_PAUSED` |
| `needs_retest_count` | `SCREENING` + `GAUNTLET` |
| `retired_count` | funnel `RETIRED` |
| `transition_log_warning` | present when any transition jsonl line was skipped |

Cockpit rules:

1. Read-only — never writes registry.
2. Defensive parse — missing or unparseable registry → empty rows; current
   builder uses the same `note` for absent and corrupt input (`read_json` → None).
   Slice 2 may distinguish corrupt vs empty.
3. Fail-closed display — do not show OK/green for QUARANTINED/RETIRED rows.
4. Dense operational copy — next required gate, not marketing text (Slice 3+).

## Restart recovery contract

**Status:** design contract for Slice 5 implementation. Trade Manager restart
recovery is **not** fully implemented; autonomous runner resumability is separate
(Phase 24, `run_autonomous.py`).

On Trade Manager / workstation process restart, the operator workstation must be
able to produce a **recovery report** from session artifacts without submitting orders.

### Inputs (read-only)

From `artifacts/sessions/{session_id}/` (Phase 23 layout):

- `session_manifest.json`
- `active_models.json`, `registry_references.json`
- `order_intents.jsonl`, `order_state_transitions.jsonl`
- `risk_rejections.jsonl`, `fills.jsonl`, `positions.jsonl`
- `kill_switch_events.jsonl`, `incident_log.jsonl`

Plus lifecycle store:

- `runtime/lifecycle/model_lifecycle.json` (verify chain optional)
- `runtime/lifecycle/transitions.jsonl` (authoritative if snapshot diverges)

### Expected report fields (Slice 5 target)

| Field | Values | Meaning |
|---|---|---|
| `status` | `OK`, `INCIDENT_REQUIRED`, `UNKNOWN` | Overall recovery verdict |
| `open_orders_unknown` | bool | Cannot reconcile order state after disconnect |
| `position_reconciliation_status` | `OK`, `MISMATCH`, `UNKNOWN` | Phase 20 monitor semantics |
| `lifecycle_registry_ok` | bool | Snapshot load + optional chain verify |
| `required_operator_actions` | string[] | e.g. reconcile positions, manual quarantine review |
| `safe_to_resume_signals` | bool | **Never** true on dev workstation for live; REPLAY-only semantics |

### Recovery rules

1. Prefer `rebuild_registry_from_log()` if snapshot diverges from hash chain.
2. Unknown order/position state → `INCIDENT_REQUIRED`, not silent OK.
3. No adapter creation, cancel, flatten, or route during recovery probe.
4. Kill-switch or QUARANTINED lifecycle state → `safe_to_resume_signals=false`.
5. Session checkpoint format remains future work (`docs/hft3_trade_manager_runbook.md` §10).

## Decay driver operator loop

Scheduled or manual: `run_lifecycle_eval --observations {model_id: obs}`.

For each `LIVE` / `DEGRADED` model with envelope + observation:

1. `decay_detector.evaluate(envelope, observation)`
2. Always `annotate(last_revalidation=…)` → submit gate updates
3. If `demote` autonomy enabled and `r.demote`:
   - `LIVE → DEGRADED` first
   - `DEGRADED → route target` (or `QUARANTINED` if route is None/infra)
4. If clean GREEN and `DEGRADED`: attempt re-arm via gate chain
5. Audit: `DEGRADATION_DETECTED`, `AUTO_DEMOTE`, `AUTO_ARM_REFUSED`, etc.

## Workbench / Trade Manager integration map

| Concern | Module | Workstation visibility |
|---|---|---|
| Registry CRUD + transitions | `model_metrics.lifecycle` | Cockpit `/api/lifecycle` |
| Envelope monitoring | `decay_detector`, `state_engine` | model_state in rows |
| Submit enforcement | `submit_gate`, `risk_layer` | risk rejection reason (Slice 4+) |
| Re-arm | `lifecycle_orchestrator.rearm` | gate report in evidence drawer (Slice 4+) |
| Session truth | `trade_manager.session` | observer CLI + recovery (Slice 5+) |
| Kill switch | `trade_manager.kill_switch` | separate from submit gate; Cockpit autonomy zone |

## Literature and vault references

| Topic | Reference |
|---|---|
| Model inventory, monitoring, action limits | Vault `library/14 Model Lifecycle and Governance.md` (Sculley ML debt; SR 26-2; Arnott/Harvey backtesting protocol; Bailey/LdP spine via `library/13`) |
| No holdout peeking / post-failure quarantine | Arnott, Harvey & Markowitz backtesting protocol (vault library 14) |
| Promotion statistics before LIVE | DSR, PBO, CSCV, MinTRL — `ROBUSTNESS_TESTING_SPEC.md`, gauntlet gate |
| Filtration / event-time | `BLUEPRINT.md`, Chicago CME authority PDFs |
| Autonomy rails default-off | Vault `2026-06-12 Autonomy doctrine` |
| Cockpit lifecycle loop | Vault `2026-06-12 Cockpit + model lifecycle + autonomy loop` |
| Trade Manager phases | `docs/hft3_trade_manager_runbook.md` |
| Execution plan slices | Vault `operations/2026-06-22 Trade Manager lifecycle coding execution plan.md` |

## Acceptance (Slice 1)

This slice is complete when:

1. This document exists at `docs/project/TRADE_MANAGER_LIFECYCLE_WORKSTATION_SPEC.md`.
2. All ten content areas from the execution plan are present: states/transitions,
   operator meanings, submit-gate tables, re-entry routes, Cockpit contract,
   restart recovery contract, re-arm evidence, no-live-routing boundary, literature links.
3. Plan-audit reviewer confirms alignment with vault execution plan and code truth.
4. No code changes required unless review finds doc/code drift that must be fixed in Slice 2+.

## Known gaps (honest)

| Gap | Target slice |
|---|---|
| Lifecycle panel UI / evidence drawer | Slices 3–4 |
| Executable restart recovery module + tests | Slices 5–6 |
| `risk_layer` fail-open on submit_gate exception | Slice 6+ (CHI404 live hardening) |
| `size_factor` from submit gate not applied to order quantity | Slice 6+ (wire `_size` in risk_layer) |
| Live routing on CHI404 | Future authorized phase; boundary inert today |

## Related documents

- [docs/hft3_trade_manager_runbook.md](../hft3_trade_manager_runbook.md)
- [ROBUSTNESS_TESTING_SPEC.md](ROBUSTNESS_TESTING_SPEC.md)
- [PHASE_CONTRACTS.md](PHASE_CONTRACTS.md)
- [institutional_model_metrics_trade_manager_plan.txt](institutional_model_metrics_trade_manager_plan.txt)
