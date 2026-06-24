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
| `evaluation.py` | `packages/research_pipeline/evaluation.py` → `WorkbenchEngine` |
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

Each artifact-producing run writes:

- `pipeline_runtime_config.json` — loaded/effective runtime defaults plus hash
- `pipeline_run.log` — JSON-lines run log for operational debugging
- `candidate_prefilter.json` — lightweight prefilter receipt
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

`--hftbacktest-realism` remains fail-closed: the writer is called only after a
promoted screening row is strict replay-eligible and carries a robustness
evidence receipt from the robustness applicator.

Implementation plan and review gates: [AUTORESEARCH_PIPELINE_UPGRADE_PLAN.md](../project/AUTORESEARCH_PIPELINE_UPGRADE_PLAN.md).

## LLM

Default model: `gpt-5.5` with `xhigh` reasoning through an OpenAI-compatible `/v1/chat/completions` endpoint. Override with `HFT3_RESEARCH_LLM_MODEL`, `HFT3_MODEL_DEVELOPMENT_LLM_MODEL`, `HFT3_LLM_BASE_URL`, and `HFT3_LLM_REASONING_EFFORT`. This runtime is **not** OpenFoundry or AlphaGeometry. Heuristic fallback remains active when the endpoint is unavailable.

After-action reports use the same GPT-5.5 XHIGH runtime via `packet_runner`. See [VENDOR_BOUNDARIES.md](VENDOR_BOUNDARIES.md).

Relationship reasoning is not a packet LLM output surface. It may hold slow/offline candidate links across defined contexts only, but those candidates are non-authoritative until evidence and proof trace validation passes.

Optional pre-run idea generation (`--idea-set`) emits `schema_pipeline_idea_set_v1` machine packets. Ideas expand the candidate queue only after static validation; full idea-set runs require VectorBT prefiltering and still must pass workbench gates. Ideas do not select models, tune parameters, or promote candidates. AAR-derived review memory is compacted into advisory fact codes for ideation context only.

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
