# MANDATORY ONTOLOGY GATE: Before using this plan, operate from the Obsidian vault ontology and the provided mathematics/quantitative-finance/HFT PDFs; do not invent project requirements outside that authority.

# Autoresearch Advanced Models Implementation Plan

Status: historical / superseded for active HftBacktest-only routing
Date: 2026-06-24
Branch: `codex/advanced-models-autoresearch`
Worktree: `C:\Users\MSI\repos\hft3-advanced-models`

Implementation receipt on 2026-06-24: Slices 1-6 are implemented in this
worktree at research-pipeline scope. Parser/registry/symbol aliases, parameter
search, cross-event aggregation, microstructure formulas, mandatory RL research
artifacts, and docs are present with local tests. RL remains promotion-blocked
until downstream validation gates are wired. Reviewer hardening added on
2026-06-24: hybrid candidate expansion defaults on with `--no-hybrid` escape,
registry `holding_bars` feeds `holding_period_bars`, multi-event packet output
keeps primary `event_id` plus full `event_ids`, VectorBT/HftBacktest rejects
multi-event screening until per-event evidence is wired, cross-event risk fields
are non-gateable diagnostics unless timestamped equity metrics exist, and deploy
is fail-closed unless at least one result passes all gates. Additional review
fixes: autoresearch rejects multi-event input until an event-set contract exists,
parser symbol extraction no longer seeds MES when another symbol is present, CLI
target symbols derive from parsed compatible instruments or fail closed on
mismatch, microstructure snapshots reject non-finite levels, and RL artifacts
mark eval metrics as chronology-audited only when monotonic timestamps are
available.

## Source And Authority

This plan saves and controls the developer brief pasted into Codex attachment
`C:\Users\MSI\.codex\attachments\3bfc86da-366b-422b-aea1-29d751de2c2b\pasted-text.txt`.
The pasted source ends mid-sentence at `via fea`; keep that truncation visible
instead of filling in invented missing text.

Owner correction on 2026-06-24: reinforcement learning is not optional. RL must
be implemented as a testable process behind explicit pipeline controls, with the
same review, verification, artifact, and failure gates as the rest of the
autoresearch system.

This plan is subordinate to the canonical authorities:

- `docs/project/OPPORTUNITY_RESEARCH_SPEC.md`
- `docs/project/VECTORBT_SCREENING_ENGINE_SPEC.md`
- `docs/cockpit/MACRO_CONTEXT_VIX_OPTIONS_CHECKLIST.md`
- `docs/project/FEATURE_LITERATURE_TRACEABILITY_MATRIX.md`
- Vault `wiki/hot.md`, `Home.md`, `Memory Stack.md`, `pipelines/Autoresearch Pipeline.md`, and `pipelines/Feature Families Pipeline.md`
- Repo `AGENTS.md`, `docs/VALIDATION_HONESTY.md`, `docs/REVIEWER_CHARTER.md`, and `docs/ai/GREPLOOP.md`

Graph gates are owner-waived: `waived-by-owner-2026-06-16`. Do not run
GraphGate, GraphPre, GraphPost, `graphify query`, `graphify update`, or graph
rebuild scripts for this plan. Use VaultGate and targeted source reads.

## Implementation Contract

The implementation must preserve these hft3 constraints:

- No lookahead: all features, parser-derived ranges, model metadata, and RL
  training/evaluation artifacts must state their decision-time information
  boundary.
- Deterministic search first: LLM output may propose structured fields, but
  deterministic code clamps, validates, and records parameter spaces before
  evaluation.
- Feature-plane honesty: do not claim full-product evidence unless artifacts
  prove point-in-time model consumption of admitted feature families.
- VectorBT is screening evidence only; HftBacktest plus hft3 native hot-path
  evidence is required for execution-realism claims.
- RL is mandatory for this plan, but it starts as research-pipeline logic, not
  live execution logic. It must emit explicit process/artifact status and remain
  blocked from promotion until validation gates pass.

