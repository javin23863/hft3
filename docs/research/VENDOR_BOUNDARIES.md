# Vendor repos vs LLM runtime (do not conflate)

hft3 vendors **two separate upstream repos** as git submodules. They are **not** interchangeable with Ollama cloud model names (Gemma, GLM, etc.).

## Vendored codebases (inside this repo)

| Submodule | Upstream | Path | Role in hft3 |
|-----------|----------|------|--------------|
| **OpenFoundry** | [syzygyhack/open-foundry](https://github.com/syzygyhack/open-foundry) | `vendor/openfoundry/` | Ontology / ODL schema, connector contract, JSONL KG conventions |
| **AlphaGeometry** | [google-deepmind/alphageometry](https://github.com/google-deepmind/alphageometry) | `vendor/alphageometry/` | Neuro-symbolic **pattern reference** (verifier before LLM); not run on order books |

Initialize both:

```bash
git submodule update --init vendor/openfoundry vendor/alphageometry
```

Pins: [`integrations/openfoundry/VENDOR.lock`](../integrations/openfoundry/VENDOR.lock)

hft3 adapter: [`integrations/openfoundry/hft3-cme-mbo.yaml`](../integrations/openfoundry/hft3-cme-mbo.yaml) · [`data_layer/openfoundry_bridge.py`](../../packages/data_layer/openfoundry_bridge.py)

## What is NOT a vendored repo

| Name | What it is | Common mistake |
|------|------------|----------------|
| **Gemma (`gemma4:31b-cloud`)** | Ollama **cloud LLM** for after-action / analyst | Treating Gemma as the same as `vendor/alphageometry/` |
| **glm-5.1:cloud** | Ollama cloud model for **autoresearch pipeline** NL parsing | Confusing with OpenFoundry ontology |
| **AlphaGeometry Meliad/JAX** | Upstream research LM inside `vendor/alphageometry/` | Running it on MBO order books in production |

**Google DeepMind** appears in this stack as the **AlphaGeometry submodule** (symbolic geometry research code). Gemma is a different product accessed via Ollama cloud — not vendored in git.

## LLM lanes (workstation, post-run / NL only)

| Lane | Default model | Env override | Entry |
|------|---------------|--------------|-------|
| After-action, analyst chat | `gemma4:31b-cloud` | `HFT3_OLLAMA_MODEL` | `data_layer.llm.packet_runner.run_llm_on_aar_packet` |
| Autoresearch pipeline | `glm-5.1:cloud` | `HFT3_PIPELINE_LLM_MODEL` | `research_pipeline/llm.py` + `packets.py` |
| Graphify semantic (optional) | `gemma4:31b-cloud` | `GRAPHIFY_OLLAMA_MODEL` | `scripts/graphify_semantic_local.ps1` |

```bash
ollama run gemma4:31b-cloud    # after-action
ollama run glm-5.1:cloud       # research pipeline
```

Packet I/O spec: [PACKET_LLM_CONTRACT.md](PACKET_LLM_CONTRACT.md)

Open fixes: [PACKET_STRICT_FIX_CHECKLIST.md](PACKET_STRICT_FIX_CHECKLIST.md)

## KG authority

All graph slices (workbench runs, pipeline documents, after-action) append to **`research_cards/kg/`** using node types aligned with the OpenFoundry connector — not a parallel ad-hoc store. Pipeline code validates the OpenFoundry connector before persisting KG slices.

## Quant X

`quant_data_refinery/` (Quant X) is **out of scope** — not vendored, not used.
