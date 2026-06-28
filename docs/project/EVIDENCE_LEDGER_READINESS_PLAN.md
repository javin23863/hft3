# Evidence Ledger Readiness Plan

Status: operator-approved planning document for organizing VectorBT, robustness,
Monte Carlo, and HftBacktest handoff evidence without forcing incompatible
native artifacts into one linear artifact shape.

Source note: `C:\Users\MSI\.codex\attachments\0997d436-470f-4839-ab94-553425d5a6d2\pasted-text.txt`

Created: 2026-06-28

## Purpose

The current problem is not that VectorBT, robustness, Monte Carlo, CSCV/PBO/DSR,
and HftBacktest are separate engines. They should be separate. The problem is
that they are being connected by artifact shape instead of by an explicit
evidence contract.

The target architecture is:

```text
Candidate registry
  -> evidence ledger
  -> gate runner
  -> next engine only if evidence justifies it
```

VectorBT, robustness, Monte Carlo, CSCV/PBO/DSR, and HftBacktest do not need to
look like each other. They need to publish evidence into one shared ledger using
the same identity keys, artifact references, status fields, and reason codes.

## Current Facts

Current Vast VectorBT and unit-artifact robustness diagnostic facts:

```text
2,300 unit artifacts consumed
10,604 promoted VectorBT rows
50 model families
37 complete-surface families
0 packaged candidates
0 HftBacktest-eligible candidates
raw replay output absent
```

Interpretation:

```text
VectorBT did not fail.
VectorBT found candidates worth screening.
The robustness bridge refused to package anything.
HftBacktest has not received a strict eligible candidate.
```

This is not proof that no alpha exists. It proves that no current candidate
satisfies the robustness bridge packaging and evaluation contract.

## Architecture Decision

Do not make all raw data uniform.

Do not make all engines use the same artifact structure.

Make the promotion contract uniform.

Each engine keeps its native artifact shape:

```text
VectorBT keeps screening artifacts.
Robustness keeps surface, WFC, DSR, PBO, CSCV, stress, and Monte Carlo evidence.
HftBacktest keeps replay, latency, queue, fill, and execution-realism artifacts.
```

All engines write into the same evidence ledger:

```text
candidate_id
family_id
model_id
symbol
root_symbol
contract
event_id
event_set_id
event_type
parameter_values_hash
parameter_space_hash
feature_recipe_hash
data_manifest_hash
lake_manifest_hash
screening_artifact_hash
code_commit
engine_name
engine_run_id
gate_name
gate_status
failure_reason
failure_severity
artifact_refs
artifact_sha256
promotion_state
created_at
```

No stage should reverse-engineer another stage's native artifact. Each stage
writes its evidence to the ledger, and the gate runner decides what is missing
or what may proceed.

## Required Stage Separation

The pipeline must separate these three concepts:

```text
1. Packaging eligibility
2. Robustness evaluation
3. HftBacktest eligibility
```

### Packaging Eligibility

Question:

```text
Do we have enough measured evidence to run robustness?
```

Failure reasons include:

```text
missing event rows
missing parameter cells
incomplete surface
bad NPZ
insufficient trade count
artifact missing
hash mismatch
```

This is not an alpha failure.

### Robustness Evaluation

Question:

```text
Given valid evidence, does the model family show robust structure?
```

Failure reasons include:

```text
surface unstable
no parameter plateau
fold persistence failed
WFC failed
DSR failed
PBO failed
Monte Carlo null failed
cost-adjusted expectancy failed
```

This is a robustness or alpha-structure failure.

### HftBacktest Eligibility

Question:

```text
Is this robust enough to spend expensive replay-realism compute?
```

Required pass evidence includes:

```text
VectorBT pass
surface stable
fold persistent
DSR/PBO/CSCV acceptable
cost-adjusted positive
data quality pass
liquidity pass
feature recipe available
raw robustness inputs packaged
robustness evidence applied
robustness_evidence_receipt present
replay_eligibility_status=eligible
strict replay eligibility validator clean
```

Do not route anything to HftBacktest until the existing executable contract is
satisfied:

```text
replay_eligibility_status=eligible
robustness_evidence_receipt present
validate_candidate_replay_eligibility() clean
```

The ledger may expose `hftbacktest_eligible_derived=true` only as a derived
convenience field from those existing source-of-truth checks. It is not a new
routing authority and must never bypass
`apply_robustness_evidence_to_screening.py` or the strict HftBacktest handoff
validator.

## Broad Discovery Versus Confirmatory Robustness

VectorBT should remain the cheap broad screen before HftBacktest, but it should
not be forced to emit only robustness-ready complete surfaces during discovery.

Recommended split:

