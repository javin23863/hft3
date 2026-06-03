# Autoresearch pipeline

Authority: [dev_instructions.pdf](../references/dev_instructions.pdf)

Workstation-only NL → hypothesis → backtest → artifact pipeline. Does **not** touch live Rithmic or CHI404 hot path until colo is stable (BLUEPRINT §4).

**Vendor vs LLM:** OpenFoundry + AlphaGeometry are **git submodules** under `vendor/`. GPT-5.5 is the OpenAI-compatible runtime LLM for research/model-development parsing and after-action analysis. See [VENDOR_BOUNDARIES.md](VENDOR_BOUNDARIES.md) and [PACKET_LLM_CONTRACT.md](PACKET_LLM_CONTRACT.md).

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

Default model: `gpt-5.5` with `xhigh` reasoning through an OpenAI-compatible `/v1/chat/completions` endpoint. Override with `HFT3_RESEARCH_LLM_MODEL`, `HFT3_MODEL_DEVELOPMENT_LLM_MODEL`, `HFT3_LLM_BASE_URL`, and `HFT3_LLM_REASONING_EFFORT`. This runtime is **not** OpenFoundry or AlphaGeometry. Heuristic fallback remains active when the endpoint is unavailable.

After-action reports use the same GPT-5.5 XHIGH runtime via `packet_runner`. See [VENDOR_BOUNDARIES.md](VENDOR_BOUNDARIES.md).

## Outputs

- `research_cards/pipeline_runs/<run_id>/` — `request_packet.json`, `response_packet.json`, config, results, report
- `research_cards/kg/nodes.jsonl` / `edges.jsonl` — document-derived graph slices

See [RESEARCH_ENTRYPOINTS.md](../vault/RESEARCH_ENTRYPOINTS.md) for canonical research order.
