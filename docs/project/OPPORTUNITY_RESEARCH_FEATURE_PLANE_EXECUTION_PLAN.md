# MANDATORY ONTOLOGY GATE: Before using this document, operate from the Obsidian vault ontology, the repo authority specs, and the provided mathematics/quantitative-finance/HFT PDFs. Do not invent pipelines, models, features, data claims, or methodology outside that authority.

# Opportunity Research Feature-Plane Execution Plan

Status: subordinate execution plan, not a new canonical scope document.

This plan consolidates the current feature-plane brainstorming into one
followable workflow. It is controlled by:

- [docs/project/OPPORTUNITY_RESEARCH_SPEC.md](OPPORTUNITY_RESEARCH_SPEC.md)
- [docs/project/VECTORBT_SCREENING_ENGINE_SPEC.md](VECTORBT_SCREENING_ENGINE_SPEC.md)
- [docs/cockpit/MACRO_CONTEXT_VIX_OPTIONS_CHECKLIST.md](../cockpit/MACRO_CONTEXT_VIX_OPTIONS_CHECKLIST.md)
- [docs/project/FEATURE_LITERATURE_TRACEABILITY_MATRIX.md](FEATURE_LITERATURE_TRACEABILITY_MATRIX.md)

If this file conflicts with those authorities, the authority specs win and this
file must be corrected. This file is a work plan for execution discipline: it
organizes what to do, how to avoid leakage, how to avoid compute waste, and
where the review gates sit.

## Operating Mindset

Every main agent and sub-agent starts from the same gate:

1. Load [docs/vault/FABLE_MINDSET.md](../vault/FABLE_MINDSET.md).
2. Load [docs/ai/PONYTAIL.md](../ai/PONYTAIL.md).
3. Run VaultGate with a task-specific query.
4. Run `scripts/vault_pre_edit.ps1` and keep the fresh VaultPre receipt.
5. Respect the graph gate state in the vault. As of the current vault receipts,
   GraphGate, GraphPre, and GraphPost are owner-waived; record the waiver and
   use VaultGate plus targeted source reads.
6. Use sub-agents for bounded, auditable work. Every sub-agent prompt must say
   that it is grounded in Fable, Ponytail, VaultGate, and this plan.

Ponytail constraint: do the smallest controlled slice that proves or rejects a
hypothesis. Fable constraint: no claim is done until it is grounded, executed,
observed, verified, reviewed, and honestly reported.

## Goal

Build an organized way to send models down the existing research pipeline so
the VectorBT/Vast screening layer filters hypotheses before HftBacktest/HBT
realism, without recreating the pipeline.

The plan must:

- Preserve the existing backtested pipeline.
- Match the HBT/HftBacktest backtester and VectorBT screening documentation.
- Keep VIX/VVIX, VIX options, CME options, macro context, cross-asset futures,
  continuous/session state, and latency-state-as-feature in the testable
  ontology.
- Avoid turning unavailable data into global stoppage.
- Avoid calling an artifact full-product evidence unless it proves PIT feature
  consumption and ablation evidence.
- Add a blocking Plan Drift Review gate immediately before the final
  Greptile/external PR AI review loop.

## Non-Goals

- Do not create a new research pipeline.
- Do not replace the canonical opportunity research spec.
- Do not promote Stage A survivors as feature-complete evidence.
- Do not generate a million HBT units before cheap VBT screening and ablation
  have reduced the surface.
- Do not download paid data without a dry-run/cost manifest and owner budget
  decision.

## Closed Ontology

### Research Clocks

Only these clocks are admitted for this work:

| Clock | Code value | Purpose | Leakage rule |
|---|---|---|---|
| Scheduled event | `scheduled_event` | Existing event-window research around known macro/event timestamps. | Release and tradable timestamps must be PIT; no post-event data enters pre-event decisions. |
| Context-feature uplift | `context_feature_uplift` | Measures whether feature families improve a target-only baseline. | Must run target-only and target-plus-context comparisons with the same target clock. |
| Continuous intraday | `continuous_intraday` | Finds non-scheduled opportunities across sessions. | Must use trailing or event-time state available at decision timestamp only. |

Authority: [packages/backtest_pipeline/src/research_clock.py](../../packages/backtest_pipeline/src/research_clock.py),
[OPPORTUNITY_RESEARCH_SPEC.md](OPPORTUNITY_RESEARCH_SPEC.md).

### Feature-Plane Status

Every VBT/Vast artifact must declare exactly one:

- `feature_complete_pit_declared`
- `scheduled_event_only`
- `bar_stub_research_only`
- `incomplete_feature_plane`

Authority: [packages/backtest_pipeline/src/feature_plane.py](../../packages/backtest_pipeline/src/feature_plane.py),
[VECTORBT_SCREENING_ENGINE_SPEC.md](VECTORBT_SCREENING_ENGINE_SPEC.md).

`feature_complete_pit_declared` is allowed only when the artifact proves PIT
consumption or dependency-scoped sidelining for every admitted feature family:

- `primary_fs_v1`
- `cross_asset_futures`
- `vix_vvix_sensor`
- `vix_options`
- `cme_options_context`
- `macro_context`
- `continuous_session`
- `latency_state`

