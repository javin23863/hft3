# Autoresearch pipeline

Authority: [dev_instructions.pdf](../references/dev_instructions.pdf)

Workstation-only NL → hypothesis → backtest → artifact pipeline. Does **not** touch live Rithmic or CHI404 hot path until colo is stable (BLUEPRINT §4).

**Vendor vs LLM:** OpenFoundry + AlphaGeometry are **git submodules** under `vendor/`. Ollama models are **runtime** choices — `glm-5.1:cloud` for this pipeline; Gemma for after-action separately. See [VENDOR_BOUNDARIES.md](VENDOR_BOUNDARIES.md) and [PACKET_LLM_CONTRACT.md](PACKET_LLM_CONTRACT.md).

## Architecture map

| PDF module | hft3 implementation |
|------------|----------------------|
| `hypothesis_parser.py` | `packages/research_pipeline/hypothesis_parser.py` |
| `document_ingestion.py` | `packages/research_pipeline/document_ingestion.py` |
| `knowledge_graph.py` | `packages/research_pipeline/knowledge_graph.py` → `data_layer/kg/` |
| `model_generation.py` | `packages/research_pipeline/model_generation.py` |
| `evaluation.py` | `packages/research_pipeline/evaluation.py` → `WorkbenchEngine` |
| `deployment.py` | `packages/research_pipeline/deployment.py` → `research_cards/pipeline_runs/` |
| CLI | `scripts/run_pipeline.py` |

## Non-goals (v1)

- Duplicate `ReplayRunner` or `run_event_replay.py`
- Full FIBO ontology or graph database
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

## LLM

Default model: `glm-5.1:cloud` via Ollama (`ollama run glm-5.1:cloud`; override with `HFT3_PIPELINE_LLM_MODEL`). This is an Ollama cloud runtime — **not** OpenFoundry or AlphaGeometry. Heuristic fallback when Ollama is unavailable.

After-action reports use Gemma (`HFT3_OLLAMA_MODEL`, default `gemma4:31b-cloud`) via `packet_runner`. See [VENDOR_BOUNDARIES.md](VENDOR_BOUNDARIES.md).

## Outputs

- `research_cards/pipeline_runs/<run_id>/` — `request_packet.json`, `response_packet.json`, config, results, report
- `research_cards/kg/nodes.jsonl` / `edges.jsonl` — document-derived graph slices

See [RESEARCH_ENTRYPOINTS.md](../vault/RESEARCH_ENTRYPOINTS.md) for canonical research order.
