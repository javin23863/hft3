# Pipeline Improvement PDF Implementation Plan

Status: execution plan from operator-supplied PDF, created 2026-06-25.

Active HBT-only supersession: this PDF-derived plan is historical for active
HftBacktest routing. Do not port its VectorBT -> robustness -> HftBacktest
ordering rule into the HBT-only campaign; port only source/citation or evidence
ideas after rewriting them to the HBT-only identity fields.

Source PDF: `C:\Users\MSI\Downloads\Pipeline improvement suggestions.pdf`

Extraction receipt: 6 pages, extracted with bundled `pypdf` from the Codex primary runtime.

## Purpose

Turn the PDF roadmap into hft3 code without recreating the already back-tested VectorBT/HftBacktest pipeline.

The PDF assumes two predecessor PRs:

- `7b44775d` - isolated pipeline logging handlers. This is already in the current branch history.
- `abc23d6a` - advanced autoresearch gates. This exists in the separate worktree `C:\Users\MSI\repos\hft3-advanced-models` on branch `codex/advanced-models-autoresearch`, not in the current PR #14 branch.

This plan therefore has two jobs:

1. Preserve PR #14 runtime/C++ receipt gates.
2. Integrate the advanced-models work or equivalent implementation cleanly, without touching the separate advanced-models worktree.

## Non-Negotiable Boundaries

- Do not write to or reset `C:\Users\MSI\repos\hft3-advanced-models`.
- Do not launch GPU training from the MSI workstation.
- For this legacy PDF-derived path, do not bypass VectorBT -> robustness
  evidence -> HftBacktest ordering. For the active HBT-only path, do not reuse
  this ordering as an eligibility rule.
- Do not treat RL output as a promotion receipt unless normal replay, robustness, and HftBacktest gates also pass.
- Do not introduce lookahead: all microstructure features must be computed from point-in-time snapshots or trailing windows only.
- Do not weaken the native C++ hot-path evidence gate added in PR #14.
- Graph gates remain `waived-by-owner-2026-06-16`; keep graphify artifacts out of commits.

## Literature And Documentation Receipts

The PDF cites the following source material. Code and docs must keep these as human-readable references where relevant:

| Claim area | Receipt |
|---|---|
| Microstructure feature patterns and order-book features | `https://arxiv.org/html/2602.00776v1` |
| Queue imbalance predicts short-horizon price moves | `https://arxiv.org/abs/1512.03492` |
| Order-book imbalance in HftBacktest examples | `https://hftbacktest.readthedocs.io/en/latest/tutorials/Market%20Making%20with%20Alpha%20-%20Order%20Book%20Imbalance.html` |
| RL for execution | `https://www.cis.upenn.edu/~mkearns/papers/rlexec.pdf` |
| GPU economics for deep learning research | `https://www.quantstart.com/articles/should-you-buy-or-rent-a-gpu-based-deep-learning-machine-for-quant-trading-research/` |
| Sharpe ratio performance measurement | `https://www.quantstart.com/articles/Sharpe-Ratio-for-Algorithmic-Trading-Performance-Measurement/` |
| Market regime detection | `https://www.quantstart.com/articles/market-regime-detection-using-hidden-markov-models-in-qstrader/` |

These receipts are not a substitute for hft3 vault ontology. They are implementation references for this PDF-derived work.

## Implementation Phases

### Phase 0 - Branch And Plan Receipt

Branch: `codex/pipeline-improvement-pdf`

Worktree: `C:\Users\MSI\repos\hft3-pipeline-improvement-pdf`

Deliverables:

- This plan document.
- A commit containing only this plan.

Gate:

- `git diff --check`
- staged files contain no graphify artifacts.

### Phase 1 - Integrate Existing Advanced-Models Work

Goal: bring the already-created advanced autoresearch implementation into this branch rather than reimplementing it blindly.

Candidate source branch:

- `codex/advanced-models-autoresearch`
- latest observed head for this port: `5b7a6904`

Expected incoming files and areas:

- `packages/features_engine/feature_sets.py`
- `packages/research_pipeline/parameter_search.py`
- `packages/research_pipeline/rl_agents.py`
- updates to `scripts/run_pipeline.py`
- updates to `packages/research_pipeline/evaluation.py`
- updates to `packages/research_pipeline/document_ingestion.py`
- updates to `packages/research_pipeline/hypothesis_parser.py`
- updates to `packages/features_engine/config/model_registry.yaml`
- tests and docs for advanced model behavior

Gate:

- Merge/cherry-pick in the isolated worktree only.
- Resolve conflicts in favor of preserving PR #14 receipt, C++ evidence, and replay-eligibility gates.
- Do not edit the advanced-models worktree.

Implementation receipt:

- Ported the model-registry metadata and symbol alias surface from the clean
  advanced worktree into
  `packages/features_engine/config/model_registry.yaml`,
  `packages/features_engine/config/symbol_aliases.yaml`, and
  `docs/model_registry.md`.
