# Autoresearch pipeline

Authority: [dev_instructions.pdf](../references/dev_instructions.pdf)

Workstation-only NL → hypothesis → backtest → artifact pipeline. Does **not** touch live Rithmic or CHI404 hot path until colo is stable (BLUEPRINT §4).

**Vendor vs LLM vs relationship reasoning:** OpenFoundry + AlphaGeometry are **git submodules** under `vendor/`. GPT-5.5 is the OpenAI-compatible runtime LLM for research/model-development parsing and after-action analysis. The hft3 relationship-reasoning layer is separate offline code under `packages/research_pipeline/relationship_reasoning/`. See [VENDOR_BOUNDARIES.md](VENDOR_BOUNDARIES.md) and [PACKET_LLM_CONTRACT.md](PACKET_LLM_CONTRACT.md).

## Architecture map

| PDF module | hft3 implementation |
|------------|----------------------|
| `hypothesis_parser.py` | `packages/research_pipeline/hypothesis_parser.py` |
| `document_ingestion.py` | `packages/research_pipeline/document_ingestion.py` |
| `knowledge_graph.py` | `packages/research_pipeline/knowledge_graph.py` → `data_layer/kg/` |
| Slow relationship reasoning | `packages/research_pipeline/relationship_reasoning/` candidate → evidence → proof trace → non-authoritative promotion record |
| `model_generation.py` | `packages/research_pipeline/model_generation.py` |
| Deterministic parameter search | `packages/research_pipeline/parameter_search.py` |
| `evaluation.py` | `packages/research_pipeline/evaluation.py` → `WorkbenchEngine` |
| RL research process | `packages/research_pipeline/rl_agents.py` |
| Research microstructure features | `packages/features_engine/feature_sets.py` |
| `deployment.py` | `packages/research_pipeline/deployment.py` → `research_cards/pipeline_runs/` |
| CLI | `scripts/run_pipeline.py` |

## Non-goals (v1)

- Duplicate `ReplayRunner` or `run_event_replay.py`
- Full FIBO ontology or graph database
- Authoritative OpenFoundry/KG relation writes from unvalidated relationship candidates
- New C++ feature slots from natural language
- Live gateway deploy (research artifacts only until CHI404 online)

## Usage

```bash
pip install -r packages/research_pipeline/requirements.txt

python scripts/run_pipeline.py \
  --thesis "Fade spread blowout after CPI surprise" \
  --event-id CPI_2024_09_11_TIGHT \
  --max-candidates 5 \
  --min-psr 0.95 \
  --min-dsr 0.90 \
  --cscv \
  --max-pbo 0.20 \
  --commission-per-trade 1.25

# Parse + candidate generation only (no backtest)
python scripts/run_pipeline.py --thesis "..." --event-id CPI_2024_09_11_TIGHT --dry-run
```

Deterministic search controls:

```bash
python scripts/run_pipeline.py \
  --thesis "Fade spread blowout after CPI surprise" \
  --event-id CPI_2024_09_11_TIGHT \
  --max-candidates 12 \
  --search-method seeded \
  --search-seed 42 \
  --dry-run
```

`--search-method grid` is the default. `seeded` samples deterministically from
the declared grid. Hybrid candidate expansion is on by default and combines the
primary model with up to two adjacent model ids from the parsed feature list; use
`--no-hybrid` to disable it. `bayesian` and `evolutionary` currently fall back
explicitly to seeded search with `method_status=method_unavailable`; they do not
add dependencies or run hidden optimizers.

Cross-event evaluation:

```bash
python scripts/run_pipeline.py \
  --thesis "Fade spread blowout after CPI surprise" \
  --event-id CPI_2024_09_11_TIGHT,NFP_2024_09_06 \
  --event-id FOMC_2024_09_18 \
  --max-candidates 5
```

The single-event path is preserved. Repeated or comma-separated `--event-id`
values are aggregated in `packages/research_pipeline/evaluation.py`. Packet
`event_id` remains the primary catalog event, and `event_ids` records the full
evaluation set. Aggregate Sharpe, Sortino, maximum drawdown, and worst tail
fields are diagnostics over per-event net PnL totals and are marked
`risk_metrics_gateable=false`. Drawdown is labeled
`cross_event_net_pnl_input_order_diagnostic` because it is path-dependent on the
caller-supplied event order until catalog chronology is wired. Configured risk
thresholds fail closed until a timestamped equity/return series supplies
gateable metrics. VectorBT and HftBacktest realism reject multi-event screening
until they can produce per-event screening evidence.

