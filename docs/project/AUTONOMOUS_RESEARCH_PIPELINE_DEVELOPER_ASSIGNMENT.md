---
status: active
replaces: all prior autoresearch pipeline prompts
repo: javin23863/hft3
date: 2026-06-20
---

# Mandatory Developer Assignment: Complete the Autonomous Research Pipeline with Two Walk-Forward Gates and Greptile PR GrepLoop

This prompt replaces all previous prompts for this work.
Follow it literally.
Do not reinterpret it as a request to write architecture documents or create another framework.

The repository is:

```text
javin23863/hft3
```

The goal is to complete and harden the **existing** autonomous research loop so one command can run the entire research campaign without human intervention:

```text
candidate proposal
→ ontology admission
→ candidate freeze
→ VectorBT screen
→ surface robustness
→ regular walk-forward
→ Walk Forward Correlation
→ statistical/Monte Carlo gauntlet
→ HftBacktest realism
→ final certification
→ learning-memory update
→ next-generation proposal
→ repeat or stop
```

The implementation must also pass the repository's real engineering review process, including the externally installed **Greptile** PR review loop.
Codex review does not satisfy the Greptile requirement.

Related repo specs (cross-links):

- [Autonomous pipeline runbook](../hft3_autonomous_pipeline_runbook.md)
- [Autonomous config](../../configs/research/autonomous_hft3.yaml)
- [Greptile PR GrepLoop procedure](../ai/GREPLOOP.md)
- [Walk Forward campaigns (incl. WFC)](../workbench/WALK_FORWARD_CAMPAIGNS.md)

---

## 1. Reuse the current implementation

The existing autonomous implementation is already located in:

```text
scripts/run_pipeline.py
packages/research_pipeline/generation_loop.py
packages/research_pipeline/generation_state.py
packages/research_pipeline/generation_summary.py
packages/research_pipeline/elite_refinement.py
packages/research_pipeline/feature_family_proposals.py
packages/research_pipeline/feature_recipe.py
packages/research_pipeline/candidate_manifest.py
packages/research_pipeline/review_memory.py
packages/backtest_pipeline/src/ontology_gate.py
packages/backtest_pipeline/src/vectorbt_adapter.py
packages/backtest_pipeline/src/surface_stability.py
packages/backtest_pipeline/src/hft_campaign/
apps/workbench/
packages/research_pipeline/src/robustness_producers.py
```

The existing CLI already supports:

```bash
python scripts/run_pipeline.py --autoresearch ...
```

Extend this path.
Do not create:

```text
another autoresearch CLI
another candidate registry
another feature registry
another robustness framework
another backtester
another worker framework
another database
another scheduler service
another gate ontology
```

A small helper module is allowed only when an existing owner cannot reasonably contain the code. Any new file must be justified in the final report.

---

## 2. Mandatory first action: active-path audit

Before changing code, run targeted searches and inspect the exact current implementations.

Run at least:

```bash
rg -n "run_autoresearch_loop|run_single_generation|propose_next_candidates" \
  scripts packages apps tests
rg -n "walk.forward|walk_forward|Walk Forward|WFC|Pearson|Spearman|correlation" \
  apps packages docs tests
rg -n "Martyn Tinsley|Walk Forward Correlation|youtube|YouTube|transcript" \
  docs apps packages tests
rg -n "allow_partial|robustness_pass|elite|best_candidate|hft_replay_status" \
  packages/research_pipeline apps/workbench
rg -n "greptile|greptileai|GrepLoop|PR GrepLoop" \
  .github docs AGENTS.md
```

Produce this audit table before editing:

```text
Requirement
Existing file
Existing function/class
Existing test
Existing artifact
Active in run_pipeline.py --autoresearch?
Complete?
Gap
Minimal required change
```

The audit must identify exact implementations for:

```text
regular walk-forward
Walk Forward Correlation
parameter-surface construction
surface alignment
Pearson computation
Spearman computation
surface-stability testing
bootstrap/Monte Carlo
DSR
CSCV/PBO
Holm/BH
fee/slippage/latency stress
HftBacktest campaign
ontology gate
Greptile PR review loop
```

Do not implement a second WFC function until proving that no suitable existing function already exists.

