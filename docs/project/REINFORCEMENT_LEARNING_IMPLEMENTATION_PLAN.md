# MANDATORY ONTOLOGY GATE: Before using this document, operate from the Obsidian vault ontology and the provided mathematics/quantitative-finance/HFT PDFs; do not invent RL, microstructure, VectorBT, HftBacktest, GPU-training, or promotion methodology outside that authority.

# Reinforcement Learning Implementation Plan

Status: roadmap captured from operator-supplied RL developer instructions.
Created: 2026-06-25.

Source attachment:
`C:\Users\MSI\.codex\attachments\bf4417bc-bcad-4fa6-9826-a6caeb2af7c9\pasted-text.txt`

Source receipt:

```text
bytes: 9961
lines: 107
sha256: 7E88A7CCC848B6E2643DDBF55CAE9C772E23DE437B073738D648ACD9FD0AAAE9
known_truncation: source ends at "Gould & Bonart (2015) -"; do not invent the missing citation tail.
```

## Purpose

Turn the supplied reinforcement learning instructions into the hft3 roadmap for
starting the RL research module.

RL is a first-class research process for intraday trading and execution policy
learning on limit-order-book microstructure data. It is not a live execution
path and it is not a promotion shortcut. RL output remains non-promotable until
the normal hft3 gates pass: point-in-time data proof, VectorBT screening,
robustness evidence, and HftBacktest execution-realism validation.

## Authority Receipts

| Area | Receipt | Binding consequence |
|---|---|---|
| Operator roadmap | Attachment above | Defines the desired RL data, state, action, reward, training, caching, and monitoring roadmap. |
| Current pipeline order | `docs/research/AUTORESEARCH_PIPELINE.md`, `docs/project/VECTORBT_SCREENING_ENGINE_SPEC.md`, `docs/project/AUTORESEARCH_PIPELINE_UPGRADE_PLAN.md` | Do not recreate the pipeline; RL plugs into the existing research pipeline and cannot bypass VectorBT/HftBacktest. |
| Planning standard | `docs/project/PROJECT_PLANNING_STANDARD.md` | Every RL feature needs thesis, data requirement, PIT rule, tests, acceptance gate, and rejection rule. |
| Feature matrix | `docs/project/FEATURE_LITERATURE_TRACEABILITY_MATRIX.md` F001, F004, F006, F010 | RL is advanced research infrastructure; it stays gated by robustness and ontology control. |
| Vault operating state | Vault `wiki/hot.md`, `Home.md`, `Memory Stack.md` | Graph gates are `waived-by-owner-2026-06-16`; use VaultGate plus targeted source reads. |
| ML/RL microstructure basis | Vault `library/papers/kearns-nevmyvaka-2013-ml-microstructure-hft.md`; vault `library/09 ML and Deep Learning for LOB.md`; vault `references/Learning Resources.md` | Start with economically interpretable execution/control states, compare against costs, and validate out of sample. |
| Current RL code baseline | `packages/research_pipeline/rl_agents.py`; `tests/research_pipeline/test_rl_agents.py` | Existing CPU smoke artifacts are fail-closed, timestamp-audited, cached, and non-promotable; extend them rather than replacing them blindly. |

## Current Baseline On Main

The merged repo already contains a narrow RL baseline:

- `packages/research_pipeline/rl_agents.py`
- `tests/research_pipeline/test_rl_agents.py`

Implemented today:

- CPU-only tabular Q-learning smoke artifact.
- CUDA request returns a blocked GPU-handoff artifact instead of training on MSI.
- Feature validation against `features_engine.feature_sets.MICROSTRUCTURE_FEATURE_RECEIPTS`.
- Timestamp chronology audit with fail-closed default.
- Mixed reward-column rejection.
- Training-data SHA256 receipt and cache-key invalidation.
- Non-promotable artifact status: `blocked_downstream_validation_required`.

This baseline is not the final RL module. It is the safe starting point.

## Controlled Interpretation Of The Attachment

The attachment says RL should run for every hypothesis and event. In hft3, that
means every in-scope hypothesis/event must emit an RL status artifact:

```text
trained_research_only
blocked_missing_training_data
blocked_missing_features
blocked_gpu_training_required
blocked_downstream_validation_required
```

It does not mean MSI-local deep RL training, silent skipping, live execution, or
promotion without normal gates.

