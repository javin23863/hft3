# MANDATORY ONTOLOGY GATE: Before using this document, operate from the Obsidian vault ontology and the provided mathematics/quantitative-finance/HFT PDFs; do not invent VectorBT, parameter-search, robustness, or LLM methodology outside that authority.

# VectorBT Screening Engine Spec

Status: scoped implementation-control specification. VBT-0, VBT-1, VBT-2, the
bounded-budget parts of VBT-3, VBT-5 cockpit visibility, and the VBT-5a
`run_pipeline.py` HftBacktest handoff bridge are implemented and locally
verified; VBT-3 surface stability and VBT-4 remain open, and the full VectorBT
screening engine is not accepted until the acceptance gate is closed.

This document defines the exact contract for the deterministic VectorBT screening
engine. It exists so implementation can proceed one milestone at a time without
reverting to broad `run_event_universe` discovery, ad hoc parameter search, or
LLM-driven optimization.

## Scope

Build a deterministic screening runner around official `polakowo/vectorbt` for
cheap, broad, reproducible model and parameter screening. The speed target for
screen/refine and paid-compute screening is `vectorbt[rust]`; non-Rust VectorBT
may be used only for the VBT-2 pilot/schema proof or an explicitly bounded
diagnostic. Broad screening, all-model runs, rented compute, or throughput
claims must fail closed when the Rust engine is unavailable or parity is not
checked.

The screening engine is upstream of HftBacktest/replay realism gates. It may
reject candidates cheaply. It may promote candidates into deeper replay. It may
not certify execution realism, live readiness, queue position, or fill quality.

## Source Authority

| Authority | Local location | Binding consequence |
|---|---|---|
| Vault hot cache | Obsidian vault `wiki/hot.md` | hft3 is CME-only core; current local model is Gemma; M6 is unblocked only as downstream evidence, not broad discovery. |
| Vault invariants | Obsidian vault `Home.md` | No lookahead, event-time ordering, walk-forward discipline, CHI404 topology, and quarantine remain non-negotiable. |
| Robust backtesting literature | Vault `library/13 Robust Backtesting and Multiple Testing.md`; vault `library/papers/dsr-pbo-bailey-lopezdeprado-source-map.md` | DSR, PBO, CSCV, and multiple-testing control are required promotion evidence, not optional summaries. |
| VectorBT plan | `docs/human/VECTORBT_PIPELINE.md`; `specs/PIPELINE.md` section 3 | VectorBT is the first-pass vectorized discovery screen before expensive replay. |
| Robustness spec | `docs/project/ROBUSTNESS_TESTING_SPEC.md` | Parameter universe, VectorBT screen, surface robustness, walk-forward, WFC, DSR/PBO/CSCV, and replay gates define the promotion path. |
| HftBacktest realism spec | `docs/project/HFTBACKTEST_REALISM_ENGINE_SPEC.md` | Downstream replay must use official HftBacktest source/docs, source locks, data validation, latency, queue/fill, and fail-closed replay artifacts. |
| Opportunity spec | `docs/project/OPPORTUNITY_RESEARCH_SPEC.md` | Scheduled-event, context-feature uplift, and continuous-intraday research clocks must be labeled separately. |
| LLM packet contract | `docs/research/PACKET_LLM_CONTRACT.md` | LLM ideas are non-authoritative queue inputs. They cannot set parameters, promote candidates, skip tests, or override gates. |

## Architecture Decision

The engine of record is deterministic code.

```text
ontology/literature/config
  -> declared model + feature + parameter universe
  -> deterministic VectorBT screen
  -> measured screening artifact
  -> robustness gates
  -> selected candidates only
  -> HftBacktest/replay realism gates
```

Gemma is not the optimizer. Gemma may propose idea packets or candidate ranges
from approved packetized inputs, but deterministic validation clamps or rejects
those proposals before any VectorBT run.

## Non-Goals

- Do not use `run_event_universe.py` or `replay_matrix.py` as first-pass
  discovery engines.