**Known WFC anchor paths (audit starting points — verify before editing):**

- Implementation: [`apps/workbench/src/robustness/wfc/gate.py`](../../apps/workbench/src/robustness/wfc/gate.py) (`evaluate_wfc_gate`)
- Config: [`apps/workbench/config/wfc_gate.yaml`](../../apps/workbench/config/wfc_gate.yaml)
- Campaign doc: [Walk Forward campaigns](../workbench/WALK_FORWARD_CAMPAIGNS.md)

---

## 3. Keep three loops separate

There are three different loops. Do not conflate them.

### Loop A — Autonomous research loop

```text
propose
→ freeze
→ evaluate
→ review machine evidence
→ reject or retain
→ learn
→ propose next generation
```

This belongs to:

```text
packages/research_pipeline/generation_loop.py
```

### Loop B — Local repository preflight loop

This is the bounded local `rg` cleanup loop:

```text
edit
→ run task-specific rg searches
→ fix actionable hits
→ repeat up to three times
```

This is not the PR GrepLoop.

### Loop C — External Greptile PR GrepLoop

This is the real pull-request review cycle using the installed Greptile GitHub integration:

```text
push current head
→ request Greptile review
→ retrieve Greptile findings
→ fix every actionable finding
→ rerun verification
→ push
→ request Greptile review again
→ stop only when clean
```

Procedure authority: [Greptile PR GrepLoop](../ai/GREPLOOP.md)

Do not name the runtime gate function `GrepLoop`.
Use a runtime name such as:

```python
run_generation_gate_chain(...)
```

---

## 4. Implement a strict runtime gate-chain contract

Add or extend one gate-chain function in the existing generation-loop ownership area.

Preferred interface:

```python
def run_generation_gate_chain(
    *,
    candidate_manifest: dict,
    ontology_receipt: dict | None,
    vectorbt_receipt: dict | None,
    surface_receipt: dict | None,
    regular_walk_forward_receipt: dict | None,
    walk_forward_correlation_receipt: dict | None,
    statistical_receipt: dict | None,
    hftbacktest_receipt: dict | None,
    certification_mode: bool,
) -> dict:
    ...
```

Each gate receipt must contain:

```text
gate_id
gate_version
candidate_id
feature_recipe_hash
manifest_hash
status
required_checks
required_check_count
passed_check_count
failed_check_count
missing_check_count
authority_refs
input_artifacts
input_hashes
output_artifacts
output_hashes
failure_reasons
started_at_utc
finished_at_utc
```

Allowed statuses:

```text
PASS
REJECT
BLOCKED
NOT_RUN
```

### Absolute PASS rule

A gate returns `PASS` only when:

```text
passed_check_count == required_check_count
failed_check_count == 0
missing_check_count == 0
all required artifacts exist
all required artifacts validate
all hashes match
all required authority references resolve
```

These states are never equivalent to PASS:

```text
None
unknown
not_run
partial
allow_partial
missing
stale
malformed
fixture_only
pilot_only
smoke_only
accelerated_non_certifying
```

Remove permissive conditions such as:

```python
robustness_pass is not False
status not in ("fail", "blocked")
bool(missing_or_optional_value)
```

Use exact positive comparisons:

```python
regular_walk_forward_status == "PASS"
walk_forward_correlation_status == "PASS"
statistical_status == "PASS"
hftbacktest_status == "PASS"
```

---

## 5. Gate 0 — Ontology admission before compute

Use the existing implementation:

```text
packages/backtest_pipeline/src/ontology_gate.py
docs/project/ONTOLOGY_GATE_AGENT_SPEC.md
docs/REVIEWER_CHARTER.md
```

- [ONTOLOGY_GATE_AGENT_SPEC.md](ONTOLOGY_GATE_AGENT_SPEC.md)
- [REVIEWER_CHARTER.md](../REVIEWER_CHARTER.md)

Do not create another ontology gate.

Every candidate must prove the basis for:

```text
model/hypothesis
selected features
feature families
source assets
cross-asset relationships
lags
windows
transformations
interactions
context gates
regime gates
parameter ranges
regular walk-forward methodology
Walk Forward Correlation methodology
statistical tests
VectorBT API usage
HftBacktest API usage
```

Each claim must resolve to one or more of:

```text
vault academic-paper ID
repo authority specification
repo mathematical PDF
official VectorBT documentation/source lock
official HftBacktest documentation/source lock
```

Required authority paths include:

```text
docs/project/ONTOLOGY_GATE_AGENT_SPEC.md
docs/project/VECTORBT_SCREENING_ENGINE_SPEC.md
docs/project/HFTBACKTEST_REALISM_ENGINE_SPEC.md
docs/project/ROBUSTNESS_TESTING_SPEC.md
docs/REVIEWER_CHARTER.md
apps/workbench/config/walk_forward.yaml
vendor/vectorbt/VENDOR.lock
vendor/hftbacktest/VENDOR.lock
```

- [VECTORBT_SCREENING_ENGINE_SPEC.md](VECTORBT_SCREENING_ENGINE_SPEC.md)
- [HFTBACKTEST_REALISM_ENGINE_SPEC.md](HFTBACKTEST_REALISM_ENGINE_SPEC.md)
- [ROBUSTNESS_TESTING_SPEC.md](ROBUSTNESS_TESTING_SPEC.md)
- [walk_forward.yaml](../../apps/workbench/config/walk_forward.yaml)
- [VectorBT VENDOR.lock](../../vendor/vectorbt/VENDOR.lock)
- [HftBacktest VENDOR.lock](../../vendor/hftbacktest/VENDOR.lock)

An unbacked feature, relationship, parameter range, formula, threshold, or weighting must produce:

```text
BLOCKED_UNBACKED_AUTHORITY
```

Do not invent a threshold because the code needs one.
If the repo contains an implementation default but no approved authority for that threshold, the candidate cannot receive certification based on that threshold until it is resolved.

Write:

```text
generation_<N>/gates/<candidate_id>/ontology_gate.json
```

Only ontology-PASS candidates may consume VectorBT compute.

---

## 6. Gate 1 — Frozen candidate manifest

Continue using:

```text
packages/research_pipeline/candidate_manifest.py
packages/research_pipeline/feature_recipe.py
```

Freeze the candidate after ontology admission and before evaluation.

The manifest must include:

```text
candidate_id
generation_index
parent_candidate_id
proposal_reason
model_id
target symbol
target event/opportunity
research clock
feature families
selected features
source symbols
lags
windows
transformations
interactions
context gates
regime gates
latency assumptions
execution parameters
feature_recipe_hash
source-data hashes
feature-data hashes
split configuration hash
complete campaign config hash
code commit
VectorBT source lock/version
HftBacktest source lock/version
robustness producer version
ontology receipt hash
```

Changing any evaluation-relevant field must change the manifest hash.
The frozen manifest cannot be mutated during the generation.

---

## 7. Gate 2 — VectorBT screening

Use the existing VectorBT path.
Do not invoke `run_pipeline.py` once per unit.

The active broad/paid path must use:

```text
one worker layer
long-lived workers
structured unit inputs
shared event/feature loading
raw-signal reuse
matrix-batched VectorBT trials
individual logical artifacts
```

Require:

```text
official VectorBT API/source-lock proof
Rust proof for broad/paid scope
candidate_id
feature_recipe_hash
manifest_hash
data hashes
PIT shift proof
full declared parameter universe
gross return
net return
gross/net PnL
fees
slippage
trade count
hit rate
expectancy
profit factor
Sharpe
Sortino
max drawdown
turnover
explicit rejection reasons
```

VectorBT is a cheap screen.
VectorBT cannot certify execution realism.

Write:

```text
generation_<N>/gates/<candidate_id>/vectorbt_gate.json
```

Only exact VectorBT PASS candidates continue.

Spec: [VECTORBT_SCREENING_ENGINE_SPEC.md](VECTORBT_SCREENING_ENGINE_SPEC.md)

---

## 8. Gate 3 — In-sample parameter-surface stability

Use the existing surface-stability implementation where applicable:

```text
packages/backtest_pipeline/src/surface_stability.py
```

Require:

```text
complete predeclared parameter surface
plateau width
neighbor stability
cliff distance from loss regions
parameter perturbation sensitivity
peak-versus-plateau comparison
minimum sample size
surface input hash
formula authority
threshold authority
```