Optional research document:

```bash
python scripts/run_pipeline.py \
  --thesis "..." \
  --doc docs/references/dev_instructions.pdf \
  --event-id CPI_2024_09_11_TIGHT
```

## Runtime Reproducibility

Legacy `scripts/run_pipeline.py` now loads a separate JSON runtime config
(`config/research_pipeline/default_runtime.json` by default). This is distinct
from the autoresearch loop YAML passed through `--config`.

## Data-Quality Pre-Check and Skip List

Missing bars in an OHLCV file invalidate trading signals and performance
metrics derived from that period. A single corrupt or empty NPZ file can
abort an entire full-lake backtest when `abort_on_failed_units=true`. The
data-quality pre-check catches these files *before* dispatch.

### Pre-checking the NPZ lake

```bash
# Check every NPZ file under HFT3_NPZ_ROOT for OHLCV validity
python scripts/check_lake_data.py

# Filter by instrument or event substring
python scripts/check_lake_data.py --pattern ZN

# Write report to a custom path
python scripts/check_lake_data.py --out runtime/reports/lake_data_quality.json
```

The script runs `research_pipeline.data_quality.check_npz_ohlcv()` on each
`.npz` file, checking that the MBO event array (`data` member) has ≥2 rows
with the required fields (`ev`, `local_ts`, `px`, `qty`, `order_id`). Files
that pass `--require-runnable-npz` (existence + row count) but contain
insufficient data to build OHLCV bars are flagged as invalid.

### Skip list mechanism

Unit IDs flagged by the pre-check (or known-bad from prior runs) are
maintained in `config/autoresearch/default.yaml` under `skipped_unit_ids`:

```yaml
skipped_unit_ids:
  - ZN.v.0_EIA_NATGAS_2019_11_28_TIGHT  # NPZ exists but <2 MBO events
```

`run_pipeline.py` merges this config list with an optional
`--skip-bad-units-file` (output of `check_lake_data.py`) and removes matching
candidates before any VectorBT compute. Skipped units are logged but not
counted as failures.

The v2 paid-screen orchestrator (`run_vectorbt_paid_screen_v2.py`) accepts
the same `--skip-bad-units-file` flag and removes matching units before
batch grouping.

### Failure handling

- `abort_on_failed_units` defaults to **false** for multi-event / full-lake
  runs. A single bad NPZ no longer kills 217 workers.
- `--fail-fast` forces `abort_on_failed_units=true` for CI or single-unit
  tests where immediate abort is desired.
- `NoOHLCVDataError` (in `research_pipeline.data_quality`) distinguishes
  data-quality failures from algorithmic failures. The paid-screen batch
  path tags these with `error_category="data_quality"`.
- The run manifest includes `failure_counts_by_type` — a dictionary keyed
  by `"{error_category}:{error}"` (e.g. `data_quality:no_ohlcv_data: 18`)
  to separate data issues from model failures.

### Fixing bad NPZ files

Once a unit is flagged, attempt to regenerate its OHLCV bars from raw
order-book or trade data. If regeneration succeeds, replace the faulty NPZ
and remove the unit from the skip list. If regeneration is not possible
(e.g. exchange feed was down), document the reason in the config comments
and keep the unit in the permanent skip list.

Each artifact-producing run writes:

- `pipeline_runtime_config.json` — loaded/effective runtime defaults plus hash
- `pipeline_run.log` — JSON-lines run log for operational debugging
- `candidate_prefilter.json` — lightweight prefilter receipt
- `num_trials.json` — generated/evaluated candidate counts and Sharpe variance
- `edge_evaluation_summary.json` — PSR/DSR, costs, power checks, PBO, and tail metrics
- `pipeline_run_receipt.json` — final structured payload for the run,
  including fail-closed failures after artifact setup

Optional documents are cached by source fingerprint under
`runtime/research_pipeline/doc_cache` unless disabled in the runtime config.
Local file cache keys include the source file SHA256. URL caching is disabled
by default because remote content can change behind a stable URL. The cache
covers extracted text summary and KG slice records; it does not change the
VectorBT or HftBacktest gates.