- Ported parser behavior into
  `packages/research_pipeline/hypothesis_parser.py`: natural-language model
  aliases, registry default parameter ranges, canonical CME symbol aliases,
  parser metadata, and instrument-compatibility receipts.
- Added `ParsedHypothesis.metadata` and persisted parsed metadata in pipeline
  reports.
- Wired `scripts/run_pipeline.py` and `packages/research_pipeline/idea_generation.py`
  so omitted `--symbol` derives from the parsed compatible instrument, while
  explicit symbol/model mismatches fail closed before VectorBT/HftBacktest.
- Review fixes keep mixed supported/unsupported instruments fail-closed unless
  the registry explicitly declares context compatibility; structural-only
  registry entries are not routed as primary autoresearch hypotheses; concrete
  loader variants such as `MES.v.0` compare by root while preserving the
  requested suffix; and `--idea-set` parses after static filtering so emitted
  parsed receipts match the queued ideas that generated candidates.
- Parameter search, RL artifact/cache handling, document cache, evaluation
  workers, and runtime receipts were already implemented on this branch before
  this Phase 1 completion pass.

Local safe verification receipt:

- `python -m py_compile packages\research_pipeline\types.py packages\research_pipeline\hypothesis_parser.py packages\research_pipeline\idea_generation.py scripts\run_pipeline.py tests\test_research_pipeline.py`
- Targeted parser/registry pytest: 11 passed.
- Targeted idea-generation pytest: 4 passed.
- Targeted `scripts.run_pipeline` dry-run/symbol pytest: 4 passed.
- `tests\research_pipeline\test_rl_agents.py`: 4 passed.
- `git diff --check`: line-ending warnings only.

Deferred verification:

- Dependency-complete full-file and end-to-end pipeline tests remain CHI404/CI
  readiness gates; MSI-local checks are limited to the targeted lightweight
  paths above.

Deliberate deferment:

- The advanced worktree also contains a legacy cross-event evaluator with
  Sharpe/Sortino/drawdown gates. This PR keeps those risk gates deferred to the
  VectorBT -> robustness evidence layer, matching Phase 6 below and the current
  pipeline ordering. Next command if the owner later wants that legacy diagnostic
  path: port `research_pipeline.evaluation.aggregate_evaluation_results` and
  the multi-event CLI into a separate PR that explicitly marks the metrics
  diagnostic-only until robustness evidence owns promotion.

### Phase 2 - Microstructure Feature Library

Required features from the PDF:

- `order_book_imbalance`
- `queue_imbalance`
- `order_flow_imbalance`
- `micro_price`
- `vwap_to_mid_deviation`
- `spread`
- `weighted_depth_price`

Implementation receipt:

- `packages/features_engine/feature_sets.py` implements the feature functions with PIT/trailing-window semantics and source receipts.
- `packages/research_pipeline/feature_recipe.py` recognizes those feature names in the existing `primary_fs_v1` recipe family and records `features_engine.feature_sets.MICROSTRUCTURE_FEATURE_RECEIPTS`.
- `tests/test_microstructure_feature_sets.py` and `tests/research_pipeline/test_feature_recipe.py` cover formulas, edge cases, and recipe PIT receipt propagation.

Implementation requirements:

- Inputs must be explicit snapshots or trailing windows.
- Functions return deterministic floats.
- Zero-denominator behavior must be defined and tested.
- Docstrings cite the relevant literature receipts.
- Tests use synthetic snapshots/windows.
- Feature recipes can reference these names without hidden lookahead.

Gate:

- Unit tests for all feature functions.
- Feature-recipe integration tests.

### Phase 3 - RL Training Workflow

PDF requirement: RL training should be the default path, with a debugging escape hatch.

Implementation receipt:

- `packages/research_pipeline/rl_agents.py` adds non-promotable RL policy artifacts.
- `packages/research_pipeline/rl_agents.py` adds cache receipts and validated
  same-input policy reuse for CPU artifacts.
- `scripts/run_pipeline.py` accepts `--rl-training-data`, `--rl-feature`, `--rl-device`, `--rl-required`, and `--rl-seed`.
- Enabled RL writes `rl_policy_artifact.json` before document/candidate work; blocked RL stops the run with `status=blocked_rl_training`.
- CPU is limited to small research-only tabular policy artifacts. CUDA through the normal pipeline writes a blocked GPU handoff artifact until a host, command, output directory, duration, stop rule, and passing runtime smoke receipt are named.
- Default enablement remains deferred until real training data, GPU host, and resumable command are named; the code path is fail-closed once enabled.

Implementation requirements:

- CLI accepts RL training data and feature names.
- Missing training data fails closed when RL is enabled.
- RL policy artifact records features, device, duration, and training-data receipt.
- RL feature names validate against the microstructure feature registry.
- The cache reuses a matching policy only when training data SHA256, feature
  list, trainer source hash, device, seed, and row cap match.

Device rule:

- `--rl-device cpu` is allowed for small mocked/unit runs.
- `--rl-device cuda` is allowed only on a GPU host.
- Deep RL or any multi-hour GPU training must be delegated to a separate GPU-training sub-agent once real training data and a runnable command exist.