Do not select an isolated maximum merely because it has the highest value.
A missing cell, post-hoc grid change, insufficient sample, or unauthorized threshold blocks the gate.

Write:

```text
generation_<N>/gates/<candidate_id>/surface_stability_gate.json
```

---

## 9. Gate 4 — Regular Walk-Forward validation

This is the first walk-forward process.
It is **not** Walk Forward Correlation.

Use the existing regular walk-forward configuration and implementation.
At minimum inspect:

```text
apps/workbench/config/walk_forward.yaml
apps/workbench/
packages/research_pipeline/generation_summary.py
```

- [walk_forward.yaml](../../apps/workbench/config/walk_forward.yaml)

The configured chronological stages are authoritative unless a higher authority explicitly changes them:

```text
Discovery
Confirmation
Holdout
Recent holdout
Sim shadow where applicable
```

Regular walk-forward must:

```text
tune only where tuning is permitted
move chronologically forward
preserve train/validation/test dates
prevent holdout feedback into learning
run the selected stable parameter methodology on later periods
produce fold-specific metrics
```

Required outputs:

```text
fold IDs
train start/end
validation start/end
test start/end
parameter selection input
selected parameter or plateau rule
fold-specific trade count
fold-specific net return
fold-specific expectancy
fold-specific drawdown
walk-forward efficiency
fold dispersion
IS/OOS gap
OOS decay
holdout evaluate-only proof
recent-holdout evaluate-only proof
```

The holdout and recent holdout results must not be fed into:

```text
feature selection
parameter refinement
candidate mutation
learning memory used for proposals
kill/resurrect decisions
```

Write:

```text
generation_<N>/gates/<candidate_id>/regular_walk_forward_gate.json
```

A regular walk-forward PASS does not satisfy the WFC gate.

---

## 10. Gate 5 — Walk Forward Correlation

This is the **second and separate** walk-forward process.
It is based on the Walk Forward Correlation methodology already referenced inside the repository, including the Martyn Tinsley material and the repository's robustness specification.

Before editing, locate the exact existing:

```text
implementation function
call site
configuration
transcript/video reference
test
artifact schema
threshold source
```

Return those exact paths in the initial audit.
Do not implement a generic correlation substitute.

**Audit anchors (verify call sites and artifact schema before new code):**

- [`apps/workbench/src/robustness/wfc/gate.py`](../../apps/workbench/src/robustness/wfc/gate.py)
- [`apps/workbench/config/wfc_gate.yaml`](../../apps/workbench/config/wfc_gate.yaml)
- [Walk Forward campaigns / WFC gate](../workbench/WALK_FORWARD_CAMPAIGNS.md)
- [ROBUSTNESS_TESTING_SPEC.md](ROBUSTNESS_TESTING_SPEC.md)

### Required meaning

Walk Forward Correlation evaluates whether the **shape and ranking of the full parameter surface** persist across the prescribed walk-forward comparison.

It is not:

```text
correlation of two equity curves
correlation of two best parameters
correlation of two scalar walk-forward scores
correlation of trade timestamps
correlation of returns from only the winning cell
```

The process must run the same complete parameter universe across the two prescribed walk-forward sides or windows defined by the existing implementation.

For each complete parameter identity `i`, align:

```text
x_i = performance metric from the first prescribed walk-forward surface
y_i = performance metric from the second prescribed walk-forward surface
```

Alignment must use the complete parameter hash, not row order.

The two surfaces must have:

```text
identical parameter definitions
identical parameter units
identical grid shape
identical metric definition
identical cost treatment
identical feature recipe
identical model implementation
declared chronological windows
```

### Required WFC outputs

```text
wfc_run_a_id
wfc_run_b_id
run_a_period
run_b_period
parameter_universe_hash
aligned_parameter_hashes
expected_cell_count
aligned_cell_count
missing_from_a
missing_from_b
metric_name
metric_unit
metric_in_first_surface
metric_in_second_surface
Pearson correlation
Spearman rank correlation
Pearson sample count
Spearman sample count
scatter rows
quadrant counts
high/high stable-region rows
low/high instability rows
high/low overfit-risk rows
constant-vector detection
NaN handling
tie handling
correlation threshold authority
pass/fail decision
failure reason
```