## Review Gates

Every implementation slice follows:

```text
Fable -> Ponytail -> VaultGate -> Spec -> Plan -> Code -> Local Preflight -> Review -> Verify -> Plan Drift Review -> Review Surface Gate -> PR GrepLoop -> GraphPost waived
```

Required receipts per slice:

- VaultGate stamp and relevant vault notes checked.
- Local preflight using task-specific `rg` patterns plus `git diff --check`.
- `cavecrew-reviewer` dual-pass receipt for code changes: Pass A Karpathy and
  Pass B math invariants.
- Scope-green verification per `docs/VALIDATION_HONESTY.md`.
- Plan drift review comparing the final diff to this plan.
- PR AI review status. Until a PR exists, report `pr-ai-review:
  unavailable(no-pr)` and `merge-ready: no`.
- Graph gate status: `graph-gate: waived-by-owner-2026-06-16`.

## Slice Plan

### Slice 0 - Plan Capture And Guardrails

Feature thesis: preserve the developer brief as a controlled repo plan and
convert optional/ambiguous items into hft3-valid gates.

End-goal connection: keeps advanced autoresearch work aligned with canonical
feature-plane, VectorBT, robustness, and validation-honesty authorities.

Implementation boundary:

- Add this plan file.
- Do not change code in Slice 0.

Acceptance gate:

- Plan file exists in the separate worktree.
- Plan explicitly marks RL as mandatory.
- Local preflight and doc verification pass.

### Slice 1 - Parser Universe, Model Aliases, And Parameter Ranges

Feature thesis: natural-language theses should resolve instrument aliases,
model aliases, and model-scoped parameter ranges before candidate generation.

End-goal connection: supports full model universe testing and deterministic
parameter-space declaration without relying on LLM free-form output.

Literature or ontology basis:

- Feature Literature Traceability Matrix F001, F002, F010.
- VectorBT Screening Engine Spec deterministic parameter-space contract.
- Vault microstructure notes for cross-asset order-book feature transfer and
  queue/OFI feature families.

Implementation boundary:

- Add `packages/features_engine/config/symbol_aliases.yaml`.
- Extend `packages/features_engine/config/model_registry.yaml` with aliases,
  default parameter ranges, recommended horizons, valid instrument universe,
  volatility regime, risk metrics, and feature recipe metadata where the
  current registry supports the model.
- Update `packages/research_pipeline/hypothesis_parser.py` to load symbol and
  model aliases, parse enriched LLM packet fields when present, and fall back
  deterministically when fields are missing or malformed.
- Keep unknown-token behavior compatible with the current parser.

Acceptance gate:

- Unit tests cover micro futures aliases, text aliases such as GOLD/WTI/10Y,
  model synonym matching such as blowout fade, fallback behavior, and
  registry-sourced parameter ranges.

### Slice 2 - Candidate Generation And Deterministic Search

Feature thesis: candidate generation should use declared model parameter ranges,
risk parameters, hybrid combinations, and bounded deterministic or seeded search
methods.

End-goal connection: supports deterministic VectorBT screening and avoids
post-hoc parameter mutation.

Implementation boundary:

- Update `packages/research_pipeline/model_generation.py`.
- Add `packages/research_pipeline/parameter_search.py`.
- Extend `scripts/run_pipeline.py` with `--search-method` and `--hybrid`.
- Prefer stdlib and installed dependencies. If Bayesian/evolutionary search
  dependencies are absent, implement deterministic seeded candidate sampling
  with explicit `method_unavailable` or `fallback_method` artifact status rather
  than adding a new dependency blindly.

Acceptance gate:

- Tests prove deterministic grid expansion, capped search budgets, hybrid size
  limits, and artifact-visible method status.

### Slice 3 - Cross-Event Evaluation And Risk Gates

Feature thesis: candidates must be evaluated across declared event sets with
risk-adjusted metrics, not only one event.

End-goal connection: supports robustness, overfit control, and honest promotion
gates.