The attachment references a performance-improvement claim for RL execution.
That claim may guide research priority, but it must not become an hft3
acceptance threshold until the exact paper receipt is verified locally and the
metric definition is reproduced.

## RL Feature Record

Feature ID: RL001

Feature name: Reinforcement learning execution and intraday policy research.

Classification: PARTIALLY_SUPPORTED.

Source / origin: operator-supplied RL implementation attachment, current
`rl_agents.py`, vault ML/microstructure library.

End-goal connection: tests whether learned execution or timing policies improve
candidate behavior under event, context-uplift, and continuous intraday clocks.

Feature thesis: RL may learn state-dependent action policies from L2 order-book
state, trade flow, inventory, and horizon variables, but only if data is
point-in-time, rewards include costs, and validation prevents overfit.

Problem it solves: static rule templates cannot adapt execution/timing behavior
to changing queue, spread, trade-flow, inventory, and horizon state.

Required system behavior:

- Build PIT training rows from L2 order-book snapshots and trailing trade-flow
  windows.
- Construct state tensors from selected microstructure features, inventory, and
  remaining time.
- Train or block with an explicit artifact for every in-scope hypothesis/event.
- Cache exact-input policy artifacts.
- Produce candidate policy references only after artifact validation.
- Keep RL candidates subject to the same VectorBT, robustness, and HftBacktest
  gates as all other candidates.

Inputs:

- Timestamped L2 snapshots.
- Timestamped trades or trailing trade-flow bins.
- Event metadata with `event_id`, symbol, event window, and research clock.
- Cost model and inventory/risk limits.
- Optional labelled reward baseline from Workbench/HftBacktest or a declared
  simple strategy.

Outputs:

- `rl_policy_artifact.json`.
- `rl_training.log`.
- Optional checkpoints for resumable GPU training.
- Optional RL candidate reference with `model_id=RL_EXECUTION`.

Point-in-time / leakage requirements:

- Snapshot timestamp must be monotonic.
- Trade-flow features use trailing windows only.
- Reward is computed after the decision timestamp and cannot be included in the
  feature vector.
- Train/validation split must be chronological or walk-forward, never random
  across future event periods.
- Synthetic tests may use missing timestamps only when the artifact says
  `missing_timestamps_allowed=true`.

Rejection rule:

- Reject or block any RL artifact that lacks timestamp proof, mixes reward
  semantics, includes label-like feature names, uses missing data without an
  explicit blocker, or tries to promote before downstream gates.

## Data Contract

The production RL dataset must be columnar when practical. JSON/JSONL remains
allowed only for small smoke tests and fixtures.

### Order Book Snapshots

Required columns:

```text
event_id
symbol
timestamp_ns
best_bid_price
best_ask_price
bid_price_1 ... bid_price_N
bid_size_1 ... bid_size_N
ask_price_1 ... ask_price_N
ask_size_1 ... ask_size_N
```

Optional columns:

```text
hidden_bid_size_i
hidden_ask_size_i
iceberg_bid_size_i
iceberg_ask_size_i
```

If hidden or iceberg depth is unavailable, the prepared dataset must explicitly
set it to zero or mark the field absent in the schema receipt.

### Trades

Required columns:

```text
event_id
symbol
timestamp_ns
price
size
side
```

Trade rows may be aggregated into trailing bins only when the bin width and
right-open/closed boundary are recorded. The attachment suggests 50 ms bins as
a practical option; hft3 must record the chosen bin width and test that no
future trades enter a decision row.

### Event Metadata

Required fields:

```text
event_id
research_clock
symbol
episode_start_ns
episode_end_ns
target_event_timestamp_ns_or_null
source_manifest_hash
```

## Feature Contract

The attachment requires these first-pass features:

| Feature | Formula / rule | Current source |
|---|---|---|
| Order-book imbalance | `(bid_vol - ask_vol) / (bid_vol + ask_vol)` | `features_engine.feature_sets` |
| Queue imbalance | `(q_bid - q_ask) / (q_bid + q_ask)` | `features_engine.feature_sets` |
| Order-flow imbalance | `(buy_volume - sell_volume) / (buy_volume + sell_volume)` over trailing trades | `features_engine.feature_sets` |
| Micro-price | `(best_bid_price * ask_vol + best_ask_price * bid_vol) / (bid_vol + ask_vol)` | `features_engine.feature_sets` |
| VWAP-to-mid deviation | `(vwap - midpoint) / midpoint` over trailing trades | `features_engine.feature_sets` |
| Spread | `best_ask_price - best_bid_price`; relative spread optional | `features_engine.feature_sets` |
| Weighted depth price / VAMP | weighted depth price across top N levels | `features_engine.feature_sets` |