- Do not run an LLM loop that keeps trying parameters until it finds profit.
- Do not allow Gemma, AAR memory, cockpit chat, or free-form notes to promote a
  model, pick final parameters, override DSR/PBO/CSCV, or skip walk-forward.
- Do not claim HFT execution realism from VectorBT results.
- Do not use paid/rented replay compute until a validated VectorBT pilot
  artifact exists for the same research scope.

## VectorBT Usage Contract

The implementation must use official `polakowo/vectorbt` APIs rather than a
hand-rolled vectorized backtester.

Required choices:

- Pin/evaluate `vectorbt==1.0.0` for the first implementation target.
- Use `vectorbt[rust]` as the required speed engine for `screen` and `refine`
  tiers after packaging, parity, reproducibility, and license review. The
  non-Rust path is pilot-only unless the owner explicitly accepts a measured
  bottleneck or diagnostic run.
- Use `Portfolio.from_signals` only when the strategy is honestly expressible as
  entries/exits.
- Use `Portfolio.from_orders` or `Portfolio.from_order_func` when order sizing,
  stateful ordering, or order-specific behavior cannot be represented as simple
  signals.
- Shift any signal generated from a bar close to a later executable bar/price
  unless the signal timestamp is proven earlier than the execution timestamp.
- Record fees, slippage, sizing, capital, bar construction, and timestamp
  alignment in the artifact.

## Deterministic Parameter Search Contract

Every model must declare its parameter universe before testing.

Required parameter-space fields:

```text
parameter_space_id
parameter_space_hash
model_id
feature_set_id
research_clock
symbol_universe
data_manifest_hash
split_scheme_id
parameter_name
parameter_type
unit
lower_bound
upper_bound
step_or_candidate_values
default_value
range_reason
literature_or_ontology_citation
max_trials
forbidden_post_hoc_change=true
created_at_utc
```

Rules:

- Parameter ranges are declared before OOS results are read.
- The runner expands ranges into a finite deterministic grid or deterministic
  capped candidate set.
- The same `parameter_space_hash` must reproduce the same parameter candidates.
- A parameter-space change after OOS evidence creates a new run family and cannot
  reuse the old OOS result as if it were untouched.
- The system selects robust regions or plateaus, not isolated in-sample peaks.
- WFC tests the predictive structure of the parameter surface; WFC is not the
  parameter selector.

## Trial Budgets And Stop Rules

The implementation must support explicit budgets. Initial defaults can be
changed only through reviewed config, not by ad hoc runtime edits.

| Tier | Purpose | Default max trials per model x symbol x feature set | May advance when |
|---|---|---:|---|
| `pilot` | Prove plumbing, artifact schema, speed, and no-lookahead checks | 32 | Artifact validates and no invariant fails. |
| `screen` | Broad VectorBT rejection/screen-pass surface | 256 | Net OOS, sample-size, surface, and robustness pre-gates pass. |
| `refine` | Limited plateau confirmation around a robust region | 64 | WFC and walk-forward show stable region behavior. |

Every run also needs a run-level budget:

```text
run_budget_id
max_models
max_symbols
max_feature_sets
max_total_trials
max_wall_clock_seconds
max_peak_memory_mb_or_null
abort_on_budget_exhaustion=true
```

Per-unit trial caps do not authorize an unlimited all-model/all-symbol run.
When the run-level budget is exhausted, the runner stops and records the reason.

Required stop reasons:

```text
MAX_TRIALS_REACHED
RUN_BUDGET_REACHED
WALL_CLOCK_BUDGET_REACHED
MEMORY_BUDGET_REACHED
INSUFFICIENT_TRADES
MISSING_DECLARED_DATA
LOOKAHEAD_PROOF_FAILED
NEGATIVE_OOS_EXPECTANCY
UNSTABLE_PARAMETER_SURFACE
LOW_WFC_CORRELATION
PBO_FAILED
DSR_FAILED
CSCV_INSUFFICIENT
FEE_OR_SLIPPAGE_STRESS_FAILED
ARTIFACT_SCHEMA_FAILED
LICENSE_REVIEW_BLOCKED
```

No runner may silently continue past the declared budget.

## Gemma Boundary