Lake existence is not feature use. Catalog eligibility is not model
consumption. A family on disk but not consumed is `not_used` or
`sidelined_missing_data`, never silently counted as covered.

### Context Sets

Use explicit context-set IDs so row counts do not explode invisibly:

| Context set | Meaning | Promotion rule |
|---|---|---|
| `target_only` | Primary target event/opportunity features only. | Required baseline for every target. |
| `target_plus_macro` | Target plus earlier macro releases and release-state features. | Must beat `target_only` after costs and robustness. |
| `target_plus_vix_vvix` | Target plus VIX/VVIX volatility sensors. | Requires PIT volatility timestamp coverage. |
| `target_plus_vix_options` | Target plus VIX options-derived state. | Requires PIT option chain/feature provenance. |
| `target_plus_cme_options` | Target plus CME options context. | Requires strict quote/chain proof or explicit sidelining. |
| `target_plus_cross_asset` | Target plus aligned cross-asset futures state. | Requires multi-symbol PIT alignment proof. |
| `target_plus_continuous_session` | Target plus continuous/session state. | Requires continuous clock proof; no full-session leakage. |
| `target_plus_latency` | Target plus latency/execution-state feature. | Requires measured or explicitly labeled latency authority. |
| `full_available_context` | All feature families that are currently PIT-proven and available for that unit. | Can run before backfill; missing families must have skip reasons. |
| `full_required_context` | All admitted feature families, including unavailable ones. | Used for completeness accounting, not for pretending missing data exists. |
| `negative_controls` | Shuffled, fake, lagged, irrelevant, or future-shift controls. | Must fail or stay materially worse than real PIT features. |

## Feature Records

Before a feature family is tested, create or update a feature record with these
fields. This can live in an existing manifest, citation ledger, or feature
recipe; do not create a parallel schema if the existing one can hold it.

```text
feature_family
feature_name
context_set_id
source_id
source_file_or_vendor
academic_or_ontology_receipt
decision_timestamp
source_timestamp
release_timestamp_or_null
tradable_timestamp_or_null
availability_timestamp
unit
join_rule
missingness_rule
model_consumption_status
pit_proof_artifact
coverage_status
ablation_status
negative_control_status
rejection_or_sidelining_reason
```

Reject the feature for the current unit if the source or availability timestamp
can be after the decision timestamp.

## Data Policy

### Available-Data Mode

Use available data now. Missing data blocks only the model cell, feature family,
symbol, event, or option window that depends on it. Missing data does not block
unrelated scheduled-event, context-uplift, or continuous research.

Required behavior:

- Record every unavailable slot as `sidelined_missing_data`, `skip`, or
  `rejected_missing_data`.
- Keep available rows moving.
- Do not count unavailable rows as runnable coverage.
- Do not zero-fill missing VIX/VVIX/options features.
- Do not clear an empty Databento/options gap without a no-data sidecar that
  proves vendor no-data status, schema, size, SHA-256, and exact window.

Authority: [MISSING_DATA_BACKFILL_SIDECAR.md](MISSING_DATA_BACKFILL_SIDECAR.md),
[specs/DATA_LAKE.md](../../specs/DATA_LAKE.md),
[packages/features_engine/src/features/vix_features.py](../../packages/features_engine/src/features/vix_features.py).

### Current Blocking Categories

This table is the decision rule for "what is missing" before testing.

| Missing or partial area | Blocks | Does not block |
|---|---|---|
| CME futures MBO event-symbol gaps | Only affected event-symbol units and strict models requiring those MBO windows. | Other symbols, other events, and models with runnable MBO coverage. |
| Options strict quote-level fixing MBO gaps | Strict options quote reconstruction, strict options features, and options model promotion for those windows. | Futures models that do not consume those option features; study-level options rows that are explicitly labeled non-strict. |
| VIX/VVIX or VIX-options unavailable rows | Units whose declared context set requires those volatility features. | `target_only`, non-volatility context sets, and volatility rows with explicit missing-data skip reasons. |
| Macro release gaps or uncertain release timing | Macro-context rows for affected release/event combinations. | Target-only event windows and non-macro context rows. |
| Cross-asset alignment gaps | Cross-asset context claims for affected symbols/windows. | Single-symbol target-only rows. |
| Continuous/session proof missing | Continuous intraday claims and continuous/session context claims. | Scheduled-event rows and context rows that do not claim continuous/session use. |
| Latency measurement missing or synthetic | Production-realistic execution claims. | Research evidence if latency is labeled with its measured or synthetic authority. |

The sidecar currently records CME futures MBO pilot event-symbol gaps and strict
options quote gaps as non-blocking queues. Refresh those counts before any paid
run; do not use stale counts for a spend decision.

### Paid Backfill Rules

Before any paid pull:

1. Run the existing dry-run or estimate command.
2. Write a `backfill_cost_manifest` with source, symbols, windows, schemas,
   expected bytes when available, estimated cost, budget cap, and owner decision.
3. Download only after the cost manifest is accepted.
4. Land bytes into the existing ignored lake/data roots.
5. Rebuild the active catalog or data-doctor surface.
6. Update the affected skip/rejection ledger only for newly filled slots.