Normalization is required for deep RL and must be receipt-backed:

```text
normalizer_type=zscore|minmax|identity
fit_window
fit_data_hash
fit_start_ns
fit_end_ns
feature_names
```

The normalizer cannot fit on validation, holdout, or future episode rows.

## Environment Contract

### State

Each state at decision time `t` contains:

```text
selected microstructure feature vector at t
optional trailing lag stack
inventory position normalized by max_position
remaining time to episode end normalized to [0, 1]
```

Future extension may add volatility regime, latency state, and context-feature
families, but only after those features carry PIT usage proof.

### Actions

Initial discrete action space:

```text
hold
enter_or_increase_long_limit
enter_or_increase_short_limit
flatten
```

The current CPU smoke artifact uses:

```text
hold
enter_long
enter_short
```

Roadmap work must add `flatten` only when the environment tracks inventory and
transaction costs.

### Reward

Required reward shape:

```text
reward_t =
  (current_pnl_t - current_pnl_t_minus_1)
  - cost_per_contract * abs(delta_position)
  - inventory_risk_penalty
  - optional_drawdown_penalty
```

Reward artifacts must record:

```text
reward_schema_version
cost_per_contract
slippage_model_id
risk_penalty_type
risk_penalty_parameters
drawdown_penalty_or_null
reward_units
```

### Episode

Scheduled-event episode default:

```text
start: configured minutes before target event
end: configured minutes after target event, with first-hour default allowed
reset: inventory and open-order state reset at episode start
```

Continuous episode default:

```text
fixed rolling window, initially 30 minutes unless overridden by config
```

Each artifact must state which episode rule was used.

## Training Roadmap

### Phase RL-0 - Plan Receipt

Deliverable:

- This plan document.

Gate:

- `git diff --check`.
- Local preflight confirms the new doc is the only intended change.

### Phase RL-1 - Data Schema And Preparation

Goal:

- Add a documented RL training data schema and a preparation command that can
  turn raw L2 snapshots and trades into PIT training rows.

Deliverables:

- `docs/project/REINFORCEMENT_LEARNING_DATA_SPEC.md` or an appendix added to
  this plan.
- Preparation script or command path.
- Unit tests for schema validation and trailing-window leakage rejection.

Gate:

- Missing snapshot/trade/event fields fail closed.
- Non-monotonic timestamps fail.
- Trailing trade windows prove they exclude future trades.

### Phase RL-2 - Environment Builder

Goal:

- Extend `rl_agents.py` or a small helper module with state construction,
  action definitions, reward computation, and episode reset logic.

Deliverables:

- State builder from rows plus feature list.
- Reward function with explicit cost and risk parameters.
- Environment transition function for CPU tests.

Gate:

- Unit tests cover hold, long, short, flatten, transaction cost, inventory
  penalty, and episode reset.
- Feature and reward fields remain separated.

### Phase RL-3 - Mandatory Artifact Invocation

Goal:

- Make the pipeline emit an RL status artifact for each in-scope
  hypothesis/event before candidate generation, without requiring MSI-local
  deep training.

Deliverables:

- Runtime config for RL defaults, such as `config/autoresearch/rl.yaml` or
  equivalent `config/research_pipeline/default_runtime.json` section.
- Pipeline integration that writes either a trained artifact or a blocked
  artifact.

Gate:

- Missing training data produces a blocker artifact, not a silent skip.
- Existing explicit debug escape hatch must be named and receipt-backed.
- No GPU process starts on MSI.

### Phase RL-4 - CPU Research Trainer

Goal:

- Upgrade the current tabular CPU smoke path into a reproducible research
  baseline for small slices.

Deliverables:

- Chronological train/validation split.
- Cumulative reward, drawdown, and Sharpe-like diagnostics on train and
  validation.
- Cache receipts keyed by data hash, feature list, hyperparameters, algorithm,
  trainer source hash, and normalizer hash.

Gate:

- Same inputs produce a cache hit.
- Changed seed, feature list, data hash, or normalizer hash invalidates cache.
- Validation metrics are diagnostic only and cannot promote.

### Phase RL-5 - Deep RL GPU Handoff

Goal:

- Add a GPU-host training path for PPO first; A3C remains a later option unless
  there is a specific reason to require asynchronous workers.