### Fail-closed conditions

WFC must reject or block when:

```text
the parameter grids differ
parameter hashes cannot be aligned
cells are silently dropped
sample size is below the declared minimum
either vector is constant
NaN handling is undeclared
metric definitions differ
fees or slippage differ
the feature recipe differs
the two windows overlap illegally
Pearson is missing
Spearman is missing
the threshold lacks authority
only the winning parameter is compared
```

Do not allow a profitable single OOS cell to rescue a non-correlated parameter surface.

Write:

```text
generation_<N>/gates/<candidate_id>/walk_forward_correlation_gate.json
```

Both must pass independently:

```text
regular_walk_forward_gate == PASS
walk_forward_correlation_gate == PASS
```

Neither result may be synthesized from the other.

---

## 11. Gate 6 — Statistical and Monte Carlo robustness gauntlet

Use existing Workbench and robustness producers.
Do not create a new robustness package.

Certifying mode must use:

```python
allow_partial = False
```

Require every applicable test from the existing robustness specification:

```text
bootstrap confidence interval
Deflated Sharpe Ratio
CSCV
PBO
Holm multiple-testing correction
Benjamini-Hochberg correction where configured
fee multiplier stress
slippage multiplier stress
latency stress
parameter perturbation
null-strategy battery
planted-alpha control
adversarial perturbation
sample-size floor
trade-count floor
contamination/provenance checks
```

For each statistical output, require:

```text
formula authority
input population
unit
denominator
sample size
random seed
number of iterations
confidence level
null hypothesis
failure semantics
artifact hash
```

A `None`, missing, stale, malformed, insufficient-sample, or partial result cannot pass.

Write:

```text
generation_<N>/gates/<candidate_id>/statistical_robustness_gate.json
```

Spec: [ROBUSTNESS_TESTING_SPEC.md](ROBUSTNESS_TESTING_SPEC.md)

---

## 12. Gate 7 — HftBacktest execution realism

The certifying autonomous configuration must not default to:

```yaml
run_hft_campaign: false
hft_stages: [0]
```

The certifying configuration must require the full applicable individual-realism stages defined by the existing HFT campaign implementation.
At minimum:

```yaml
run_hft_campaign: true
accelerated_mode: false
stepping_mode: event
```

Use:

```text
packages/backtest_pipeline/src/hft_campaign/
packages/backtest_pipeline/src/hftbacktest_realism.py
packages/backtest_pipeline/src/hft_backtest_builder.py
```

Only candidates that passed all prior gates may enter HftBacktest.
Do not generate the HFT scenario manifest from every VectorBT promotion if some failed robustness.

Require exact equality of:

```text
candidate_id
feature_recipe_hash
manifest_hash
screening artifact hash
robustness artifact hash
source-data hashes
```

Each scenario must have:

```text
prepared replay-data reuse
fresh HftBacktest engine
independent order book
independent orders
independent positions
independent queue state
event-driven stepping
latency model
queue/fill model
fee model
missed-fill accounting
partial-fill accounting
adverse-selection/markout output
execution-adjusted PnL
```

Map results per candidate.
Do not assign one campaign-level HFT status to every candidate.

Write:

```text
generation_<N>/gates/<candidate_id>/hftbacktest_gate.json
```

Spec: [HFTBACKTEST_REALISM_ENGINE_SPEC.md](HFTBACKTEST_REALISM_ENGINE_SPEC.md)

---

## 13. Gate 8 — Final candidate certification

A candidate receives `FINAL_PASS` only when:

```text
ontology_gate == PASS
manifest_gate == PASS
vectorbt_gate == PASS
surface_stability_gate == PASS
regular_walk_forward_gate == PASS
walk_forward_correlation_gate == PASS
statistical_robustness_gate == PASS
hftbacktest_gate == PASS
```

Allowed final states:

```text
FINAL_PASS
ONTOLOGY_REJECTED
VECTORBT_REJECTED
SURFACE_REJECTED
REGULAR_WF_REJECTED
WFC_REJECTED
STATISTICAL_REJECTED
HFT_REJECTED
BLOCKED_MISSING_EVIDENCE
INFRASTRUCTURE_FAILED
```

Only `FINAL_PASS` candidates may be:

```text
elite
best_candidate
used for exploitation
sent to combined replay
called certified
considered for later paper/live review
```

A score cannot override a failed gate.

---

## 14. Correct generation-summary behavior

Modify the existing summary logic so it does not promote incomplete evidence.

The generation summary must include every proposed candidate, not only VectorBT promotions.

For every candidate record:

```text
candidate_id
parent_candidate_id
feature_recipe_hash
manifest_hash
proposal type
feature-family mutation
execution-parameter mutation
all gate statuses
all gate receipt paths
all rejection reasons
research score
final status
```

Set:

```python
elite = final_status == "FINAL_PASS"
```

Select `best_candidate_id` only from `FINAL_PASS`.
Do not allow:

```text
robustness_pass=None
HFT not_run
WFC not_run
regular walk-forward not_run
partial evidence
```

to produce an elite.

Owner: [`packages/research_pipeline/generation_summary.py`](../../packages/research_pipeline/generation_summary.py)

---

## 15. Karpathy-style autonomous learning behavior

The system must operate without human intervention after launch.

### Exploitation

Generate exploitation children only from `FINAL_PASS` parents.
Permitted bounded changes include:

```text
one supported feature
one supported feature family
one source asset
one lag
one rolling window
one approved transformation
one approved interaction
one context gate
one regime gate
one latency condition
execution parameters inside a stable plateau
```

### Exploration

Keep a deterministic exploration fraction.
Exploration candidates must pass ontology admission before compute.

### When no candidate passes

Do not:

```text
lower thresholds
mark rejected candidates elite
use holdout results for tuning
bypass WFC
bypass HftBacktest
invent missing evidence
```

Use structured failure information to propose a different supported candidate.

Examples:

```text
unavailable VIX data → do not repeat the same VIX-dependent recipe
poor WFC → explore a different stable feature family or simpler surface
latency failure → explore only declared latency-robust variants
queue failure → do not treat VectorBT profitability as execution success
```

If no new ontology-supported candidate remains, stop with:

```text
no_supported_exploration_remaining
```

---

## 16. Learning memory

Extend the existing advisory memory rather than adding another memory system.

Record:

```text
candidate_id
parent_candidate_id
generation
feature_recipe_hash
proposal reason
mutation type
ontology result
VectorBT result
surface-stability result
regular walk-forward result
Walk Forward Correlation Pearson
Walk Forward Correlation Spearman
WFC rejection reason
bootstrap result
DSR result
CSCV/PBO result
multiple-testing result
stress results
HftBacktest result
final status
research score
```

Memory remains advisory.
It cannot override gates.
LLM availability cannot be required for the loop to continue.

Owner: [`packages/research_pipeline/review_memory.py`](../../packages/research_pipeline/review_memory.py)

---

## 17. Honest generation completion

Do not write `.generation_complete` before validation.

A generation is complete only when every proposed candidate has a valid terminal status and all required receipts validate.

Validate:

```text
candidate manifests
ontology receipts
VectorBT receipts
surface-stability receipts
regular walk-forward receipts
WFC receipts
statistical receipts
HftBacktest receipts
final certification receipts
generation summary
artifact hashes
```

Generation statuses:

```text
pending
in_progress
blocked
complete
failed
aborted
```

A generation may complete with zero `FINAL_PASS` candidates.
It may not complete while required evidence is missing.

---

## 18. Deterministic resume

Expand the campaign configuration hash to cover all semantic inputs:

```text
candidate limits
feature-family search settings
exploration fraction
VectorBT scope and budgets
parameter universe
regular walk-forward periods
regular walk-forward selection rules
WFC paired windows
WFC metric
WFC thresholds
surface-stability thresholds
statistical thresholds
Monte Carlo seeds/iterations
HFT enabled state
HFT stages
latency model
queue/fill model
fee model
engine versions
source-data hashes
gate versions
stop conditions
```

On resume:

```text
validate manifest
validate config hash
validate every receipt
reuse valid complete gates
rerun missing/corrupt gates
never mutate a frozen generation
never skip a gate because a marker exists
never duplicate a tested feature recipe
```

Owner: [`packages/research_pipeline/generation_state.py`](../../packages/research_pipeline/generation_state.py)

---

## 19. Performance requirements