Candidate generation is controlled by the runtime config's
`candidate_search` section or the `--candidate-search-method` and
`--candidate-search-seed` CLI flags. Supported methods are `grid`,
`bayesian`, and `evolutionary`. These methods only select parameter sets before
VectorBT screening; the emitted `candidate_search` metadata records
`backend=stdlib`, the seed, grid size, iterations, and
`objective_evaluations=0`. They do not promote candidates and do not replace
the VectorBT -> robustness evidence -> HftBacktest gate order.

The parser consumes the model registry metadata documented in
[model_registry.md](../model_registry.md). Natural-language model aliases,
registry default parameter ranges, `volatility_regime`, and canonical CME
symbol aliases are copied into parsed-hypothesis receipts. If `--symbol` is
omitted, the pipeline derives the target symbol from the parsed compatible
instrument universe. If `--symbol` is supplied and conflicts with the model's
`valid_instrument_universe` or `target_instrument_universe`, candidate
generation fails closed before VectorBT/HftBacktest. Concrete loader variants
such as `MES.v.0` compare by canonical root (`MES`) for compatibility while
preserving the requested suffix for downstream feature-store loading.

Structural registry entries are metadata/feature receipts, not primary
autoresearch hypothesis routes. The parser only selects `kind=hypothesis`
models as primary models for this entrypoint.

RL policy artifacts are controlled by the runtime config's `rl_training`
section or the `--rl-training-data`, `--rl-feature`, `--rl-device`,
`--rl-required`, and `--rl-seed` CLI flags. The current implementation is
opt-in until real training data and a GPU host command are named. Once enabled,
it is fail-closed: CPU runs write a small research-only tabular policy artifact,
while CUDA requests through the normal pipeline still write a blocked
`rl_policy_artifact.json` until a GPU handoff is named. GPU readiness is checked
separately by `rl_gpu_training_readiness_artifact`, which records the training
data receipt, validated feature list, host, command, bounded output directory,
duration budget, stop rule, and CUDA runtime smoke. The bounded local GPU smoke
entrypoint is `scripts/run_rl_gpu_smoke.py`; it writes a
non-promotable `rl_gpu_smoke_artifact.json` and checkpoint receipt. RL artifacts
are always non-promotable and cannot bypass VectorBT, robustness evidence, or
HftBacktest gates.

The full RL handoff is split into two explicit source-backed entrypoints:
`scripts/build_rl_training_data.py` builds point-in-time `rl_training_rows.jsonl`
from fs_v1 feature-store rows, and `scripts/train_deep_rl_policy.py` trains a
bounded non-promotable replay Q-network artifact on the named host.

RL CPU policy artifacts are cached under
`runtime/research_pipeline/rl_policy_cache` by default. Cache receipts include
the training-data SHA256, normalized feature list, device, seed, row cap,
artifact schema, and trainer source hash. A per-run `rl_policy_artifact.json`
is still written every time and records `cache_receipt.status` as `miss`,
`hit`, `disabled`, or `blocked`; cached artifacts are validated before reuse.
CUDA handoff artifacts are not written through to the cache.

The legacy post-filter evaluation loop accepts `--evaluation-workers` and the
runtime config's `evaluation.workers`. The default is `1`. Values greater than
one use a bounded `ProcessPoolExecutor` for candidate-level independence. MSI
is capped by `evaluation.msi_max_workers` (default `1`), while CHI404/Vast-style
hosts are capped by `evaluation.max_workers`. VectorBT paid-screen and
HftBacktest campaign runners keep their own worker controls.

Legacy evaluation gates can be selected with `gate_profiles.default_profile` or
`--gate-profile`, with explicit CLI overrides for min net PnL, min trades, max
tail loss, and min win rate. These profiles apply only to the legacy
`WorkbenchEngine` evaluation result fields already emitted by this entrypoint;
they do not replace VectorBT promotion gates, robustness evidence, or
HftBacktest replay gates.

When no `--gate-profile` override is supplied, a model-registry
`volatility_regime` may select a matching legacy gate profile through
`gate_profiles.volatility_regime_profiles`. The run receipt and runtime config
receipt record a per-candidate `gate_profile_plan` so reviewers can distinguish
CLI overrides, runtime-config defaults, and model-registry selection.

`--hftbacktest-realism` remains fail-closed: the writer is called only after a
promoted screening row is strict replay-eligible and carries a robustness
evidence receipt from the robustness applicator.

Implementation plan and review gates: [AUTORESEARCH_PIPELINE_UPGRADE_PLAN.md](../project/AUTORESEARCH_PIPELINE_UPGRADE_PLAN.md).

