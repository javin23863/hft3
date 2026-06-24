# Pipeline Improvement PDF Implementation Plan

Status: execution plan from operator-supplied PDF, created 2026-06-25.

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
- Do not bypass VectorBT -> robustness evidence -> HftBacktest ordering.
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
- latest observed head: `2b8d7644`

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

### Phase 2 - Microstructure Feature Library

Required features from the PDF:

- `order_book_imbalance`
- `queue_imbalance`
- `order_flow_imbalance`
- `micro_price`
- `vwap_to_mid_deviation`
- `spread`
- `weighted_depth_price`

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

Implementation requirements:

- CLI accepts RL training data and feature names.
- Missing training data fails closed when RL is enabled.
- RL policy artifact records features, device, duration, and training-data receipt.
- RL feature names validate against the microstructure feature registry.
- A cache may reuse a matching policy only when training data, feature list, code/config receipt, and device policy match.

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
- No real GPU training in local CI or on MSI.

### Phase 4 - Advanced Parameter Search

Required methods:

- `bayesian`
- `evolutionary`

Implementation requirements:

- No silent fallback to `seeded` for requested advanced methods.
- If optional third-party optimizers are unavailable, use a deterministic stdlib fallback and record `backend=stdlib`.
- Search metadata records method, iterations, seed, and explored parameter count.
- Tests verify each method returns non-empty candidates for non-trivial ranges.

Gate:

- Parameter-search unit tests.
- Pipeline tests proving requested methods are honored.

### Phase 5 - Parallel Evaluation And Caching

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

Implementation requirements:

- Config file for gate profiles, for example `config/autoresearch/gate_thresholds.yaml`.
- Profiles include high, normal, and low volatility defaults.
- Model registry volatility regime selects the default profile.
- CLI overrides are explicit and receipt-backed.

Gate:

- Tests reject candidates with insufficient Sharpe, insufficient Sortino, or excessive drawdown under the selected profile.

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