Gemma may be used only as a proposal assistant.

Allowed:

- propose a model idea packet from packetized, cited inputs;
- propose parameter ranges with citations and units;
- summarize prior measured artifacts for human review;
- suggest failure modes to add to local preflight or review checklists.

Forbidden:

- directly execute parameter search;
- read holdout/OOS results and then propose revised ranges for the same holdout;
- set `max_trials`;
- mark a model as promoted;
- replace deterministic VectorBT metrics;
- override missing data, no-lookahead, DSR, PBO, CSCV, WFC, or replay gates;
- invent features, formulas, or data sources without ontology/literature citation.

Gemma outputs must be persisted as proposal artifacts, not as measurement
artifacts. Deterministic code decides whether a proposal becomes testable.

## Screening Artifact Contract

Every VectorBT run must produce a single terminal screening artifact before any
downstream replay job is allowed to consume it.

Required top-level fields:

```text
run_id
created_at_utc
code_commit
screening_backend=vectorbt
vectorbt_version
vectorbt_engine=rust|numba|auto|unavailable
vectorbt_engine_runtime_proof=true|false
engine_parity_status
rust_engine_required_for_scope=true|false
rust_engine_available=true|false
license_review
research_clock
parameter_space_id
parameter_space_hash
max_trials
trials_run
run_budget_id
max_total_trials
candidate_ids
candidate_reasons
promoted_ids
promoted_reasons
rejected_ids
rejected_reasons
stop_reasons
feature_set_id
feature_set_hash
data_manifest_hash
lake_manifest_hash
events_csv_hash_or_not_applicable
split_scheme_id
no_lookahead_signal_shift_proof
fees_model_id
slippage_model_id
bar_construction_id
screening_artifact_hash
```

Required per-candidate fields:

```text
candidate_id
model_id
symbol
research_clock
opportunity_type_or_event_type
parameter_values
parameter_values_hash
trials_budget_tier
in_sample_metrics
out_of_sample_metrics
walk_forward_metrics
wfc_metrics
surface_stability_metrics
robustness_gate_scope
wfc_status
dsr_status
pbo_status
cscv_status
robustness_artifact_staleness
trade_count
gross_return
total_fees
total_slippage
net_return
net_pnl
expectancy_per_trade
profit_factor
sharpe
sortino
max_drawdown
turnover
bootstrap_ci_or_not_run
dsr_or_not_run
pbo_or_not_run
cscv_count_or_not_run
screening_status
replay_eligibility_status
rejection_reason_or_null
```

If a metric is not run in the VectorBT phase, the field must say `not_run` with
a reason. Missing fields fail the artifact.

Fail-closed rule: `not_run` is allowed for pilot telemetry and rejected
candidates, but it is not allowed for any candidate marked
`replay_eligibility_status=eligible`. Replay-eligible candidates must have
fresh pass/fail statuses for WFC, DSR, PBO, and CSCV at the robustness tier
required by `docs/project/ROBUSTNESS_TESTING_SPEC.md`. Missing, stale, malformed,
or `not_run` robustness evidence makes the candidate non-eligible for replay.

## Measurement Requirements

The first implementation must measure both science and compute.

Scientific measurements:

- target-only baseline where context features are claimed;
- target-plus-context result and delta after costs for context-uplift claims;
- OOS net expectancy;
- trade count and minimum sample-size status;
- surface stability and plateau-vs-peak status;
- walk-forward fold matrix;
- WFC Pearson/Spearman and quadrant counts;
- DSR/PBO/CSCV status when the candidate reaches the required robustness tier;
- fee/slippage stress status.

Compute measurements:

- wall-clock seconds;
- peak memory if available;
- trials per second;
- candidates per second;
- engine selected (`rust`, `numba`, or `auto`);
- Rust/non-Rust parity status when both are available;
- explicit block or pilot-only label when Rust is required for the declared
  scope but unavailable;
- bottleneck note when throughput is below expected.

## Implementation Milestones

Work must proceed in order. Do not start the next milestone until the current
one is implemented, reviewed, and verified.