Edge-testing implementation plan: [EDGE_TESTING_ALPHA_EVALUATION_PLAN.md](../project/EDGE_TESTING_ALPHA_EVALUATION_PLAN.md).

## Edge Evaluation

The simple Workbench evaluation path now records statistical, cost, validation,
and tail-risk evidence for every candidate. Defaults are configured under
`edge_evaluation` in `config/research_pipeline/default_runtime.json`; CLI flags
override the JSON runtime config for a single run.

Statistical significance:

- `psr` is the probabilistic Sharpe ratio, the probability that the observed
  Sharpe exceeds `--sr-benchmark` after skewness and kurtosis adjustment.
- `dsr` is the deflated Sharpe ratio. It uses the number of generated
  candidates and the variance of candidate Sharpes to account for selection
  bias across trials.
- `adjusted_p_value` stores the Holm-adjusted selected-test p-value derived
  from PSR. The run also writes `num_trials.json` so reviewers can recompute
  their own correction.
- `required_sample_size` and `sample_size_pass` come from the configured
  `--alpha` and `--power` assumptions. By default this is recorded but not a
  hard gate; pass `--require-sample-size` to fail candidates that are too short.

Validation and overfitting:

- `--cscv` computes combinatorially symmetric cross-validation diagnostics from
  the aligned candidate PnL matrix. `--cscv-subsets` controls chronological
  partitioning and `--max-pbo` can gate candidates by probability of backtest
  overfitting.
- `--rolling-validation --rolling-window N` writes rolling PnL-window summaries
  for each candidate into `edge_evaluation_summary.json`.

Costs and risk:

- `--spread-cost`, `--commission-per-trade`, `--slippage-bps`, and
  `--market-impact-coeff` subtract estimated execution costs from the gross PnL
  series before gates are evaluated.
- Evaluation results include gross PnL, net PnL, cost breakdown, Sharpe,
  Sortino, skewness, kurtosis, CVaR 95/99, and tail ratio.
- `--min-tail-ratio`, `--max-cvar-95`, and `--max-cvar-99` can be used as
  fail gates for fat-tail exposure.

## LLM

Default model: `gpt-5.5` with `xhigh` reasoning through an OpenAI-compatible `/v1/chat/completions` endpoint. Override with `HFT3_RESEARCH_LLM_MODEL`, `HFT3_MODEL_DEVELOPMENT_LLM_MODEL`, `HFT3_LLM_BASE_URL`, and `HFT3_LLM_REASONING_EFFORT`. This runtime is **not** OpenFoundry or AlphaGeometry. Heuristic fallback remains active when the endpoint is unavailable.

After-action reports use the same GPT-5.5 XHIGH runtime via `packet_runner`. See [VENDOR_BOUNDARIES.md](VENDOR_BOUNDARIES.md).

Relationship reasoning is not a packet LLM output surface. It may hold slow/offline candidate links across defined contexts only, but those candidates are non-authoritative until evidence and proof trace validation passes.

Optional pre-run idea generation (`--idea-set`) emits `schema_pipeline_idea_set_v1` machine packets. Ideas expand the candidate queue only after static validation; full idea-set runs require VectorBT prefiltering and still must pass workbench gates. Ideas do not select models, tune parameters, or promote candidates. AAR-derived review memory is compacted into advisory fact codes for ideation context only.

## Advanced Research Surfaces

Parser and registry metadata:

- Symbol aliases are declared in `packages/features_engine/config/symbol_aliases.yaml`.
- CLI `--symbol` is optional; when omitted the pipeline uses the first parsed
  compatible instrument, falling back to `MES` only when no symbol is parsed.
  An explicit incompatible `--symbol` fails closed.
- Model aliases, parameter ranges, valid instruments, volatility regime, and risk
  metric metadata are declared in `packages/features_engine/config/model_registry.yaml`.
- The hypothesis parser records instrument compatibility metadata; unsupported
  target instruments are visible but do not silently become compatible. If a
  routable model does not declare `valid_instrument_universe`, CLI candidate
  routing fails closed instead of assuming compatibility.

Microstructure feature library:

- `packages/features_engine/feature_sets.py` implements research-only
  order-book imbalance, queue imbalance, micro-price, VAMP, and weighted-depth
  price functions.
- These functions consume one point-in-time depth snapshot at decision time `t`.
- Cross-side micro-price and VAMP return `0.0` when either side of book is
  missing; same-side weighted depth remains available for a declared side.
