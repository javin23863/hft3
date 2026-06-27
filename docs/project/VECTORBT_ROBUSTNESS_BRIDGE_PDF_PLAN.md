# VectorBT Robustness Bridge PDF Plan

Status: execution plan from operator-supplied PDF, created 2026-06-27 Bangkok.

Source PDF: `C:\Users\MSI\Downloads\HFT3_VectorBT_Robustness_Bridge_Findings_and_Developer_Prompt.pdf`

Archived source: `docs/references/hft3_vectorbt_robustness_bridge_findings_and_developer_prompt.pdf`

Extraction receipt: 6 pages, 14,262 text characters, extracted with bundled `pypdf` from the Codex primary runtime.

Source SHA256: `c50d31e6470c6926aec77125d8b2aeb5429b4373dd5b787adac920c04ddbb8d2`

## Purpose

Turn the operator-supplied VectorBT robustness bridge prompt into a controlled hft3 workflow without relaunching expensive compute blindly.

The PDF conclusion is that VectorBT did promote candidates, but the handoff from screening artifacts into robustness/HftBacktest packaging may be choking viable model families before the actual WFC, CSCV, PBO, DSR, and walk-forward evidence objects exist.

Core next move: run a diagnostic-only bridge sensitivity study from existing VectorBT artifacts before any full rerun, Vast rental, or HftBacktest realism routing.

## Current Observation

| Item | Observed or interpreted result |
|---|---|
| VectorBT promoted candidates | 1,728 candidates promoted |
| Robustness packaging accepted | 0 / 50 model families |
| HftBacktest eligible candidates | 0 |
| Suspected choke point | `scripts/build_robustness_raw_inputs_from_screening.py` |
| Suspicious logic | `train_event = events[0]` used for family-level surface stability |
| Required next action | Bridge diagnostic before any full rerun |

## Non-Negotiable Boundaries

- Do not restart the full VectorBT pipeline until this diagnostic explains whether the bridge is choking candidates or the candidates are genuinely non-robust.
- Do not send candidates to HftBacktest through a single-event family gate after this issue is identified.
- Do not treat a single event surface as a family-level prerequisite.
- Do not fabricate robustness evidence. The diagnostic may compare surface policies, but replay eligibility still requires real WFC, DSR, PBO, CSCV, surface-stability, and receipt evidence.
- Do not weaken hft3 invariants: point-in-time data, filtration, event-time ordering, walk-forward discipline, and CHI404-only live/paper topology.
- Graph gates remain `waived-by-owner-2026-06-16`; use VaultGate plus targeted source reads.

## Methodology Diagnosis

The suspected `events[0]` pattern is a quant-methodology problem, not just a code smell. If a family-level gate computes surface stability from only the first event in a family, one noisy, sparse, structurally unusual, or unrepresentative event can reject the entire family before the bridge constructs the evidence needed for WFC, CSCV, PBO, DSR, or walk-forward checks.

The correct statistical object is an event-by-parameter performance matrix, or a fold-specific in-sample matrix, not a single-event surface.

## Diagnostic Surface Policies

Implement diagnostic-only modes that compare the current bridge against corrected surface definitions. Production HftBacktest routing must not change until this report is reviewed.

| Surface policy | Definition | Purpose |
|---|---|---|
| `current_first_event` | Current logic: use `events[0]` only | Baseline reproduction of current behavior |
| `pooled_train_events` | Aggregate parameter surfaces over all usable training events | Main low-friction corrected methodology |
| `median_event_surface` | Compute surface per event, then summarize median and downside dispersion | Robustness against one bad event |
| `fold_is_surface` | Compute surface on in-sample events inside each WFC/walk-forward fold | Preferred mode if fold definitions already exist |

## Implementation Phases

### Phase 0 - Plan And Source Receipt

Deliverables:

- This plan document.
- Archived source PDF under `docs/references/`.
- PDF binary git attribute receipt if the archive would otherwise be treated as text.
- Commit containing only the plan/source/binary-attribute receipt.

Gate:

- VaultGate fresh.
- Source PDF hash recorded.
- Source PDF is stored as binary and not line-ending normalized.
- `git diff --check`.
- Staged files contain no runtime or graphify artifacts.
- Reviewer pass on the staged diff.

### Phase 1 - Locate Existing Bridge Inputs

Goal: identify the exact current bridge logic, artifact schemas, and tests without broad code churn.

Expected reads:

- `scripts/build_robustness_raw_inputs_from_screening.py`
- `scripts/package_robustness_evidence_inputs.py`
- `scripts/apply_robustness_evidence_to_screening.py`
- robustness bridge and paid-screen tests that cover raw-input assembly and strict replay eligibility
- current runtime receipt paths for the existing VectorBT artifacts

Gate:

- Document the exact current first-event behavior.
- Identify the smallest artifact needed to reproduce the current 0-family pass.
- Do not edit routing code in this phase.

### Phase 2 - Add Diagnostic Surface Policies

Goal: add diagnostic-only `--surface-policy` handling to the bridge raw-input assembler.

Required modes:

- `current_first_event`
- `pooled_train_events`
- `median_event_surface`
- `fold_is_surface`