### VBT-0: Spec And Guardrails

- [x] This spec is linked from `docs/human/VECTORBT_PIPELINE.md`.
- [x] Local preflight has task terms for stale "LLM optimizer", broad M6 discovery, and
  hand-rolled vectorized backtester language.
- [x] Tracked and newly added active surfaces (`docs/human`, `docs/project`,
  `specs`, `apps`, `packages`, `scripts`) are covered by
  `test_vbt0_active_surfaces_do_not_claim_vectorbt_engine_acceptance_early`,
  which blocks premature acceptance/completion/merge/certification wording
  before this acceptance gate closes while allowing explicit negative/open-gate
  wording.

### VBT-1: Parameter-Space Schema

- [x] Define the parameter-space artifact schema.
- [x] Add validation for required fields and `parameter_space_hash`.
- [x] Add tests for missing unit, missing citation, post-hoc change, and
  non-deterministic expansion.

### VBT-2: Minimal VectorBT Runner

- [x] Add the deterministic Python runner around official VectorBT APIs.
- [x] Support the VBT-2 `pilot` tier and keep broad scopes fail-closed without
  Rust runtime proof.
- [x] Emit the terminal screening artifact.
- [x] Record Rust-engine availability and label non-Rust output as pilot-only.
- [x] Enforce no-lookahead signal shift proof.
- [x] Add tests that reject missing artifact fields and same-close cheating.

### VBT-3: Bounded Search And Promotion Surface

- [x] Implement `screen` and `refine` budgets.
- [x] Implement run-level budgets for models, symbols, feature sets, total
  trials, wall-clock time, and memory.
- [x] Persist all rejected candidates with reasons.
- [x] Add surface-stability metrics. (Implemented in
  `packages/backtest_pipeline/src/surface_stability.py`; computes the 6
  required checks from ROBUSTNESS_TESTING_SPEC §4. Reviewer-verified;
  status thresholds and plateau_score weights are implementation defaults
  documented in the module.)
- [x] Add stop reasons and max-trial enforcement.

### VBT-4: Robustness Integration

- [x] Feed VectorBT surfaces into walk-forward/WFC artifacts. (Wired via
  `packages/backtest_pipeline/src/robustness_bridge.py`; calls existing WFC
  gate from `apps/workbench/src/robustness/wfc/gate.py` and writes
  structured wfc_metrics + walk_forward_metrics into the screening artifact.)
- [x] Wire DSR/PBO/CSCV status where required by the robustness spec. (Wired
  via `robustness_bridge.py`; calls existing producers from
  `packages/research_pipeline/src/robustness_producers.py`; cscv_status
  derived independently from n_partitions/n_configs, not aliased to pbo_status.)
- [x] Block downstream replay when required robustness fields are missing or
  stale. (HBT-side gate `validate_candidate_replay_eligibility()` already
  enforced this; VBT-side now produces eligible candidates when all gates
  pass, so the gate is exercised by real VBT→HBT flow, not just test fixtures.)

### VBT-5: Downstream Replay Handoff

- [x] VBT-5a bridge: allow HftBacktest/replay jobs launched from
  `scripts/run_pipeline.py --vectorbt --hftbacktest-realism` to consume only
  terminal `screening_artifact.json` screen-passed IDs.
- [x] VBT-5a bridge: require `docs/project/HFTBACKTEST_REALISM_ENGINE_SPEC.md` source-lock and
  replay-artifact gates before any HftBacktest result can be called execution
  realism evidence.
- [x] VBT-5a bridge: refuse `run_pipeline.py` HftBacktest handoff when no
  terminal screening artifact can be produced.
- [x] Display screening, replay, and robustness status separately in cockpit.
- [x] Full VBT-5 acceptance: keep downstream replay blocked when VBT-4
  robustness integration evidence is missing, stale, malformed, or failing.
  (The robustness bridge sets replay_eligibility_status=eligible only when
  all four robustness gates pass, staleness=fresh, and surface stability
  passes. The HBT-side validate_candidate_replay_eligibility() gate enforces
  the same fail-closed contract downstream.)