- Non-positive depth and non-finite price/quantity inputs are rejected.
- They do not modify `FeatureIndex`, C++ feature slots, or live execution paths.

RL research process:

- `--rl` runs a deterministic tabular Q-learning research process and writes
  `rl_policy_artifact.json`.
- RL requires explicit `--rl-feature` fields; label-like or non-PIT feature names
  such as future/next/target/reward/return/PNL fields are rejected.
- If monotonic decision timestamps are present, RL records
  `audit_status=chronology_audited`; without timestamps, metrics are marked
  `chronology_not_audited`.
- Missing or malformed RL input writes a blocked artifact and stops before
  VectorBT, HftBacktest, evaluation, or deployment.
- Trained RL artifacts are research-only with
  `promotion_status=blocked_downstream_validation_required`; they cannot reach
  VectorBT, HftBacktest, evaluation, or deployment except ordinary `--dry-run`
  artifact inspection.

Deployment:

- Workbench-only evaluation is research-only and emits packets without calling
  `deploy_best`.
- `deploy_best` itself returns `None` unless at least one result passes all
  gates; it does not fall back to highest net PnL.

## Relationship Data Sources

Every evidence item must name a defined `RelationshipDataSource` and exact canonical `source_ref`. There is no implicit data feed. Every validated candidate also needs at least one empirical offline source; code/config/PDF definitions alone cannot validate a relationship.

| Context | Defined sources | Canonical path | Authority |
|---------|-----------------|----------------|-----------|
| `micro` | `DATABENTO_CME_MBO_NPZ` | `data/npz/<symbol>_<event_id>_mbo.npz` | Offline CME MBO replay observations from Databento GLBX.MDP3 |
| `micro` | `MICROSTRUCTURE_PDF_MANIFEST` | `docs/references/MANIFEST.md` | Citation authority for microstructure concepts, not raw observations |
| `macro` | `ECONOMIC_EVENT_UNIVERSE` | `packages/economic_event_universe/config/event_universe.yaml` | Macro catalog metadata, official source URLs, labels, windows |
| `macro` | `SOURCED_RELEASE_CALENDAR` | `packages/economic_event_universe/config/calendars/sourced/*.csv` | Official rows only when `row_status=SOURCED` |
| `macro` | `DATA_SYSTEM_EVENTS_CSV` | `packages/data_system/config/events.csv` | Canonical replay `event_id` and window artifact |
| `regime` | `DATA_SYSTEM_EVENTS_CSV`, `SOURCED_RELEASE_CALENDAR`, `ECONOMIC_EVENT_UNIVERSE` | `packages/data_system/config/events.csv`, `packages/economic_event_universe/config/calendars/sourced/*.csv`, `packages/economic_event_universe/config/event_universe.yaml` | Macro event/window inputs for regime-context review |
| `regime` | `FEATURES_ENGINE_REGIME_FILTER` | `packages/features_engine/src/regime/regime_filter.py:<label-or-function>` | Definition-only regime posterior logic |
| `regime` | `EVENT_CONTEXT_REGIME_MAP` | `packages/features_engine/config/event_context_regime.json:<key>` | Definition-only configured boost map |
| `world_event` | `GDELT_WORLD_EVENTS` | `artifacts/world_events/gdelt/events/<YYYYMMDD>.jsonl:<GLOBALEVENTID>` | Offline cached GDELT 2.1 event record with actor, event, location, tone, and source URL provenance |

`world_event` is valid only when evidence cites a cached GDELT record through the canonical source ref and validation can find the matching cache record under the supplied repo root. The backend is `packages/research_pipeline/world_events/`; it is not UI code and does not write to OpenFoundry or KG.

`SOURCED_RELEASE_CALENDAR` refs must include `row_status=SOURCED`. `DATA_SYSTEM_EVENTS_CSV` refs must start with `packages/data_system/config/events.csv:`. `DATABENTO_CME_MBO_NPZ` refs must match `data/npz/<symbol>_<event_id>_mbo.npz`.

World-event backend details: [WORLD_EVENT_DATA_BACKEND.md](WORLD_EVENT_DATA_BACKEND.md).

## Outputs

- `research_cards/pipeline_runs/<run_id>/` — `request_packet.json`, `response_packet.json`, config, results, report
- `research_cards/kg/nodes.jsonl` / `edges.jsonl` — document-derived graph slices

See [RESEARCH_ENTRYPOINTS.md](../vault/RESEARCH_ENTRYPOINTS.md) for canonical research order.