The full gate chain is not permission to restore the slow implementation.

### VectorBT

Require:

```text
no run_pipeline.py subprocess per unit
long-lived workers
one process layer
shared event/feature loading
raw signal computed once per recipe
matrix-batched trials
bounded chunking
native thread limits
individual unit artifacts
```

### HftBacktest

Require:

```text
prepared data reused
feature timeline reused
long-lived worker processes
fresh engine per scenario
event-driven stepping
individual replay before combined replay
bounded worker recycling
```

Run an identical-scope benchmark and report projected time for the declared full campaign.

---

## 20. Required automated tests

Add tests for:

```text
ontology gate runs before VectorBT
unbacked candidate cannot consume compute
candidate manifest is immutable
VectorBT recipe hash matches manifest
surface stability is required
regular walk-forward is required
WFC is required independently
regular WF PASS cannot substitute for WFC
WFC PASS cannot substitute for regular WF
WFC aligns rows by parameter hash
WFC rejects mismatched grids
WFC rejects missing cells
WFC rejects constant vectors
WFC handles ties deterministically
WFC emits Pearson
WFC emits Spearman
WFC emits scatter rows
WFC emits quadrant counts
WFC does not compare only best parameters
WFC does not correlate equity curves
bootstrap/Monte Carlo evidence is required
DSR is required
CSCV/PBO is required
multiple-testing control is required
partial robustness cannot pass
HFT not_run cannot produce elite
best candidate comes only from FINAL_PASS
HFT receives only full robustness passers
HFT status maps per candidate
holdout data cannot enter proposal memory
generation marker is written after validation
corrupt receipts rerun on resume
changed config blocks resume
Generation N+1 uses validated Generation N evidence
a child changes a real feature-recipe dimension
duplicate recipe hashes are not retested
three generations run unattended
```

Include planted PASS and planted FAIL cases for every gate.

---

## 21. Three-generation acceptance run

Run one deterministic unattended three-generation campaign.

The report must include:

```text
Generation 0 proposed candidates
ontology rejects
VectorBT rejects
surface rejects
regular-WF rejects
WFC rejects
statistical rejects
HFT rejects
FINAL_PASS candidates
Generation 1 parent-child recipe changes
Generation 2 parent-child recipe changes
deduplication counts
stop reason
```

At least one later-generation child must differ in a real feature-recipe dimension, not only threshold or holding period.

---

## 22. Local `rg` preflight for this implementation

After every edit batch, run a bounded local search loop.

Required negative searches:

```bash
rg -n "robustness_pass is not False|hft_replay_status.*not in" \
  packages apps tests
rg -n "allow_partial\s*=\s*True|run_hft_campaign:\s*false|hft_stages:\s*\[0\]" \
  packages apps config tests
rg -n "elite.*vectorbt_pass|best_candidate.*vectorbt" \
  packages/research_pipeline
rg -n "WFC.*optional|walk.forward.correlation.*not_run" \
  packages apps config tests
```

Required positive searches:

```bash
rg -n "regular_walk_forward_gate|walk_forward_correlation_gate" \
  packages apps tests
rg -n "pearson|spearman|parameter_universe_hash|aligned_parameter_hashes" \
  packages apps tests
rg -n "FINAL_PASS|run_generation_gate_chain|hftbacktest_gate" \
  packages apps tests
```

Then:

```bash
git diff --check
```

Repeat local preflight at most three times.
Unresolved actionable hits block review.

---

## 23. Mandatory Greptile PR GrepLoop

The owner has stated that **Greptile is installed and connected to this repository**.
Greptile is therefore the required external PR AI reviewer for this task.
Codex, Copilot, Bugbot, local agent review, or a prose self-review does not substitute for Greptile.

Full procedure: [Greptile PR GrepLoop](../ai/GREPLOOP.md)

### Greptile loop procedure

#### Step 1 — Open or identify the PR

```bash
gh pr view --json number,headRefName,headRefOid,url
```

Record:

```text
PR number
branch
current head SHA
```

#### Step 2 — Push the current verified commit

```bash
git push
```

#### Step 3 — Request Greptile review

Use the repository's installed Greptile trigger:

```bash
gh pr comment <PR_NUMBER> --body "@greptileai"
```