No paid pull is allowed merely because a broad run wants a prettier denominator.

## Continuous Session Leakage Rule

Historical continuous research can work, but only as its own
`continuous_intraday` clock or as a separately declared
`continuous_session` context feature. It must not be smuggled into scheduled
event rows as a full-session statistic.

Allowed:

- Trailing windows ending at or before `decision_timestamp`.
- Event-time state from messages with `source_timestamp <= availability_timestamp <= decision_timestamp`.
- Session-open state once the open has occurred and latency/availability is
  explicit.
- Walk-forward or purged time splits.
- Per-decision features that could have existed in live operation at that time.

Forbidden:

- Full-day or full-session aggregates used before the session ends.
- Normalization fitted on holdout or future sessions.
- Random train/test splits across calendar time.
- Feature selection using holdout or recent holdout performance.
- A continuous feature that changes the target clock without declaring
  `continuous_intraday`.

If continuous data cannot satisfy this rule, separate it from historical
scheduled-event research and run it as its own continuous opportunity track.

## Negative Controls

Every context-uplift or full-context claim needs negative controls. At minimum:

- Shuffled labels by time block.
- Fake event timestamps with the same session/time-of-day distribution.
- Lagged context features that should be too old to carry the effect.
- Irrelevant context family for the target symbol/event.
- Future-shift trap that must be rejected by PIT validation.
- Target-only baseline with the same costs and fold boundaries.

If a negative control performs as well as or better than the real context
feature, the feature is not promoted. It may remain a research candidate with a
failure reason.

## Survivor A Policy

Stage A survivors are a seed ledger, not the identity of the feature-complete
VectorBT product.

Use Stage A as:

- `source_scope=stage_a_seed`
- candidate IDs to re-check under the closed research clocks
- a receipt for what was run, with its artifact hash and survivor count

Do not use Stage A as:

- proof that context features were consumed
- proof that VIX/VVIX/options/cross-asset/continuous/latency were consumed
  under the full feature-plane contract
- a reason to rebuild an exploding monolithic unit list before VBT screening
- a blocker for available-data feature-plane testing

The reported Stage A run can be cited as a user-supplied snapshot:

```text
path: research_cards/stage_a_full/stage_a_survivors.json
run: 16,931 / 16,931
errors: 0
BH survivors: 423
pass-through: [42, 43, 45]
sha256: d4954f82fd01ab04c83504eeea7b870dfc13e71721f0a4c963de57ac815e8048
```

The mismatch between older counts such as 72,000, expanded Stage A counts, and
all-active million-unit surfaces must be explained by layered manifests, not by
forcing all rows into one JSONL before screening.

## Manifest Layers

Use layered manifests to keep the work testable and cheap:

| Manifest | Purpose |
|---|---|
| `data_scope_manifest` | What data exists, what is missing, what is sidelined, and why. |
| `feature_recipe_manifest` | Feature families, source IDs, timestamps, units, joins, and missingness rules. |
| `logical_unit_manifest` | Candidate research units before expansion into execution rows. |
| `ablation_manifest` | Target-only, target-plus-context, full-available-context, and negative-control comparisons. |
| `execution_plan_manifest` | Worker count, unit count, cost estimate, abort rule, checkpoint/reuse plan. |
| `backfill_cost_manifest` | Paid-data estimate and owner decision before any download. |
| `review_receipt_manifest` | Local preflight, reviewer, ontology, verify, plan-drift, and PR AI receipts. |

The VBT layer consumes these manifests and rejects, promotes, or sidelines
logical units. HBT consumes only the promoted units with enough evidence to
justify realism cost.

## Runtime Language Boundary

Speed and realism are part of the research design. Do not rewrite the pipeline
to change languages unless a measured bottleneck or realism gap proves that the
existing layer cannot do its job.