Implementation boundary:

- Update `scripts/run_pipeline.py`, `packages/research_pipeline/evaluation.py`,
  and `packages/research_pipeline/types.py`.
- Support repeated or comma-separated `--event-id`.
- Extend gate thresholds with risk metrics such as Sharpe, Sortino, and maximum
  drawdown.
- Preserve existing single-event behavior.

Acceptance gate:

- Tests cover event parsing, per-event metrics, aggregate metrics, failing risk
  thresholds, and compatibility with existing fixtures.

### Slice 4 - Microstructure Feature Library

Feature thesis: order-book imbalance, queue imbalance, micro-price, VAMP, and
weighted-depth price should be explicit feature functions with formulas and
tests.

End-goal connection: supports feature family research with point-in-time,
literature-traceable microstructure inputs.

Implementation boundary:

- Add or extend `packages/features_engine/feature_sets.py`.
- Add feature recipes in model registry only where data requirements and
  point-in-time boundaries can be stated.
- Do not merge PDF structural models into the 64-dim `FeatureIndex` or C++ hot
  path without a separate C++ parity review.

Acceptance gate:

- Tests use synthetic depth snapshots and cover zero/empty depth, balanced
  books, one-sided books, and deterministic formula outputs.

### Slice 5 - Mandatory RL Research Process

Feature thesis: RL should be an implemented, testable research process for
execution or signal timing that consumes declared microstructure features under
strict budgets and leakage controls.

End-goal connection: supports advanced model testing while keeping LLM/RL
outputs subordinate to deterministic validation and robustness gates.

Literature or ontology basis:

- Nevmyvaka, Feng, and Kearns style execution-state factorization from the
  developer brief.
- Feature Literature Traceability Matrix F001, F006, F010.
- VectorBT Screening Engine Spec: deterministic budgets, no Gemma optimization,
  explicit artifacts, and fail-closed promotion.

Implementation boundary:

- Add `packages/research_pipeline/rl_agents.py`.
- Implement a minimal deterministic RL process, such as seeded tabular
  Q-learning over discretized feature states, action space, reward function,
  and episode budget.
- Add `--rl` pipeline control only after the process emits a structured artifact
  with training budget, seed, feature names, action space, reward definition,
  train/eval split metadata, and promotion status.
- RL output may produce candidate policy artifacts for screening; it may not
  bypass robustness, walk-forward, DSR/PBO/CSCV, VectorBT, or HftBacktest gates.

Acceptance gate:

- Tests cover deterministic training with a fixed seed, budget exhaustion,
  malformed feature input rejection, artifact schema, and no-promotion without
  downstream gates.

### Slice 6 - Documentation And Model Registry Docs

Feature thesis: new parser, registry, search, evaluation, microstructure, and RL
surfaces must be documented with authority links and failure rules.

Implementation boundary:

- Update `docs/research/AUTORESEARCH_PIPELINE.md`.
- Add `docs/model_registry.md`.
- Update `docs/project/FEATURE_LITERATURE_TRACEABILITY_MATRIX.md` if a feature
  classification, boundary, or acceptance gate changes.

Acceptance gate:

- Docs describe what is implemented versus planned, and avoid claiming
  full-product, execution-realism, or merge-ready status without evidence.

## Saved Developer Brief - Verbatim Source, Not Active Overrides

The following block preserves the pasted developer brief for traceability. Where
it says RL is optional, exploratory, or an optional file, that text is superseded
by the owner correction above and by the Slice 5 implementation receipt.