```text
Stage A - broad VectorBT discovery
Stage B - robustness-compatible VectorBT backfill
Stage C - robustness engine
Stage D - HftBacktest
```

### Stage A - Broad VectorBT Discovery

Purpose:

```text
Find potentially interesting model, parameter, and event regions cheaply.
```

Output:

```text
promoted rows
candidate IDs
rough performance
coverage information
```

Complete event-by-parameter surfaces are not required here.

### Stage B - Robustness-Compatible VectorBT Backfill

Purpose:

```text
For promising families, rebuild full or coverage-qualified event-by-parameter
matrices.
```

Output:

```text
complete or coverage-qualified surface matrix
coverage report
missing-cell report
```

### Stage C - Robustness Engine

Purpose:

```text
Test parameter-region stability, fold persistence, WFC, DSR, PBO, CSCV,
Monte Carlo, and stress behavior.
```

Output:

```text
robustness evidence package
readiness table
gate summary
raw diagnostic evidence
```

### Stage D - HftBacktest

Purpose:

```text
Replay only robust survivors with realistic fills, queue, latency, slippage,
and hft3 risk constraints.
```

Output:

```text
execution realism evidence
```

HftBacktest should answer whether an already robust strategy survives realistic
execution, not whether HftBacktest can rescue weak VectorBT candidates.

## Diagnostic Evidence Requirement

Diagnostic-only mode must not write raw replay inputs. That absence is correct
when no candidate is HftBacktest-eligible.

However, no robustness gate may return `fail` without writing the raw diagnostic
evidence used to make the decision.

For a surface-stability failure, write:

```text
family_surface_matrix.parquet
family_surface_coverage.json
family_gate_decision.json
```

For a fold-persistence failure, write:

```text
fold_persistence_matrix.parquet
fold_gate_decision.json
```

These are diagnostic evidence artifacts, not HftBacktest replay inputs.

## Required Ledger Outputs

Add an evidence ledger artifact set:

```text
runtime/evidence_ledger/<run_id>/candidate_evidence.parquet
runtime/evidence_ledger/<run_id>/family_readiness.parquet
runtime/evidence_ledger/<run_id>/gate_summary.json
runtime/evidence_ledger/<run_id>/robustness_bridge_readiness_report.md
```

The first implementation may use JSONL or JSON if Parquet support would add
unnecessary friction, but the schema must be explicit and lossless enough to
convert to Parquet later.

## Family Classification

Every family must be classified into exactly one bucket:

```text
robustness_fail_complete_evidence
surface_incomplete_missing_cells
adapter_contract_failure
data_quality_failure
hftbacktest_eligible_derived
```

Incomplete families must not be marked as alpha failures. They are evidence
coverage failures until the missing cells or data-quality issues are resolved.

## Family Readiness Fields

For each family, write:

```text
model_family
vectorbt_promoted_rows
unit_artifact_count
event_count
parameter_cell_count
expected_surface_cells
observed_surface_cells
surface_completeness_ratio
surface_stability_score
fold_persistence_score
robustness_gate_status
packaging_gate_status
replay_eligibility_status
robustness_evidence_receipt_status
hftbacktest_eligible_derived
primary_failure_reason
secondary_failure_reasons
recommended_next_action
```

Candidate-level rows should use the same identity keys and point to the family
decision plus candidate-specific evidence where available.

## Backfill Plan Requirement

For incomplete families, produce a targeted VectorBT backfill plan instead of
rerunning the full pipeline.

The backfill plan must list:

```text
missing events
missing parameter hashes
missing symbols
missing feature_recipe_hashes
exact VectorBT command or config needed to fill them
```

## Sensitivity Diagnostic Requirement

For complete families that fail robustness, do not rerun blindly. Produce a
sensitivity diagnostic:

```text
current threshold result
relaxed threshold result
pooled surface result
fold-level result
pass/fail reason
```

Gate calibration changes require evidence and should not be made just to create
HftBacktest candidates.

## Immediate Developer Tasks

Do not restart the full pipeline yet.

Task 1: Add a candidate and family evidence ledger artifact.

Required outputs:

```text
runtime/evidence_ledger/<run_id>/candidate_evidence.parquet
runtime/evidence_ledger/<run_id>/family_readiness.parquet
runtime/evidence_ledger/<run_id>/gate_summary.json
runtime/evidence_ledger/<run_id>/robustness_bridge_readiness_report.md
```

Task 2: Classify each of the 50 current families into exactly one bucket:

```text
robustness_fail_complete_evidence
surface_incomplete_missing_cells
adapter_contract_failure
data_quality_failure
hftbacktest_eligible_derived
```

Task 3: Persist raw diagnostic evidence for each failed robustness decision.

Task 4: Do not route anything to HftBacktest until the ledger records the
existing strict contract as true:

```text
replay_eligibility_status=eligible
robustness_evidence_receipt present
validate_candidate_replay_eligibility() clean
```

Any `hftbacktest_eligible_derived` field must be computed from those checks and
must not be accepted as independent authority.

Task 5: For incomplete families, produce targeted backfill commands or configs.

Task 6: For complete robustness failures, produce sensitivity diagnostics.

Task 7: Produce one final report:

```text
runtime/evidence_ledger/<run_id>/robustness_bridge_readiness_report.md
```

The report must answer:

```text
1. Did VectorBT produce usable screening evidence?
2. Did the bridge have complete surfaces?
3. Which families failed due to real robustness weakness?
4. Which families failed due to missing surface cells?
5. Which families failed due to adapter or data issues?
6. Is any candidate eligible for HftBacktest?
7. If none, what exact next action is required?
```

## Fable/Ponytail Calibration Correction

The next step is not more abstract bridge work. The bridge must explicitly
decide whether the operator should shape the data, shape the VectorBT run, shape
the pipeline/gate, or reject the model family.

Every blocked family must receive exactly one final diagnosis:

```text
download_or_build_missing_data
rerun_vectorbt_surface_shape
fix_pipeline_measurement_or_gate
reject_model_family
apply_robustness_evidence_first
ready_for_hftbacktest_decision
```

Interpretation:

```text
download_or_build_missing_data
  The NPZ, feature store, artifact, source receipt, or hash-bound source data is
  actually missing or corrupt. Do not write substitute code; download, build, or
  restore the data.

rerun_vectorbt_surface_shape
  The raw data exists, but the broad VectorBT discovery run did not produce the
  event x parameter coverage required by robustness. Run a targeted surface
  backfill, not a full restart.

fix_pipeline_measurement_or_gate
  Rows or unit artifacts exist, but measured metrics are missing, official stats
  are unavailable, or zero/insufficient-trade handling prevents a valid
  robustness cell. Diagnose instrumentation and gate semantics before rerunning
  data.

reject_model_family
  The family has complete enough evidence and fails robustness. Treat this as a
  model/hypothesis failure unless a separate sensitivity diagnostic justifies a
  gate-policy change.

apply_robustness_evidence_first
  Robustness appears to pass, but the explicit evidence applicator has not
  stamped strict replay eligibility. Apply evidence before any HftBacktest
  routing.

ready_for_hftbacktest_decision
  Strict derived HftBacktest eligibility is true. The operator may decide
  whether to spend replay compute.
```

The ledger must write:

```text
runtime/evidence_ledger/<run_id>/data_vs_pipeline_audit.json
runtime/evidence_ledger/<run_id>/data_vs_pipeline_audit.md
```

The audit must summarize counts by final diagnosis and list each family with:

```text
family_id
model_family
classification_bucket
primary_failure_reason
secondary_failure_reasons
zero_or_insufficient_trade_evidence
missing_data_evidence
surface_shape_evidence
recommended_next_action
final_diagnosis
```

### HftBacktest Boundary

This slice does not route any failed, data-quality, or control candidate to
HftBacktest. The production promotion gate remains fail-closed:

```text
replay_eligibility_status=eligible
robustness_evidence_receipt present
validate_candidate_replay_eligibility() clean
```

If the operator later authorizes a separate HftBacktest calibration experiment,
it must be planned as a new diagnostic lane with its own explicit waiver and
must not change this evidence-ledger gate.

## Acceptance Criteria

- No full-pipeline restart is required to produce the ledger from existing
  artifacts.
- The ledger separates packaging failures from robustness failures.
- Incomplete surfaces are not reported as alpha failures.
- Diagnostic gates write raw diagnostic evidence for every fail decision.
- The data-vs-pipeline audit classifies every blocked family into one concrete
  next-action diagnosis.
- HftBacktest routing remains fail-closed until strict eligibility appears in
  the ledger.
- This slice does not create a pre-eligibility HftBacktest calibration route.
- The readiness report gives exact next actions by family.

## Non-Goals

- Do not run HftBacktest from the current evidence.
- Do not restart the whole VectorBT pipeline before ledger classification.
- Do not relax robustness gates to create candidates.
- Do not revive an aggregate artifact path that caused memory pressure.
- Do not use local heavy compute for verification.

## Gate Plan

This plan and its implementation work must follow the hft3 workflow:

```text
Fable
Ponytail
VaultGate
Graph waiver: waived-by-owner-2026-06-16
bounded plan
scoped edit
local preflight hygiene
subagent review
Vast-only verification for heavy checks
plan drift review
operator decision before HftBacktest
```

External PR review tooling is not part of this plan unless the operator
explicitly reauthorizes it.
