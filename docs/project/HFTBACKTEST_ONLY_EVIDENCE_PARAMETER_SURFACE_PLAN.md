# HftBacktest-Only Evidence Ledger And Parameter Surface Plan

Status: accepted planning-control addendum.
Date: 2026-06-29.

This plan ports the useful evidence-ledger work from the old robustness path into
the new HftBacktest-only campaign identity. It does not preserve the old active
routing rule where VectorBT screening or robustness artifacts decide what
HftBacktest receives.

## Authority

Use this plan only under the active HBT-only supersession in
[HFTBACKTEST_ONLY_PIPELINE_PLAN.md](HFTBACKTEST_ONLY_PIPELINE_PLAN.md) and the
vault decision `2026-06-29 HBT-only all-model uniform-flow rule`.

Older VectorBT, robustness, screening, and bridge documents remain historical or
diagnostic references. Their evidence-shape improvements may be reused. Their
eligibility rule must not be reused.

## Non-Carry-Forward Rule

Do not carry this legacy routing rule into the active plan:

```text
VectorBT screen -> robustness evidence -> HftBacktest eligibility
```

The active routing rule is:

```text
canonical registry slug
  x HBT-normalized source NPZ/event unit
  x declared parameter hash
  -> HftBacktest run attempt or fail-closed pipeline/data/authority blocker
  -> post-HBT evidence ledger
  -> post-HBT evaluation
  -> reject / observe / promote
```

VectorBT artifacts, Stage A survivors, screening artifacts, and bridge-computed
robustness rows may be cited only as legacy diagnostics or comparison receipts.
They may not decide active HBT eligibility.

## Evidence Improvements To Keep

Keep these proven evidence-ledger ideas:

- evidence ledger;
- family readiness and candidate readiness;
- raw diagnostic evidence;
- blocker reason codes;
- data-vs-pipeline audit;
- no bridge or adapter failure treated as model failure.

Port them to HBT-only identity fields:

```text
canonical_model_id
registry_hash
source_npz_sha256
initial_snapshot_sha256
adapter_status
authority_refs
hbt_run_status
promotion_decision_path
```

Legacy identifiers may appear only in `legacy_aliases` or provenance fields.

## Parameter Surface Layer

Before any broad HBT campaign, build a deterministic parameter-surface manifest:

```text
canonical_model_id
  x source_npz/event unit
  x parameter_hash
```

Each parameter-surface row must include:

```text
campaign_id
unit_id
surface_unit_id
canonical_model_id
legacy_aliases
registry_hash
source_npz
source_npz_sha256
symbol
contract
event_id
event_window
initial_snapshot
initial_snapshot_sha256
parameter_family
parameter_hash
strategy_params
parameter_proposal_status
objective_evaluations
adapter_status
admissibility_status
blocker_code
blocker_detail
authority_refs
hbt_run_status
hbt_run_id
recorder_result_path
stats_summary_path
promotion_decision_path
```

The `parameter_hash` is a deterministic hash of canonicalized `strategy_params`
plus the parameter-family label. It is not a score, rank, or promotion handle.

## Parameter Proposal Semantics

The legacy `run_pipeline` parameter proposals are allowed only as deterministic
proposal generators:

```text
grid
bayesian-prior
evolutionary-prior
```

They are not true adaptive optimizers in this plan when
`objective_evaluations=0` before HBT execution. They must be recorded as:

```text
parameter_proposal_status=declared_pre_hbt
objective_evaluations=0
optimizer_claim=false
```

No parameter region may be ranked, accepted, rejected for economics, or promoted
until the corresponding HftBacktest artifacts exist.

## HBT Evidence Ledger

The HBT evidence ledger is written after HBT run attempts, not before. It records
what happened to each canonical model/event/parameter row.

Required row groups:

```text
1. identity
   canonical_model_id, legacy_aliases, registry_hash, authority_refs

2. data binding
   source_npz, source_npz_sha256, initial_snapshot, initial_snapshot_sha256,
   symbol, contract, event_id, event_window

3. parameter binding
   parameter_family, parameter_hash, strategy_params,
   objective_evaluations

4. adapter and run state
   adapter_status, admissibility_status, blocker_code, blocker_detail,
   hbt_run_status, hbt_run_id

5. artifact binding
   recorder_result_path, stats_summary_path, latency_report_path,
   fill_quality_report_path, queue_diagnostics_path,
   promotion_decision_path

6. post-HBT diagnostics
   mechanical_validity_status, economic_result_status,
   microstructure_realism_status, robustness_status,
   data_vs_pipeline_audit_status
```

## Readiness Ledgers

Family readiness groups rows by:

```text
canonical_model_id
symbol or instrument family
event family
parameter_family
```

Candidate readiness groups rows by:

```text
canonical_model_id
source_npz_sha256
initial_snapshot_sha256
parameter_hash
```

Both ledgers must distinguish:

```text
ready_for_hbt_run
blocked_data
blocked_authority
blocked_pipeline
hbt_run_failed
hbt_complete_pending_evaluation
post_hbt_observe
post_hbt_reject
post_hbt_promote_candidate
```

Only HBT-complete rows can enter `post_hbt_*` states.

## Data-Vs-Pipeline Audit

Every blocker must classify ownership:

```text
data_blocker
authority_missing
pipeline_blocker
hbt_run_failed
post_hbt_economic_reject
post_hbt_observe
post_hbt_promote_candidate
```

Correct examples:

```text
pipeline_blocker:missing_uniform_hbt_adapter
pipeline_blocker:feature_surface_mismatch
authority_missing
data_blocker:source_npz_missing
data_blocker:initial_snapshot_missing
```

Incorrect examples:

```text
adapter missing recorded as model failure
feature shape mismatch recorded as economic untradability
legacy screen non-promotion recorded as active HBT omission
parameter_rejected before HBT run artifacts exist
```

## Raw Diagnostic Evidence

Raw diagnostic evidence is a receipt layer, not a selector. It may include:

- source file hashes;
- HBT dtype validation output;
- event-order validation output;
- adapter exception type and message;
- feature-surface mismatch details;
- HBT return-code failures;
- latency/fill/queue stress results after HBT run;
- null strategy and negative-control outputs after HBT run.

Raw diagnostic evidence may explain blockers and drive fixes. It must not
silently remove rows from the campaign.

## Full Campaign Order

The active full campaign order is:

```text
1. Build canonical campaign manifest from registry slugs x HBT-normalized events.
2. Expand parameter-surface manifest by deterministic declared parameter sets.
3. Fail closed missing data, missing authority, and missing adapters as blockers.
4. Run HBT for admissible model/event/parameter rows.
5. Write recorder_result.npz and stats_summary.json.
6. Write promotion_decision.json only after both required HBT outputs exist.
7. Build HBT evidence ledger, family readiness, candidate readiness,
   raw diagnostics, blocker summary, and data-vs-pipeline audit.
8. Evaluate parameter regions only from completed HBT evidence.
9. Run Plan Drift Review.
10. Run PR GrepLoop last on the current review surface when merge-ready is intended.
```

## Acceptance Tests

Required tests for implementation:

```text
1. Parameter-surface manifest expands canonical_model_id x source event x parameter_hash.
2. parameter_hash is deterministic for canonicalized strategy_params.
3. grid/bayesian-prior/evolutionary-prior rows record objective_evaluations=0.
4. No parameter row can become economic reject before HBT artifacts exist.
5. Missing adapter/data/feature shape creates pipeline/data blocker, not model rejection.
6. Evidence ledger rows include the HBT-only identity fields listed above.
7. Family readiness and candidate readiness never read VectorBT or Stage A selectors.
8. Raw diagnostics preserve bridge/adapter failures as pipeline evidence, not model failure.
9. Data-vs-pipeline audit classifies every blocker.
10. Active HBT path does not consume screening_artifact.json for eligibility.
```

## Implementation Receipt

As of 2026-06-29, the pre-HBT parameter-surface manifest layer is implemented
locally in
`packages/backtest_pipeline/src/hftbacktest_only_campaign_manifest.py` and
`scripts/build_hftbacktest_only_campaign_manifest.py`.

Implemented scope:

- expands campaign rows into
  `canonical_model_id x source_npz/event x parameter_hash`;
- writes JSONL plus summary receipts for the parameter surface;
- restricts pre-HBT proposal families to `grid`, `bayesian-prior`, and
  `evolutionary-prior`;
- records `parameter_proposal_status=declared_pre_hbt`,
  `objective_evaluations=0`, and `optimizer_claim=false`;
- hashes strict canonical JSON with the parameter-family label included;
- preserves data, authority, adapter, and feature-shape blockers as
  non-economic blockers;
- refuses pre-HBT model or parameter economic decisions;
- refuses `promotion_decision_path` before recorder and stats artifact paths.

Not yet implemented in this receipt:

- post-HBT evidence ledger builder;
- family readiness ledger;
- candidate readiness ledger;
- full raw diagnostic ledger;
- full data-vs-pipeline audit ledger.

## Current Known Blockers

As of this plan addendum:

```text
1. RL canonical slugs are preserved but still need uniform HBT order adapters.
2. Vast full prepared-data manifest has not run for the current HBT-only campaign.
3. Vast remote compiled feature path still needs verification for the full campaign.
4. PR GrepLoop/review surface has not run on the current head, so merge-ready is no.
```