Sub-agent trigger:

Spawn the GPU-training sub-agent only after all are true:

- training-data path exists and is hashable
- feature list is validated
- run config has a bounded output directory
- the command is resumable or writes progress
- expected duration and GPU host are named

Gate:

- Unit tests mock training and require policy artifact metadata.
- No full GPU training in local CI or on MSI. A bounded MSI CUDA smoke is allowed only when the operator explicitly approves it and it writes a non-promotable readiness/smoke receipt.

### Phase 4 - Advanced Parameter Search

Required methods:

- `bayesian`
- `evolutionary`

Implementation receipt:

- `packages/research_pipeline/parameter_search.py` adds deterministic stdlib-backed `grid`, `bayesian`, and `evolutionary` candidate selectors.
- `scripts/run_pipeline.py` records effective `candidate_search.method` and `candidate_search.seed` in `pipeline_runtime_config.json`.
- The methods run before VectorBT only and record `objective_evaluations=0`; they do not promote candidates or bypass downstream gates.

Implementation requirements:

- No silent fallback to `seeded` for requested advanced methods.
- If optional third-party optimizers are unavailable, use a deterministic stdlib fallback and record `backend=stdlib`.
- Search metadata records method, iterations, seed, and explored parameter count.
- Tests verify each method returns non-empty candidates for non-trivial ranges.

Gate:

- Parameter-search unit tests.
- Pipeline tests proving requested methods are honored.

### Phase 5 - Parallel Evaluation And Caching

Implementation receipt:

- Document ingestion cache was already present from the runtime upgrade.
- `scripts/run_pipeline.py` now records `evaluation.workers`, `evaluation.worker_policy`, and uses a bounded `ProcessPoolExecutor` for the legacy candidate evaluation loop when effective workers > 1.
- Default workers remain `1`; MSI is capped by `evaluation.msi_max_workers`, while CHI404/Vast-style hosts are capped by `evaluation.max_workers`.
- VectorBT paid-screen and HftBacktest campaign worker controls remain the canonical high-throughput paths.
- RL CPU artifact cache reuse is implemented under
  `runtime/research_pipeline/rl_policy_cache`; same-inputs-twice tests prove
  miss then hit, and changed seed invalidates the cache key. Deep RL/GPU
  training remains deferred until a real training-data path, resumable training
  command, and GPU host are named.

Implementation requirements:

- Candidate/event evaluation can use a bounded worker count.
- Worker processes initialize expensive engines inside the worker.
- Document cache and RL cache live under ignored `runtime/cache/` or the existing runtime cache layout.
- Cache receipts include keys, hit/miss status, and invalidation inputs.

Gate:

- Integration test runs the same inputs twice and sees the second run hit cache.
- Parallel worker count is configurable and defaults conservatively.
- No heavy local worker fan-out on MSI.

### Phase 6 - Regime-Aware Gates

Implementation receipt:

- `config/research_pipeline/default_runtime.json` and `scripts/run_pipeline.py` now define `normal`, `high_volatility`, and `low_volatility` legacy evaluation gate profiles.
- CLI overrides exist for profile, min net PnL, min trades, max tail loss, and min win rate.
- Model-registry `volatility_regime` metadata can select the default legacy
  profile when no CLI `--gate-profile` override is supplied, and the final run
  payload and runtime receipt record a per-candidate `gate_profile_plan`.
- The current gate profile applies only to legacy `EvaluationResult` fields already produced by `evaluate_model`; Sharpe/Sortino/drawdown profile gates remain deferred to the VectorBT/robustness layers that emit those metrics.

Implementation requirements:

- Config file for gate profiles, for example `config/autoresearch/gate_thresholds.yaml`.
- Profiles include high, normal, and low volatility defaults.
- Model registry volatility regime selects the default profile when declared;
  explicit CLI profile/threshold overrides remain authoritative.
- CLI overrides are explicit and receipt-backed.

Gate:

- Tests reject candidates under the selected legacy profile using the metrics emitted by `EvaluationResult`: net PnL, trade count, tail loss, and win rate.
- Sharpe, Sortino, and drawdown profile gates remain owned by VectorBT/robustness layers that emit those metrics, not by this legacy evaluation slice.

### Phase 7 - Docs, Review, And PR Gate

Documentation updates:

- `docs/research/AUTORESEARCH_PIPELINE.md`
- relevant model-registry or feature docs
- this plan updated if implementation order changes

Review gates:

- local preflight `rg` loop for stale fallback language, graph artifacts, and missing receipts
- reviewer sub-agent pass
- CHI404 bounded tests
- plan-drift review against this document
- review surface gate
- external PR AI review loop to clean status

## Plan-Drift Checklist

Before claiming completion:

- The code implements or deliberately defers each PDF phase above.
- Any deferment names a blocker and next command.
- The advanced-models worktree remains untouched.
- GPU training was not started locally.
- If GPU training became runnable, a separate GPU-training sub-agent was spawned with the exact host, command, data hashes, and output path.
- Graphify artifacts are not staged.