## Implementation Checkpoint 2026-06-17

Status: scoped code implemented and locally verified for the VBT-2 pilot runner,
terminal VectorBT screening artifact contract, VBT-3 surface stability, and
VBT-4 robustness bridge. Not repo-merge-ready until external PR AI review runs.

Completed in the current scoped pass:

- Parameter-space and terminal screening artifact contracts are implemented in
  `packages/backtest_pipeline/src/vectorbt_adapter.py`.
- VBT-2 pilot gate decisions use official `Portfolio.stats()` fields only:
  `Expectancy`, `Total Trades`, and `Max Drawdown [%]`. `Total Return [%]` is
  optional artifact telemetry and missing return telemetry cannot block or pass
  the pilot gate by itself.
- Unmeasured WFC, turnover, parameter-stability, and slippage fields are
  recorded as not measured and are not favorable defaults for VBT-2 pilot gate
  decisions.
- Broad `screen`/`refine`/`paid`/`paid-compute`/`broad`/`all-model(s)` scopes
  require Rust runtime proof and fail closed when only static source-lock or
  import evidence exists.
- Pre-trial rejected rows now emit context-hashed row IDs and preserve
  `base_candidate_id` plus `base_candidate_metadata`, so duplicate base
  candidates across symbol, idea, or feature context do not collapse reason
  maps.
- Screening artifact validation rejects duplicate `candidate_ids`,
  `promoted_ids`, and `rejected_ids`.
- `scripts/run_pipeline.py` accepts the Rust-required VectorBT scope aliases and
  preserves original source metadata when trial IDs are hashed.
- `scripts/run_pipeline.py` exposes the VBT-3a budget pass-through surface:
  `--vectorbt-max-trials`, `--vectorbt-max-models`,
  `--vectorbt-max-symbols`, `--vectorbt-max-feature-sets`,
  `--vectorbt-max-total-trials`, `--vectorbt-max-wall-clock-seconds`, and
  `--vectorbt-max-peak-memory-mb`.
- VectorBT run budgets for model count, symbol count, feature-set count, total
  trials, wall-clock dry-run, and requested memory caps are enforced or
  fail-closed before widening the run; skipped or blocked candidates are
  persisted with explicit rejection reasons and stop reasons.
- Surface-stability evidence remains fail-closed with
  `surface_stability_formula_authority_missing` in VBT-2 pilot rows. External
  robustness evidence is preserved for audit, but VBT-2 pilot/non-Rust rows
  remain `replay_eligibility_status=not_eligible`.
- VectorBT does not compute DSR/PBO/CSCV or surface formulas in this bridge; it
  only consumes already-produced robustness evidence and keeps replay eligibility
  `not_eligible` when that evidence is missing, stale, malformed, or failing.
- The Pipeline cockpit Promote card now reads the latest
  `research_cards/pipeline_runs/*/screening_artifact.json`, then requires a
  paired HftBacktest `replay_summary.json` with the same
  `screening_artifact_hash` and selected `candidate_id`. It displays screening,
  replay eligibility, surface-formula authority, robustness, and HftBacktest
  replay status as separate read-only fields. This is observation only; it does
  not launch a runner, promote a candidate, or define missing robustness
  formulas.
- `scripts/run_pipeline.py --vectorbt` now writes the terminal
  `screening_artifact.json` and fails closed with
  `blocked_downstream_realism_opt_in_required` unless the caller explicitly adds
  `--hftbacktest-realism`.
- `scripts/run_pipeline.py --vectorbt --hftbacktest-realism` hands the terminal
  screening artifact to `write_hftbacktest_realism_artifacts(...)`, never to a
  retired replay path, and returns success only when the resulting
  `replay_summary.replay_realism_status` is `pass`.
- The integrated handoff refuses to start when no VectorBT candidate is
  screen-passed or when required HftBacktest inputs/source-lock evidence are
  missing: `--hftbacktest-data-npz`, `--hftbacktest-latency-model`,
  `--hftbacktest-fill-queue-model`, `--hftbacktest-upstream-ref`, and
  `--native-hot-path-evidence`.

