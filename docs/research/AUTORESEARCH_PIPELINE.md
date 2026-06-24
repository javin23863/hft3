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
  --max-candidates 5

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