| Layer | Language/runtime rule | Receipt |
|---|---|---|
| Planning, inventory, manifests, orchestration, reports | Python is allowed and preferred. These are control-plane tasks, not production latency authority. | [docs/workbench/LATENCY_ARCHITECTURE.md](../workbench/LATENCY_ARCHITECTURE.md), [docs/workbench/MEMORY_ARCHITECTURE.md](../workbench/MEMORY_ARCHITECTURE.md) |
| Feature extraction research path | Use the existing Python feature pipeline unless the implemented C++ backend is explicitly selected and parity-checked. `auto` may use C++; `cpp` must fail closed when unavailable; `python` is only for ablation/debug. | [packages/features_engine/src/pipeline/market_state_pipeline.py](../../packages/features_engine/src/pipeline/market_state_pipeline.py), [scripts/verify_cpp_parity.py](../../scripts/verify_cpp_parity.py) |
| VectorBT paid screening | Cheap pilots and dry-runs may be planning/control-plane. Broad/refine/paid-compute evidence must satisfy the Rust-engine gates before it can feed promotion/HBT claims. | [scripts/validate_paid_screen_ready_gate.py](../../scripts/validate_paid_screen_ready_gate.py), [scripts/audit_vbt_run_progress.py](../../scripts/audit_vbt_run_progress.py), [VBT_PAID_SCREEN_RUNBOOK.md](VBT_PAID_SCREEN_RUNBOOK.md) |
| HBT/HftBacktest realism | Keep the official HftBacktest handoff source-locked and run it only for VBT-promoted candidates with required NPZ, latency, queue, and upstream receipts. Do not replace it with a new simulator. | [packages/backtest_pipeline/src/hftbacktest_realism.py](../../packages/backtest_pipeline/src/hftbacktest_realism.py), [scripts/run_hftbacktest_realism.py](../../scripts/run_hftbacktest_realism.py), [scripts/hft_generate_campaign_manifest.py](../../scripts/hft_generate_campaign_manifest.py) |
| CHI404 live/paper order-placement and latency authority | C++ native probe is the authority. Python ctypes/connectors may orchestrate or capture non-hot data, but Python wall time cannot certify placement speed or production latency. | [docs/vault/CHI404_CANONICAL_ENTRYPOINTS.md](../vault/CHI404_CANONICAL_ENTRYPOINTS.md), [docs/workbench/HOT_PATH_AUDIT.md](../workbench/HOT_PATH_AUDIT.md), [packages/data_system/rithmic_trial/connector/rithmic_api_connector.py](../../packages/data_system/rithmic_trial/connector/rithmic_api_connector.py) |

Implementation consequence:

- Keep the current Python manifest/inventory/orchestration slice Python.
- Keep VBT scale work on the existing v2 paid-screen path and enforce Rust
  evidence for paid/broad scope before promotion claims.
- Keep HBT realism on the existing HftBacktest handoff after VBT promotion.
- Keep production latency and order-placement measurements on CHI404 native
  C++ artifacts.
- Move code toward C++ only for measured hot-path extraction/latency work with
  parity receipts; do not turn MVC feature wiring into a C++ rewrite.

## Execution Phases

### Phase 0 - Grounding

Outputs:

- VaultGate receipt.
- VaultPre receipt.
- GraphGate and GraphPre receipts when active, or owner-waiver receipt when
  waived.
- This plan read and cited.
- Task-specific success criteria.

Exit gate:

- No work starts until the target feature family, context set, research clock,
  and expected artifact path are named.

### Phase 1 - Data-Scope Inventory

Outputs:

- Fresh `data_scope_manifest`.
- CME lane missing-data table by symbol, event/window, schema, and dependency.
- VIX/VVIX/VIX-options availability table.
- CME options strict/study availability table.
- Macro release timestamp availability table.
- Cross-asset alignment availability table.
- Continuous/session raw and feature-proof availability table.
- Latency authority table.

Exit gate:

- Every missing row has a dependency-scoped skip rule.
- No global stop unless the requested unit depends on the missing data.

### Phase 2 - Feature-Recipe And PIT Proof

Outputs:

- `feature_recipe_manifest`.
- Source timestamp, availability timestamp, decision timestamp, join rule, unit,
  and missingness rule per feature family.
- Citation ledger per feature family.

Exit gate:

- Any feature with ambiguous PIT availability is sidelined before VBT.

### Phase 3 - Cheap Target-Only Baseline

Outputs:

- `target_only` VBT artifact.
- `feature_plane_status=scheduled_event_only`,
  `bar_stub_research_only`, or `incomplete_feature_plane` as appropriate.
- Candidate/promoted/rejected IDs with reasons.

Exit gate:

- No HBT expansion until the cheap artifact has candidate IDs and rejection
  reasons.

### Phase 4 - Pairwise Context Ablations

Outputs:

- One target-plus-context run per feature family:
  `target_plus_macro`, `target_plus_vix_vvix`, `target_plus_vix_options`,
  `target_plus_cme_options`, `target_plus_cross_asset`,
  `target_plus_continuous_session`, and `target_plus_latency`.
- Target-only baseline reference.
- Delta after costs.
- PIT proof.
- Negative controls.

Exit gate:

- A context family advances only if it beats target-only after costs and
  survives robustness and negative controls.

### Phase 5 - Continuous Intraday Pilot

Outputs:

- Small continuous-intraday pilot, separate from scheduled-event claims.
- Decision timestamp/bucket, opportunity type, horizon, and execution
  assumption.
- Leakage audit for trailing windows and session state.

Exit gate:

- Continuous opportunity rows stay separate unless their features are explicitly
  admitted as PIT context for a scheduled target.

### Phase 6 - Full Available Context

Outputs:

- `full_available_context` VBT artifact.
- `feature_usage_manifest_hash`.
- Consumed/sidelined/not-used status for every admitted feature family.

Exit gate:

- The artifact can be called full available context only for the families that
  are actually PIT-proven or dependency-sidelined.

### Phase 7 - Full Required Context Accounting

Outputs:

- `full_required_context` completeness report.
- Backfill queue and cost manifest for unavailable dependencies.
- Decision on whether missing data is major, nice-to-have, or blocking for a
  specific model cell.

Exit gate:

- No paid backfill without an accepted cost manifest.
- No wait-for-backfill pause for unrelated available-data testing.

### Phase 8 - VBT Scale Run

Outputs:

- Pilot throughput receipt.
- Unit count derivation.
- Worker count, reserved cores, 85 percent utilization target, and measured
  bottleneck notes.
- Stall monitor and abort rule.
- Terminal VBT artifact, not just JSONL row count.

Exit gate:

- Import results through quarantine first.
- Validate row count, run ID, hashes, PBO/DSR/CSCV gates, and cockpit
  aggregation before promotion.

### Phase 9 - HBT Realism On Promoted Rows

Outputs:

- HBT/HftBacktest run only for promoted VBT candidates.
- Latency, queue, fill, fee, slippage, and adverse-selection assumptions.
- Robustness report.

Exit gate:

- No model promotion without net edge after costs, latency and queue realism,
  walk-forward discipline, and robustness gates.

## Compute Planning

Every expensive run needs a prior estimate:

```text
total_vcpu
target_core_usage = 0.85 unless repo runbook is stricter
target_workers = floor(total_vcpu * target_core_usage)
reserved_cores = total_vcpu - target_workers
pilot_units
pilot_elapsed_hours
pilot_units_per_hour = pilot_units / pilot_elapsed_hours
planned_units
safety_factor = 1.25 to 1.50 unless measured otherwise
estimated_hours = planned_units / pilot_units_per_hour * safety_factor
estimated_cost = quoted_hourly_compute_and_storage * estimated_hours
abort_rule = no row advance for declared interval while workers are present
```

If the active runbook requires a stricter worker count, use the runbook. For
example, the VBT paid screen runbook requires at least 230 workers on a 256-vCPU
host unless a measured bottleneck or owner acceptance justifies lower usage.

Before any Vast or rented-compute run:

- Run a pilot with the same artifact schema as the full run.
- Prove expected work-unit count.
- Prove checkpoint/reuse rules.
- Quote cost before rent or download.
- Record source hashes.
- Do not sync code into an active expensive checkpoint unless the change is
  explicitly metadata/control-only and checkpoint backup/reuse evidence exists.

## Review Gates

The review chain for implementation work is:

```text
Fable
Ponytail
VaultGate
VaultPre
Spec
GraphGate when active
GraphPre when active
Plan
Delegate / Code
Local Preflight
Dual-Pass Reviewer
Ontology Gate when VBT/HBT/feature-plane/data changes
Verify
Plan Drift Review
Review Surface Gate
Greptile / external PR AI PR GrepLoop
GraphPost when active
```

Review Surface Gate creates or reuses the PR/MR/CL surface that external PR AI
needs. Greptile/external PR AI is the final review gate after that surface
exists. GraphPost is repository hygiene after code changes and is currently
owner-waived with the rest of the graph gate.

### Gate 1 - Local Preflight

Run bounded `rg` checks over changed scope before reviewer time:

- forbidden legacy terms and stale field names
- missing required vocabulary
- missing citation rows
- missing `feature_plane_status`
- missing `feature_usage_manifest_hash`
- missing `context_ablation_status`
- whitespace errors via `git diff --check`

Patch actionable hits. Stop after three local iterations and report blockers
instead of widening blindly.

### Gate 2 - Dual-Pass Reviewer

Use the reviewer charter:

- Pass A: assumptions, simplicity, surgical scope, verifiable goals.
- Pass B: filtration, event-time, no lookahead, walk-forward, execution
  realism, regime semantics, data lanes, production failure states.

Reviewer is required before verify output can be used as merge evidence.

### Gate 3 - Ontology Gate

Required for VBT/HBT pipeline, feature-plane, research-data, or finance/math
changes. It sits after the dual-pass reviewer and before verify.

The gate checks:

- citation trace
- invariant enforcement
- official tool/API usage
- artifact schema
- drift guard
- scope honesty

Any red finding blocks verify and promotion.

### Gate 4 - Verify

Run scope-appropriate tests or artifact validators. For docs-only edits, at
minimum run:

```powershell
git diff --check -- <changed-doc>
rg -n "<required-term>" <changed-doc>
rg -n "<forbidden-term>" <changed-doc>
```

For code/data changes, use [docs/VALIDATION_HONESTY.md](../VALIDATION_HONESTY.md)
to select the scope-green command.

### Gate 5 - Plan Drift Review

This is the new blocking gate requested by the owner. It sits after Verify and
immediately before Review Surface Gate, which then precedes Greptile/external
PR AI.

Purpose: keep the orchestrator honest that the executed work followed the
approved plan.

The Plan Drift Review agent is narrow. It does not redesign architecture. It
only compares plan, diff, artifacts, commands, and receipts.

Required reviewer prompt:

```text
You are the Plan Drift Review agent for hft3.

Load docs/vault/FABLE_MINDSET.md and docs/ai/PONYTAIL.md.
Run or observe VaultGate with the task query, then run or observe VaultPre.
Operate from the Obsidian vault ontology, repo authority specs, and the provided
mathematics/quantitative-finance/HFT PDFs.
Read docs/project/OPPORTUNITY_RESEARCH_FEATURE_PLANE_EXECUTION_PLAN.md.
Read the task plan, changed files, diff, artifacts, local preflight receipt,
reviewer receipt, ontology receipt when present, and verify receipt.

Output only findings.

Check:
1. Every changed file maps to a planned phase, manifest, or gate.
2. No feature family, context set, model family, schema, pipeline, or paid-data
   action was added outside the plan.
3. No planned step was marked complete without an artifact or receipt.
4. No missing data was treated as global stoppage unless the requested unit
   directly depended on it.
5. No unavailable feature was counted as consumed or covered.
6. No `feature_complete_pit_declared` claim appears without PIT proof for all
   admitted feature families or dependency-scoped sidelining.
7. Continuous/session features obey the leakage rule.
8. VIX/VVIX, VIX options, CME options, macro, cross-asset, continuous/session,
   and latency features are either consumed, not used, or sidelined with
   reasons; none are silently omitted from full-context claims.
9. Review and verify status is honest under docs/VALIDATION_HONESTY.md.
10. The next gate is Review Surface Gate, then Greptile/external PR AI; neither
    has been substituted by local Codex self-review.

Severity:
RED = plan violation or dishonest status; must fix before PR AI review.
YELLOW = ambiguous mapping or missing receipt; fix or document waiver before
PR AI review.
BLUE = clarity issue only.

If no findings, output:
Plan Drift Review: PASS
plan-drift: pass
```

If Plan Drift Review finds a red issue, fix the work or update the approved plan
before Greptile. If the plan is updated, rerun Local Preflight, Reviewer,
Ontology Gate when applicable, Verify, and Plan Drift Review.

### Gate 6 - Review Surface Gate

This gate makes the external PR AI loop executable instead of optional theater.
After Local Preflight, Reviewer, Ontology Gate when applicable, Verify, and Plan
Drift Review pass, create or reuse a branch plus PR/MR/CL review surface before
claiming merge-ready.

Rules:

- If no review surface exists, create or reuse one after Plan Drift Review passes.
- If publishing is blocked or owner-forbidden, record
  `review-surface: none(blocked: <reason>)`,
  `pr-ai-review: unavailable(no-pr)`, and `merge-ready: no`.
- If the owner explicitly waives external PR AI for the slice, record
  `review-surface: none(waived-by-user: <reason>)` and
  `pr-ai-review: waived-by-user`.
- Check review-surface size before opening or updating: split if the changed
  unit is too large or spans unrelated subsystems.
- Do not call local `rg`, local Codex self-review, or cavecrew-reviewer a review
  surface.

### Gate 7 - Greptile / External PR AI PR GrepLoop

This is the final review gate when a PR/MR/CL review surface exists and the
external PR AI connector is installed.

Rules:

- Trigger Greptile/external PR AI only after Plan Drift Review passes and the
  Review Surface Gate has a current-head surface.
- Use current-head SHA.
- Fix actionable findings.
- Rerun Local Preflight -> Reviewer -> Ontology Gate when applicable -> Verify
  -> Plan Drift Review -> Review Surface Gate -> Greptile/external PR AI after
  fixes.
- Stop after the repo's bounded iteration limit and report remaining issues if
  not clean.
- If the connector is unavailable, record
  `pr-ai-review: unavailable(no-connector|not-authenticated)`.
- Local Codex review does not satisfy this gate.

## Academic And Ontology Receipts

Every feature claim must attach a receipt. This plan-level ledger is only the
starting map.

| Area | Required receipt source |
|---|---|
| Operating discipline | [docs/vault/FABLE_MINDSET.md](../vault/FABLE_MINDSET.md), [docs/ai/PONYTAIL.md](../ai/PONYTAIL.md) |
| Feature-plane product authority | [OPPORTUNITY_RESEARCH_SPEC.md](OPPORTUNITY_RESEARCH_SPEC.md), [VECTORBT_SCREENING_ENGINE_SPEC.md](VECTORBT_SCREENING_ENGINE_SPEC.md), [FEATURE_LITERATURE_TRACEABILITY_MATRIX.md](FEATURE_LITERATURE_TRACEABILITY_MATRIX.md) |
| Context feature source/timestamp/unit/missingness rule | [MACRO_CONTEXT_VIX_OPTIONS_CHECKLIST.md](../cockpit/MACRO_CONTEXT_VIX_OPTIONS_CHECKLIST.md) |
| Feature-plane code enum and artifact fields | [packages/backtest_pipeline/src/feature_plane.py](../../packages/backtest_pipeline/src/feature_plane.py) |
| Research-clock code enum | [packages/backtest_pipeline/src/research_clock.py](../../packages/backtest_pipeline/src/research_clock.py) |
| PIT and leakage requirements | [ACCEPTANCE_CHECKLIST.md](ACCEPTANCE_CHECKLIST.md), [docs/REVIEWER_CHARTER.md](../REVIEWER_CHARTER.md) |
| Data lake and spend audit | [specs/DATA_LAKE.md](../../specs/DATA_LAKE.md), [MISSING_DATA_BACKFILL_SIDECAR.md](MISSING_DATA_BACKFILL_SIDECAR.md) |
| VIX missing-data behavior | [packages/features_engine/src/features/vix_features.py](../../packages/features_engine/src/features/vix_features.py) |
| Paid VBT/Vast execution | [VBT_PAID_SCREEN_RUNBOOK.md](VBT_PAID_SCREEN_RUNBOOK.md), [scripts/run_vbt_paid_screen_vast_full.sh](../../scripts/run_vbt_paid_screen_vast_full.sh) |
| Runtime language boundary | [docs/workbench/LATENCY_ARCHITECTURE.md](../workbench/LATENCY_ARCHITECTURE.md), [docs/workbench/MEMORY_ARCHITECTURE.md](../workbench/MEMORY_ARCHITECTURE.md), [docs/workbench/HOT_PATH_AUDIT.md](../workbench/HOT_PATH_AUDIT.md), [docs/vault/CHI404_CANONICAL_ENTRYPOINTS.md](../vault/CHI404_CANONICAL_ENTRYPOINTS.md) |
| Review gates | [docs/ai/GREPLOOP.md](../ai/GREPLOOP.md), [docs/REVIEWER_CHARTER.md](../REVIEWER_CHARTER.md), [docs/VALIDATION_HONESTY.md](../VALIDATION_HONESTY.md), [ONTOLOGY_GATE_AGENT_SPEC.md](ONTOLOGY_GATE_AGENT_SPEC.md) |
| MBO/event-time/filtration literature | `docs/references/chicago_cme_microstructure_mathematical_model.pdf`, `docs/references/README.md` |
| Walk-forward and robust backtesting literature | `docs/references/Ultimate_Quantitative_Finance_Researcher.pdf`, vault `library/13 Robust Backtesting and Multiple Testing.md` |
| OFI/MLOFI | vault `library/04 Order Flow Imbalance and Price Impact.md` |
| Options microstructure | vault `library/11 Options Microstructure.md` |
| System-level mapping | vault `library/System Implications.md` |