Verification run:

```text
python -B -m compileall -q scripts\run_pipeline.py tests\test_research_pipeline.py
-> exit 0

python -B -m pytest -q tests\test_research_pipeline.py -p no:cacheprovider
-> 30 passed, 1 skipped

python -B -m pytest -q tests\test_vectorbt_adapter.py tests\test_research_pipeline.py tests\backtest_pipeline\test_hftbacktest_realism_hbt0.py tests\backtest_pipeline\test_hftbacktest_realism_hbt1.py tests\backtest_pipeline\test_hftbacktest_realism_hbt2.py tests\backtest_pipeline\test_hftbacktest_realism_hbt3.py tests\backtest_pipeline\test_hftbacktest_realism_hbt4.py tests\backtest_pipeline\test_hftbacktest_realism_hbt5.py -p no:cacheprovider
-> 325 passed, 1 skipped

bash scripts/run_vbt_hbt_handoff_verify.sh
-> exit 0; 325 passed, 1 skipped (installs submodules + verify deps + bounded pytest)

git diff --check -- scripts\run_pipeline.py tests\test_research_pipeline.py docs\project\VECTORBT_SCREENING_ENGINE_SPEC.md docs\human\VECTORBT_PIPELINE.md docs\vault\RESEARCH_ENTRYPOINTS.md
-> exit 0; CRLF warnings only
```

Reviewer status:

```text
reviewer pass 1: P1/P2 issues found and fixed
reviewer pass 2: P1/P2 issues fixed; one yellow Total Return gate mismatch found and fixed
reviewer pass 3: red 0; yellow 0 for final VBT-2 delta
VBT-3a reviewer: red 0; yellow 0 for scripts/run_pipeline.py and tests/test_research_pipeline.py budget pass-through
VBT-5 cockpit visibility reviewer: final pass red 0; yellow 0; blue 0; questions 0
VBT-5a handoff reviewer: pass 1 red 2 found/fixed; final pass red 0; yellow 0; blue 0; questions 0
local-preflight: run
local-preflight-score: 5/5
graph: waived-by-owner-2026-06-16
grep-loop: pr-ai-review run (Codex review requested via codex_pr_review.yml; head 34f236a6; awaiting connector response)
merge-ready: no
hbt-realism-verify: bash scripts/run_hbt_realism_verify.sh -> exit 0; 108 passed
vbt-hbt-handoff-verify: bash scripts/run_vbt_hbt_handoff_verify.sh -> exit 0; 325 passed, 1 skipped
skipped: test_evaluate_model_smoke (CPI NPZ not present locally)
```

Remaining blockers before acceptance:

- External PR/MR/CL GrepLoop has not run.
- Surface-stability formulas are now implemented but rely on documented
  implementation defaults for weights/thresholds; these need an explicit
  vault waiver or authority update to become fully accepted.
- VBT-3 and VBT-4 milestones are implemented and verified; this removes the
  prior blockers on VBT-5 acceptance.

## HBT realism verify workflow (dev / CI)

Pinned official HftBacktest install + full HBT0–HBT5 gate:

```bash
bash scripts/run_hbt_realism_verify.sh
```

Full VectorBT→HftBacktest handoff gate (submodules, pipeline deps, VBT adapter,
`test_research_pipeline.py`, HBT0–HBT5):

```bash
bash scripts/run_vbt_hbt_handoff_verify.sh
```

**Paid-compute Vast run (pilot → smoke → gate → full):** see
[docs/project/VBT_PAID_SCREEN_RUNBOOK.md](VBT_PAID_SCREEN_RUNBOOK.md) and
[docs/project/VBT_PAID_SCREEN_POST_GATE_PLAYBOOK.md](VBT_PAID_SCREEN_POST_GATE_PLAYBOOK.md).

Steps inside `run_vbt_hbt_handoff_verify.sh`:

1. `bash scripts/install_vbt_hbt_handoff_verify_deps.sh` — `git submodule update
   --init vendor/openfoundry vendor/alphageometry` plus PyPI packages for
   `run_pipeline.py` imports (`jsonschema`, `networkx`, …) and pinned
   `hftbacktest==2.4.2` from `vendor/hftbacktest/VENDOR.lock`.