```text
Developer Brief: Advanced Models & Hypotheses for hft3 Autoresearch Pipeline
Context

PR #13 has prepared the hft3 autoresearch pipeline for further enhancements. The current hypothesis parser and candidate generator use simple heuristics: they parse a natural-language thesis to detect only a few instrument symbols (NQ and ES) and set a fixed range of signal thresholds. Candidate generation then sweeps over these thresholds and a few holding periods. While this works, it limits the flexibility of the models and does not leverage modern research on market microstructure, parameter search and risk management.

Academic studies highlight several relevant points:

Microstructure features such as order book/flow imbalance provide strong predictive power across assets. In the hftbacktest tutorial on order book imbalance, the authors note that order book (order flow) imbalance is a widely recognised microstructure indicator and describe several derivatives (micro-price, VAMP, weighted-depth price). They emphasise that such indicators can be standardised and combined.
Queue imbalance can predict the direction of the next mid-price movement. Gould & Bonart (2015) show that logistic regressions between queue imbalance and the next mid-price direction provide significant improvement in binary and probabilistic classification, compared to a null model.
There is evidence that cross-asset patterns in order-book microstructure are stable and transferable. An arXiv paper on cryptocurrency microstructure notes that the same engineered order-book and trade features exhibit similar predictive importance across assets and that feature rankings remain stable despite heterogeneous liquidity and volatility.
Market regime detection can improve trading performance by filtering out trades during high-volatility regimes. A QuantStart article on Hidden-Markov-Model (HMM) filters shows that fitting a HMM to returns data can detect low and high volatility regimes and improve Sharpe ratio by disallowing trades during high volatility.
Reinforcement learning has been successfully applied to trade execution. Nevmyvaka, Feng & Kearns (2006) demonstrate that RL can improve execution performance and exploit microstructure variables; their RL-optimised policies improved performance by up to 50% and the authors highlight that microstructure variables such as order-book state are valuable.
Risk-adjusted metrics such as the Sharpe ratio provide a more informative evaluation than raw PnL. A QuantStart article explains that the Sharpe ratio measures excess return per unit of risk and is widely used for comparing strategies, while also noting limitations and the need to account for transaction costs.

The enhancements below use these insights to guide more sophisticated hypothesis parsing, candidate generation and evaluation. Each task lists the files to modify and explains why the change is necessary. Please implement them carefully, adding unit tests and documentation where indicated.

1. Broaden Instrument Detection

Files: packages/research_pipeline/hypothesis_parser.py, packages/features_engine/config/symbol_aliases.yaml (new)

Why: The parser only recognises NQ and ES tokens; this restricts the instrument universe and ignores cross-asset microstructure patterns. Research shows that order-book features are robust across assets, so expanding the instrument set will allow the hypotheses to generalise.

Tasks:

Create a YAML file packages/features_engine/config/symbol_aliases.yaml containing a mapping from canonical instrument symbols to lists of synonyms (e.g., NQ: ["NQ", "MNQ", "NQH", ...], ES: ["ES", "MES", ...], YM: ["YM", "MYM"], CL: ["CL", "OIL", "WTI"], GC: ["GC", "GOLD"], ZN: ["ZN", "10Y"], etc.). Include micro futures and common text abbreviations. Use comments to document any ambiguous mappings.
In hypothesis_parser.py, load this YAML file once at module import. When parsing the thesis text, normalise it to upper case and iterate through the aliases to detect any instrument references. If a match is found, add the canonical symbol to the instrument_universe list. Preserve the existing behaviour for unknown tokens.
Write unit tests ensuring that phrases like "trade micro NQ futures" or "GOLD breakout" correctly add NQ or GC to the instrument universe.

Academic receipt: The cross-asset microstructure study suggests that the same order-book features apply across different assets, motivating a broader universe.

2. Model-Specific Parameter Ranges

Files: packages/features_engine/config/model_registry.yaml, packages/research_pipeline/hypothesis_parser.py

Why: The parser currently returns a fixed signal_threshold range of [0.05, 0.35] for all models. Different hypotheses (e.g., liquidity vacuum vs. continuation) and instruments require different threshold ranges, holding periods or stop-loss levels. Research on queue imbalance finds that logistic regression parameters vary by tick size and liquidity, suggesting model-dependent tuning.

Tasks:

Add a new default_param_ranges section to each model entry in model_registry.yaml. For example:

SPREAD_BLOWOUT_RECOMPRESSION:
  description: "..."
  default_param_ranges:
    signal_threshold: [0.02, 0.15]
    stop_loss: [0.05, 0.30]
    take_profit: [0.05, 0.30]
    holding_bars: [1, 10]
  valid_universe: ["ES", "NQ", "YM"]
  ...

Use narrower ranges for models that target small moves and broader ranges for models that exploit large events. Document the rationale in inline comments.

Modify hypothesis_parser.py so that after determining the model slug(s) it looks up the default_param_ranges from the registry. Set parsed.param_ranges to this dictionary instead of the hard-coded [0.05, 0.35]. If a model entry lacks this section, fall back to a sensible default.
Update the CLI or configuration loader to allow the user to override these defaults via a config file or command-line flags.
Add unit tests verifying that the parser returns the correct ranges for each model.

Academic receipt: Queue imbalance studies demonstrate that the optimal logistic regression parameters differ between large-tick and small-tick stocks, implying that each model should have its own parameter bounds.

3. Natural-Language Parsing Improvements

Files: packages/research_pipeline/hypothesis_parser.py, data_layer/llm/packet_runner.py

Why: Currently, the parser uses simple string matching and instructs the LLM to output a JSON object. This limits its ability to extract entry/exit conditions, stop-loss/take-profit hints or time horizons from the thesis. Better natural-language understanding will reduce ambiguities and produce more accurate hypotheses.

Tasks:

Refine the LLM prompt in packet_runner.py to ask for specific fields such as entry_rule, exit_rule, target_instruments, indicative_stop_loss, and expected_holding_period. Provide examples of desired output in the prompt to guide the model.
In hypothesis_parser.py, parse these new fields into ParsedHypothesis. Map common terms (e.g., "fade", "mean reversion", "continuation", "breakout") to known model slugs. Use regex patterns or a small rule-based system to fall back when the LLM fails to parse.
Handle errors gracefully: if the LLM returns malformed JSON or lacks certain keys, log a warning and use default values.
Add a small training corpus (in a new data/nlp_training_examples.json) containing examples of theses and their corresponding parsed outputs. This can be used to finetune or evaluate the parser offline.

Academic receipt: The microstructure literature emphasises that order-flow indicators (e.g., micro-price, VAMP, weighted-depth price) require context such as reference levels and time horizons. Extracting such context from the thesis will improve model specification.

4. Support Model Aliases and Synonyms

Files: packages/features_engine/config/model_registry.yaml, packages/research_pipeline/hypothesis_parser.py

Why: Traders often refer to strategies by informal names (e.g., "blowout fade" vs. SPREAD_BLOWOUT_RECOMPRESSION). The parser only matches canonical slugs, leading to mismatches or unknown models.

Tasks:

Add an aliases field to each model in model_registry.yaml, listing known synonyms or shorthand. Example:

SPREAD_BLOWOUT_RECOMPRESSION:
  aliases: ["spread blowout", "blowout fade", "squeeze fade"]
  ...
Extend the model-matching function in hypothesis_parser.py to check the thesis text against both the slug and any alias (case-insensitive). If multiple models match, select the most specific or prompt the LLM for disambiguation.
Add tests ensuring that a thesis mentioning "blowout fade" maps to the correct model.

Academic receipt: The queue imbalance paper notes that logistic regression models are more accurate when features are correctly specified; alias recognition ensures the correct model is chosen based on the trader's terminology.

5. Enrich Model Registry Metadata

File: packages/features_engine/config/model_registry.yaml

Why: The registry currently holds only the model name and description. To guide candidate generation and evaluation, we need metadata such as recommended time horizon, valid instruments and volatility regime.

Tasks:

Add the following optional fields to each model entry:
recommended_horizon_bars: typical holding period in bars (e.g., 1-5 for scalping strategies, 30-60 for swing strategies).
valid_instrument_universe: list of symbols where the model is known to perform well.
volatility_regime: e.g., high_volatility, low_volatility, or any. Use this to filter candidates based on the current regime.
risk_metrics: a dict specifying which risk metrics (e.g., Sharpe ratio, drawdown) are most relevant.
Document these fields in docs/research/AUTORESEARCH_PIPELINE.md and update the parser to read them into ParsedHypothesis.

Academic receipt: The HMM regime filter article demonstrates that filtering trades during high-volatility regimes improves Sharpe ratio. Storing volatility regime metadata allows the pipeline to apply appropriate filters.

6. Enhance Candidate Generation and Search

Files: packages/research_pipeline/model_generation.py, packages/research_pipeline/parameter_search.py (new), scripts/run_pipeline.py

Why: Candidate generation currently sweeps a fixed grid of thresholds and holding periods. Research suggests using dynamic and intelligent search methods: queue-imbalance parameters vary by instrument; RL approaches demonstrate that learning policies can improve performance.

Tasks:

Include risk-management parameters: Modify model_generation.py to always include stop_loss and take_profit in the grid (not only when the vectorbt expansion flag is set). Use the ranges from default_param_ranges (see task 2).
Hybrid strategies: Add a --hybrid flag to combine multiple hypotheses. When set, generate candidates by summing or averaging the signals from two or more models. Create weighting parameters (e.g., 0.5/0.5) and include them in the candidate specification. Limit the combination to 2-3 models to avoid combinatorial explosion.
Advanced parameter search: Implement a new module parameter_search.py with a function search_parameters(model_id, param_ranges, eval_function, method="bayes", n_iter=20). Use Bayesian optimisation (e.g., with scikit-optimize or optuna) to sample parameter combinations and call eval_function to evaluate each candidate. Provide grid as a baseline method.
Modify scripts/run_pipeline.py to accept a --search-method argument (grid, bayes, evolutionary). Use the selected method to generate candidates. Document this in the CLI help.
Add tests for the new search module and hybrid generation.

Academic receipt: The RL execution paper reports that RL policies can significantly improve trade execution; combining models and using intelligent search can likewise improve candidate quality. Queue-imbalance research shows that parameter choice matters.

7. Cross-Event Evaluation and Risk-Adjusted Gating

Files: scripts/run_pipeline.py, packages/research_pipeline/evaluation.py, packages/research_pipeline/types.py

Why: Evaluating candidates on a single event may overfit to that event. Cross-asset patterns in microstructure are stable, so testing across similar events improves robustness. Risk metrics such as the Sharpe ratio help compare strategies.

Tasks:

Modify the CLI to accept multiple --event-id arguments (e.g., comma-separated or repeated flags). Pass this list to the evaluation module.
In evaluation.py, adapt evaluate_model() to loop over all events. Compute performance metrics (PnL, trade count, Sharpe ratio, drawdown) for each event and aggregate them (e.g., mean, median, worst). Add these aggregated metrics to EvaluationResult.
In types.py, extend GateThresholds to include risk thresholds such as min_sharpe_ratio, max_drawdown, min_sortino_ratio. Update gating logic so a candidate must pass all thresholds on each event or aggregated metrics to be accepted.
Provide a configuration file (or CLI flags) to specify risk thresholds. Document recommended values.
Add tests verifying that cross-event evaluation works and that risk metrics are computed correctly.

Academic receipt: The Sharpe ratio is widely used for risk-adjusted evaluation, and the HMM regime filter improved Sharpe ratio by removing trades in volatile regimes. Cross-event testing ensures that the order-book features stable across assets lead to robust models.

8. Risk-Aware Microstructure Features and Predictive Models

Files: packages/features_engine/feature_sets.py (new or modified), packages/research_pipeline/model_generation.py

Why: Current hypotheses rely on simple order-flow signals. Research indicates that richer microstructure features (order book imbalance, queue imbalance, micro-price, volume-adjusted price) can predict short-term price movements. Incorporating these features and optionally predictive models (e.g., RL) will produce more informed signals.

Tasks:

Feature library: Create or extend feature_sets.py to define functions computing microstructure features:
order_book_imbalance(depth_snapshot): compute static order book imbalance or its standardised versions.
queue_imbalance(depth_snapshot): compute bid/ask queue imbalance based on level-2 order book; reference the logistic regression analysis that shows predictive power.
micro_price(depth_snapshot), VAMP, weighted_depth_price as described in hftbacktest.
Document each feature with formulas and references.
Signal templates: For each hypothesis in model_registry.yaml, add a feature_recipe that defines how to combine features (e.g., signal = order_book_imbalance - threshold). Use this to generate candidate functions in model_generation.py instead of hard-coded logic.
Reinforcement learning (optional/exploratory): Provide an experimental path (behind a flag) to train RL agents for execution or signal timing. Create a rl_agents.py module with a function train_rl_agent(data, feature_names, reward_function). Use the approach in Nevmyvaka et al. (2006) to factorise the state space and include microstructure variables. Allow the pipeline to call this function when the --rl flag is set, passing historical microstructure data and collecting the trained policy.
Add tests verifying that feature functions return expected values on synthetic depth data.

Academic receipt: The hftbacktest tutorial emphasises multiple derivatives of order book imbalance and micro-price, and the queue imbalance study confirms that queue imbalance predicts price moves. Reinforcement learning has been shown to improve execution performance using microstructure data.

Note: the owner correction above supersedes the source brief's "optional/exploratory" wording for RL.

9. Documentation and Testing

Files: docs/research/AUTORESEARCH_PIPELINE.md, docs/model_registry.md (new), packages/research_pipeline/tests/

Why: The introduction of new parameters, features and search methods increases complexity. Comprehensive documentation and tests will help maintain the codebase and ease onboarding for future developers.

Tasks:

Update AUTORESEARCH_PIPELINE.md to describe:
The extended instrument detection and alias resolution.
The new default_param_ranges, recommended_horizon_bars, valid_instrument_universe, volatility_regime fields in model_registry.yaml.
The hybrid candidate generation and advanced parameter search options.
Cross-event evaluation and risk metrics.
The microstructure feature library and RL options.
Create docs/model_registry.md to document each model, including its description, default parameter ranges, valid instruments, recommended horizon, volatility regime and aliases. Provide examples of parsed hypotheses.
Add unit tests for every new function, including:
Instrument detection with synonyms.
Parameter range extraction.
Natural-language parsing fallback logic.
Alias matching.
Candidate generation with hybrid strategies and Bayesian search.
Cross-event evaluation and risk metrics.
Microstructure feature computations.
RL agent training stub (mocked if necessary).
Add integration tests verifying that the pipeline runs end-to-end on a sample thesis, generates diverse candidates and correctly evaluates them using risk-adjusted metrics.
Implementation Roadmap
Create new config/data files: symbol_aliases.yaml, data/nlp_training_examples.json, feature_sets.py, rl_agents.py (optional), docs/model_registry.md.
Enhance hypothesis_parser.py: load symbol aliases; map models using aliases; fetch model-specific parameter ranges; parse enriched LLM outputs; handle synonyms and fallback logic.
Update model_registry.yaml: add default_param_ranges, aliases, recommended_horizon_bars, valid_instrument_universe, volatility_regime and risk_metrics fields.
Expand model_generation.py: include stop-loss and take-profit parameters by default; implement hybrid candidate generation; call advanced parameter search when requested; use feature_recipe to construct signals.
Implement parameter_search.py: provide Bayesian optimisation and grid search functions; allow hooking in evaluation callbacks.
Adapt evaluation.py and types.py: support cross-event evaluation; compute risk metrics; enforce gating thresholds for Sharpe ratio and drawdown.
Extend scripts/run_pipeline.py: handle multiple event IDs; parse new CLI flags (--hybrid, --search-method, --rl); pass new options to generator and evaluator.
**Add microstructure feature computations in feature_sets.py and integrate them into hypotheses via fea
```