If the installed integration uses an automatic trigger, confirm that Greptile reviewed the current head SHA. Do not assume the old review applies to the new commit.

#### Step 4 — Fetch all Greptile review surfaces

```bash
gh pr view <PR_NUMBER> --json body,reviews,comments,statusCheckRollup
gh api --paginate \
  "repos/{owner}/{repo}/issues/<PR_NUMBER>/comments?per_page=100"
gh api --paginate \
  "repos/{owner}/{repo}/pulls/<PR_NUMBER>/comments?per_page=100"
```

Inspect:

```text
general Greptile summary
inline Greptile comments
review submissions
updated-in-place Greptile comments
review head SHA
```

#### Step 5 — Fix all actionable Greptile findings

For each finding, classify:

```text
actionable
already fixed
false positive with concrete evidence
informational
```

Fix every actionable issue.
Do not make unrelated architectural changes in response to informational comments.

#### Step 6 — Re-run required gates

After fixes:

```text
local rg preflight
dual-pass reviewer
ontology gate
scope-appropriate tests
git diff --check
```

#### Step 7 — Push and request Greptile again

```bash
git push
gh pr comment <PR_NUMBER> --body "@greptileai"
```

#### Step 8 — Stop condition

The Greptile gate passes only when:

```text
Greptile reviewed the current PR head SHA
Greptile confidence ≥ 4/5 (4/5 or 5/5 in summary when present)
zero unresolved actionable Greptile findings remain
all required local verification is green
```

Run at most five Greptile iterations. Do **not** advance to split PR-B/C or
Phase 10 until the current PR meets confidence ≥ 4/5 **and** zero actionable
findings on current head. Codex/@codex review does **not** satisfy this gate.
After five unsuccessful iterations:

```text
pr-greptile-review: BLOCKED
merge-ready: no
```

Report all remaining findings.

If Greptile is genuinely unavailable or unauthenticated:

```text
pr-greptile-review: unavailable(<reason>)
merge-ready: no
```

Only an explicit owner waiver may change that status.
Do not silently fall back to `@codex review`.

---

## 24. Final acceptance checklist

The assignment fails unless all are true:

```text
[ ] existing autoresearch loop was extended, not replaced
[ ] ontology admission occurs before compute
[ ] candidates are frozen before evaluation
[ ] optimized VectorBT path is active
[ ] surface-stability gate passes
[ ] regular walk-forward passes
[ ] Walk Forward Correlation passes separately
[ ] WFC uses full aligned parameter surfaces
[ ] WFC emits Pearson and Spearman
[ ] WFC cannot be replaced by two summary values
[ ] statistical/Monte Carlo gauntlet passes
[ ] HftBacktest realism passes
[ ] only FINAL_PASS candidates become elites
[ ] only FINAL_PASS candidates can become best_candidate
[ ] next generation changes feature recipes
[ ] holdout results cannot train the learner
[ ] resume is deterministic
[ ] completion markers are honest
[ ] three generations run unattended
[ ] active-path performance benchmark exists
[ ] local rg preflight is clean
[ ] dual-pass reviewer has zero red findings
[ ] ontology implementation gate passes
[ ] full-scope tests pass
[ ] Greptile reviewed the current head SHA
[ ] zero unresolved actionable Greptile findings remain
```

---

## 25. Required final developer response

Return this table only:

```text
Requirement
Pass/Fail
Exact file/function
Authority
Test command
Evidence artifact
Measured result
Remaining defect
```

Include separate rows for:

```text
ontology admission
candidate freeze
VectorBT performance path
surface stability
regular walk-forward
Walk Forward Correlation
WFC parameter alignment
WFC Pearson
WFC Spearman
bootstrap/Monte Carlo
DSR
CSCV/PBO
multiple-testing correction
HftBacktest
final certification
elite selection
best-candidate selection
feature-family learning
holdout isolation
resume
generation completion
three-generation unattended run
local rg preflight
dual-pass reviewer
ontology implementation gate
full-scope verification
Greptile iteration count
Greptile current-head review proof
Greptile unresolved actionable count
```

Do not mark an item complete because a class, function, document, or test fixture exists.
It passes only when the canonical autonomous command executes that behavior and produces validated artifacts.