Future implementation slices must cite exact paper IDs, vault notes, repo
sections, or official tool docs in their feature records. A passive citation in
this plan is not enough to promote a model.

## Phase 1 Start-State Receipt

Generated local date: 2026-06-23.

This receipt records the start state used for the first available-data MVC
slice. It replaces the loose runtime start manifest so the workflow has one
organized planning file.

| Gate | Status | Receipt |
|---|---|---|
| Fable | read | [docs/vault/FABLE_MINDSET.md](../vault/FABLE_MINDSET.md) |
| Ponytail | read | [docs/ai/PONYTAIL.md](../ai/PONYTAIL.md) |
| VaultGate | run | current session stamp query: `follow opportunity research feature-plane execution plan current diff paid-screen runnable NPZ manifest filter plan doc cleanup gates`; earlier operator-supplied planning queries: `opportunity research feature plane start phase 1 data inventory VIX options CME options continuous latency NPC NCP`; `CME lane missing data VIX VVIX VIX options CME options macro cross asset continuous latency coverage` |
| VaultPre | pass | `scripts/vault_pre_edit.ps1` reported fresh stamp |
| GraphGate / GraphPre / GraphPost | waived | vault hot cache reports `waived-by-owner-2026-06-16` |

Start decision: proceed in available-data MVC mode. Do not start with
full-context, HBT-scale, or paid-data backfill. The safe first slice is one
scheduled-event target-only run plus one context-family ablation. All
unavailable or unconsumed feature families must be recorded as `not_used`,
`not_measured`, `sidelined_scope`, or `sidelined_missing_data`.

Do not claim `feature_complete_pit_declared` in the MVC. Current evidence
supports `scheduled_event_only` or `incomplete_feature_plane` until every
admitted family is PIT-consumed or dependency-sidelined.

| Family | Observed coverage | Missing or blocking status | MVC effect |
|---|---|---|---|
| CME MBO | Current lake JSON catalog reports `60,783` records, `8,465` events, `2018-01-01..2026-06-04`; Q001 pilot reports `4,829/5,040` slots, `95.8135%`. | `211` unavailable pilot event-symbol slots: `203` full no-market plus `8` partial FED_H41. | Not a global blocker. Skip only affected event-symbol cells. |
| VIX / VVIX sensors | `C:\hft3-lake\sensors` has `863` sensor parquet files; VIX/VVIX family is implemented. | Not VBT/HBT consumed; no Q001 certification of complete VIX feature lake. Missing inputs are unknown and must never be zero-filled. VVIX is sensor-only / Databento-unavailable rather than a local missing-file gap. | Do not include in MVC unless a PIT feature row is proven. Mark `not_used` for target-only/cross-asset MVC. |
| VIX options | `C:\hft3-lake\features\VIX.OPT` has `342` feature NPZs; legacy `C:\hft3-lake\vix_options\cmbp1` has `862` slot JSONs. | Partial only: limited strike/expiry depth, not VBT-consumed. | Blocks VIX-options context claims, not target-only rows. |
| CME options | Current data doctor reports fixing MBO quotes `275`, trades `507`, definitions `2,645`, statistics `206`, OHLCV `1`. | Current runtime shows study `gap_count=4` for `2026-06-15..2026-06-18`, strict quote gaps `511`, stale strict quote gaps `507`. Strict quote reconstruction, strict quote-only features, options replay, and options promotion remain blocked. | Options context is out of MVC. Target-only and cross-asset futures rows can proceed. |
| Macro | `events.csv` has `12,147` sourced rows across `45` event types; release calendars observed. | Macro context uplift not VBT-consumed; ablation not measured. | Target macro event can run as scheduled-event target. Macro-context uplift waits. |
| Cross-asset futures | Lake has all-7-core coverage for `6,388` events; pair coverage includes ES/MES `6,580`, NQ/MNQ `6,541`, ZN/ZB `7,695`; fs_v1 all-7-core features for `627` events. | Cross-asset family remains partial and not yet wired as VBT-consumed context. | First context ablation candidate because data exists and code has PIT assembly. |
| Continuous / session | Event-window lake remains primary; continuous tape acquisition is deferred. Continuous/session family is implemented but scoped out for scheduled-event VBT. | Blocks continuous-intraday and full-session claims. Full-session aggregates before session end are forbidden. | Out of MVC. Use `scheduled_event`; mark continuous/session `sidelined_scope`. |
| Latency | Runtime latency has paper and live order latency measured; live ack p99 `9.8108 ms`, paper ack p99 `6.9103 ms`; live placement `tick_to_send_p99=60.894us`. | Latency-as-feature is not VBT-consumed; sub-2ms/lane-1 production claims are blocked by current network/order-ack gate. | HBT/replay may later use measured ack injection. Mark latency feature `not_used` in MVC. |