Deliverables:

- GPU training command that is resumable, writes checkpoints, and records
  progress.
- Separate GPU-training sub-agent/run receipt when real data and host are
  named.
- Checkpoints with optimizer state, model weights, config hash, and data hash.

Gate:

- The training-data path exists and is hashable.
- Feature list is validated.
- Output directory is bounded.
- Expected runtime, GPU host, and stop rule are named.
- No local MSI GPU/CPU deep training is launched.

### Phase RL-6 - RL Candidate Mapping

Goal:

- Convert trained policy artifacts into candidate references without treating
  the policy as validated alpha.

Deliverables:

- Candidate metadata for `model_id=RL_EXECUTION`.
- Policy artifact hash and path in candidate metadata.
- Explicit transaction-cost and execution assumptions.

Gate:

- RL candidate cannot be screen-passed unless VectorBT evaluates it under the
  declared assumptions.
- RL candidate cannot be replay-eligible without robustness evidence.
- RL candidate cannot claim execution realism without HftBacktest pass.

### Phase RL-7 - Validation, Ablations, And Monitoring

Goal:

- Measure whether RL improves over baselines and which features matter.

Deliverables:

- Target-only baseline vs RL policy result.
- Feature ablations such as removing queue imbalance or OFI.
- Train/validation reward curves.
- Drawdown and cost diagnostics.
- `artifact_dir/rl_training.log`.

Gate:

- Validation stagnation or overfit produces a rejection reason.
- Ablation result is recorded; unsupported feature claims are rejected.
- Cockpit/backend state shows blocked, trained-research-only, or validated
  downstream state honestly.

## Configuration Roadmap

Initial config surface:

```yaml
enabled: true
required: true
training_data: null
features:
  - order_book_imbalance
  - order_flow_imbalance
  - spread
device: cpu
algorithm: tabular_q_learning_research_cpu_smoke
episodes: 1000
max_steps_per_episode: 1000
batch_size: 256
learning_rate: 0.0003
discount_factor: 0.99
checkpoint_every_episodes: 50
cache_enabled: true
cache_root: runtime/research_pipeline/rl_policy_cache
```

Defaults must be conservative. If `required=true` and `training_data=null`, the
pipeline must emit a blocked artifact instead of skipping RL.

## Verification Plan

MSI-safe checks:

```text
python -m py_compile packages/research_pipeline/rl_agents.py tests/research_pipeline/test_rl_agents.py
python -m pytest -q tests/research_pipeline/test_rl_agents.py
git diff --check
```

CHI404 checks once data/environment work begins:

```text
PYTHONPATH='.:packages:apps:tests/backtest_pipeline' python3 -m pytest -q \
  tests/research_pipeline/test_rl_agents.py \
  tests/test_microstructure_feature_sets.py
```

GPU/Vast checks are blocked until a real dataset, host, command, expected
duration, stop rule, and resumable output path are named. The executable
preflight receipt is `rl_gpu_training_readiness_artifact`; it does not train,
promote, or bypass VectorBT, robustness, or HftBacktest gates. MSI may run only
a bounded CUDA smoke when the operator explicitly approves it. The smoke command
is `python scripts/run_rl_gpu_smoke.py --training-data <rows.jsonl> --feature
<name> --output-dir <runtime-dir> --steps 1`; it writes a non-promotable
`rl_gpu_smoke_artifact.json` and checkpoint receipt.

## Start Here

The next coding step is Phase RL-1:

1. Define the RL training-data schema.
2. Add validation for snapshots, trades, event metadata, and trailing-window
   no-leakage rules.
3. Keep current `rl_agents.py` CPU smoke behavior intact.
4. Do not start deep RL training until Phase RL-5 gates are satisfied.

## Known Open Questions

| Question | Current status |
|---|---|
| Exact source for the attachment's `Gould & Bonart (2015)` reference | Blocked by truncated attachment; do not cite beyond the visible text. |
| Exact numeric RL performance-improvement threshold | Do not encode until the underlying paper and metric are verified. |
| Production RL data format | Columnar preferred; JSON/JSONL remains fixture-only until a data-prep path exists. |
| PPO dependency choice | Defer until GPU host and installed dependencies are known; avoid adding dependencies prematurely. |
| Whether RL runs on every event as trained policy or blocked artifact | Current roadmap says mandatory status artifact for every in-scope hypothesis/event; training runs only when data and host gates are satisfied. |