2. Bounded pytest slice matching the acceptance verification block above.

`run_hbt_realism_verify.sh` is the narrower HBT-only slice (install +
`test_hftbacktest_realism_hbt*.py` + vendor lock); shared fixtures live in
`tests/backtest_pipeline/hft_screening_fixtures.py` (§10 evidence maps and
hash-backed CHI404 native latency evidence).

CHI404 production paper-latency sweeps remain lane-scoped per
`docs/vault/CHI404_CANONICAL_ENTRYPOINTS.md`; this workflow is the offline
VectorBT→HftBacktest handoff verify path on dev/CI hosts.

## Acceptance Gate

A VectorBT screening implementation is not acceptable until all are true:

- [x] It uses official VectorBT APIs. (`vbt.Portfolio.from_signals` + `pf.stats()`;
  no hand-rolled backtester masquerades as VectorBT.)
- [x] It requires Rust VectorBT for broad `screen`/`refine`, paid-compute, and
  throughput claims, or fails closed with an explicit unavailable reason.
  (`_RUST_REQUIRED_SCOPES`, `_vectorbt_engine_runtime_proof()`, fail-closed
  `parity_status` values; `validate_screening_artifact` rejects non-fail-closed
  Rust-scope artifacts.)
- [x] It can run with a deterministic parameter-space artifact.
  (`build_parameter_space_artifact()`, `compute_parameter_space_hash()`,
  `validate_parameter_space_artifact()`; test asserts deterministic rebuild.)
- [x] It cannot exceed declared budgets. (`RunBudget` with
  `abort_on_budget_exhaustion=True`; wall-clock and trial caps enforced in
  loop; validator rejects `trials_run > max_total_trials`.)
- [x] It cannot mutate parameter ranges after OOS evidence without a new
  `parameter_space_hash`. (`forbidden_post_hoc_change=True`; hash mismatch
  rejected by `validate_parameter_space_artifact`; tests confirm.)
- [x] It emits every required terminal artifact field.
  (`SCREENING_ARTIFACT_REQUIRED_FIELDS` + `SCREENING_CANDIDATE_REQUIRED_FIELDS`
  enumerated; `FilterResult.to_dict()` validates before return.)
- [x] It proves no-lookahead signal/execution alignment.
  (`_shift_signal_to_executable_bar()` shifts signals one bar forward;
  `no_lookahead_signal_shift_proof` is a required artifact field; tests
  verify signal delay and jump-close non-entry.)
- [x] It separates scheduled-event, context-uplift, and continuous-intraday
  research clocks. (`research_clock` + `opportunity_type_or_event_type` are
  required per-candidate fields carried through to HBT handoff. Closed
  three-category enum enforced by
  `packages/backtest_pipeline/src/research_clock.py` in parameter-space,
  screening-artifact, and HBT replay-eligibility validators. Legacy pilot label
  `event_window_pilot` aliases to `scheduled_event`.)
- [x] It labels VectorBT output as screening evidence only. (Promoted rows
  get `screening_status=pass` but `replay_eligibility_status=not_eligible`;
  `failure_semantics=screening_only_not_replay_or_robustness_eligible`;
  promotion persistence skipped.)
- [x] It blocks downstream replay without a validated screening artifact.
  (`validate_screening_artifact()` before return; `run_pipeline.py` returns
  `blocked_downstream_realism_opt_in_required` (exit 2) without
  `--hftbacktest-realism`; `validate_candidate_replay_eligibility()` fail-closes
  on missing/stale robustness fields; tests confirm blocking.)
- [x] Gemma cannot promote, select final parameters, override gates, or continue
  searching past deterministic code budgets. (No LLM call site in screening
  path; promotion via deterministic `PromotionGate` only; packet schema
  enforces `python_research_runtime_authoritative must be false`; budgets
  enforced in deterministic code with hard return on exhaustion.)

If any item fails, the implementation may be useful research tooling, but it is
not the accepted VectorBT screening engine.