Behavior requirements:

- `current_first_event` must reproduce the current behavior exactly.
- `pooled_train_events` must aggregate each parameter cell across usable training events using a median or trimmed mean and require minimum event coverage per cell.
- `median_event_surface` must compute per-event stability, median stability, lower-quartile or downside stability, and event dispersion.
- `fold_is_surface` must compute stability on in-sample events per fold and record whether selected parameter regions persist out of sample.
- Events flagged as `data_bad`, `insufficient_trades`, `missing_surface`, or `nonfinite_metrics` must be excluded from aggregation but reported explicitly.
- Packaging eligibility must stay separate from robustness pass/fail.

Gate:

- No HftBacktest routing change.
- Baseline policy reproduces the current result unless artifacts have changed.
- Corrected policies report family and candidate counts without granting replay eligibility by themselves.

### Phase 3 - Diagnostic Report Contract

Goal: write a report that explains where candidates are lost.

Target artifact:

- `runtime/robustness/robustness_bridge_sensitivity_report.json`

Required fields:

- `model_family`
- `vectorbt_promoted_count`
- `event_count`, `usable_event_count`, `rejected_event_count`
- `parameter_cell_count`
- pass booleans for all surface policies
- `candidates_passing_current_first_event`
- `candidates_passing_pooled_train_events`
- `candidates_passing_median_event_surface`
- `candidates_passing_fold_is_surface`
- `event_0_id`
- `event_0_surface_metrics`
- `pooled_surface_metrics`
- policy-specific failure reasons
- `candidates_rejected_by_current_but_passed_by_corrected_policy`

Bridge attrition sequence:

1. VectorBT promoted candidates.
2. Model families formed.
3. Families with enough events, cells, trades, and data quality for packaging.
4. Current first-event surface survivors.
5. Corrected pooled, median, and fold-policy survivors.
6. WFC and walk-forward survivors.
7. CSCV and PBO survivors.
8. DSR survivors.
9. HftBacktest-eligible candidates.

### Phase 4 - Tests And Verification

Goal: prove the diagnostic is deterministic and cannot silently relax production gates.

Expected tests:

- baseline reproduction for `current_first_event`
- pooled-event behavior with one noisy first event and multiple usable events
- median-event behavior with downside dispersion
- fold in-sample behavior when fold definitions exist
- report schema fields and attrition counters
- fail-closed behavior when event/cell coverage is below minimum
- proof that diagnostic survivors do not automatically become HftBacktest-eligible

Gate:

- Local preflight `rg` loop over changed files for stale policy names, accidental routing changes, and missing report fields.
- Dual-pass reviewer: Karpathy/Ponytail simplicity plus math-invariant review.
- Bounded test command with exit code and output tail.
- Plan drift review against this document.

### Phase 5 - Production Policy Recommendation

Only after the diagnostic report exists:

| Diagnostic outcome | Action |
|---|---|
| `current_first_event = 0`, corrected policy > 0 | Fix bridge policy before HftBacktest routing. Prefer `fold_is_surface` if folds exist; otherwise use `pooled_train_events` with coverage and downside-stability thresholds. |
| all policies = 0 | Do not HftBacktest these candidates. Treat VectorBT promotions as cheap-screen false positives under the available evidence. |
| current and corrected policies agree | Current gate may not be choking this run, but avoid single-event default for future family-level screening. |

## Review Gate Stack

This plan is controlled by the hft3 workflow:

- Fable first: ground in real artifacts, reason before acting, verify before claiming success.
- Ponytail second: minimal diffs, no speculative abstractions, no validation shortcuts.
- VaultGate third: `scripts/vault_gate.ps1` and `scripts/vault_pre_edit.ps1`.
- Graph gates: `waived-by-owner-2026-06-16`.
- Local preflight before reviewer time.
- Reviewer gate with 0 red findings before merge-ready claims.
- Scope-green verification, not only targeted smoke if the touched scope is broader.
- Plan drift review against this document.
- Review surface and PR GrepLoop before any merge-ready status, unless explicitly waived by the owner.

## Acceptance Criteria

- The first diagnostic runs against existing VectorBT screening artifacts.
- No full rerun is required for the first diagnostic.
- `current_first_event` reproduces the current zero-family pass result unless artifacts changed.
- The report shows family and candidate pass counts under each surface policy.
- Corrected-policy survivors confirm the current bridge is too brittle and must be fixed before HftBacktest routing.
- Zero survivors under all policies means the promoted candidates are not robust enough under available evidence.
- No candidate reaches HftBacktest through the old first-event gate.

## Vault Authority Checked

- `wiki/hot.md`
- `Home.md`
- `Memory Stack.md`
- `operations/2026-06-23 Pre-VastAI smoke handoff.md`
- `operations/2026-06-25 HFT3 repo resume packet.md`
- `operations/2026-06-26 Vast VectorBT value CPU full run progress.md`
- `docs/vault/FABLE_MINDSET.md`
- `docs/ai/PONYTAIL.md`

## Workflow Start Status

Phase 0 is the current active phase. Implementation must not proceed until this plan/source receipt commit is complete and the scoped review gates pass.