Refresh caveat: `python scripts\paid_data_inventory.py --dry-run --verify-q001-hashes`
was attempted from the canonical repo and timed out after about 124 seconds.
Use existing accepted Q001/runtime reports for this start manifest. Treat a full
inventory refresh as a separate bounded task before paid spend.

Parsing caveat: `C:\hft3-lake\manifest.parquet` and
`C:\hft3-lake\vix_options\manifest.parquet` were observed on disk, but this
start pass used JSON/runtime reports for parsed coverage.

Dependency-scoped blockers:

| Missing or blocked item | Scope blocked | Scope not blocked |
|---|---|---|
| `211` CME MBO pilot event-symbol slots | Affected event-symbol cells and strict models requiring those exact windows. | Other runnable CME MBO rows. |
| CME options study gaps on `2026-06-15..2026-06-18` | Options study rows requiring those dates. | Futures target-only or cross-asset futures rows. |
| CME options strict quote gaps | Strict quote reconstruction, strict quote-only features, options replay, options promotion. | Futures VBT MVC. |
| VIX/options partial or unconsumed state | Claims that VIX/VVIX/VIX-options/CME-options context was tested or improved results. | Target-only MVC and cross-asset futures ablation. |
| Continuous tape/session scope gap | Continuous-intraday opportunity claims and full-session historical claims. | Scheduled-event VBT. |
| Latency-as-feature unconsumed state | Claims that latency state improved VBT. | Later HBT/replay latency injection experiments. |

## Acceptance Criteria For This Plan

This plan is usable when:

- It is read before feature-plane implementation or expensive runs.
- Every new research slice names a research clock and context set.
- Every artifact declares `feature_plane_status`.
- Every full-context claim has a `feature_usage_manifest_hash`.
- Every context claim has target-only baseline, target-plus-context result,
  delta after costs, PIT proof, negative controls, and robustness state.
- Every missing data item is dependency-scoped.
- Every paid-data action has a cost manifest before download.
- Every expensive compute action has a pilot, unit-count derivation, worker
  count, cost estimate, checkpoint policy, and abort rule.
- Plan Drift Review runs after Verify and before Greptile/external PR AI.
- Review Surface Gate runs before Greptile/external PR AI.
- Greptile/external PR AI remains the final review gate when a current-head
  review surface and installed connector are available.

## Handoff Block Template

Use this at the end of every implementation slice:

```text
merge-ready:     yes | no
scope-green:     yes | no | not-run
scope:           <touched path prefix or lane name>
data-mode:       n/a | fixture | production | live | mixed
research-clock:  scheduled_event | context_feature_uplift | continuous_intraday | n/a
context-set:     <context_set_id> | n/a
feature-plane:   <feature_plane_status> | n/a
vault-gate:      run | not-run
vault-pre:       pass | not-run
graph-gate:      pass | waived-by-owner | not-run
local-preflight: run | waived-by-user
reviewer:        pass | fail | not-run
ontology-gate:   pass | fail | n/a | not-run
verify-run:      <command> -> exit <code>; <summary tail> | WAIVED | not-run
plan-drift:      pass | fail | not-run
review-surface:  <PR/MR/CL URL or id>; head=<sha>; split-needed yes|no | none(blocked: <reason>) | none(waived-by-user: <reason>)
pr-ai-review:    run | unavailable(no-pr|no-connector|not-authenticated) | waived-by-user
known-gaps:      <list> | none | unverified
```

`merge-ready: yes` is impossible unless local preflight, reviewer, verify, Plan
Drift Review, Review Surface Gate, and PR AI requirements are satisfied or the
PR AI gate is explicitly owner-waived with `pr-ai-review: waived-by-user` and
`review-surface: none(waived-by-user: <reason>)`.
