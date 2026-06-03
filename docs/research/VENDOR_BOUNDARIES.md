# Vendor repos vs LLM runtime (do not conflate)

hft3 vendors **two separate upstream repos** as git submodules. They are **not** interchangeable with the OpenAI-compatible GPT-5.5 runtime.

## Vendored codebases (inside this repo)

| Submodule | Upstream | Path | Role in hft3 |
|-----------|----------|------|--------------|
| **OpenFoundry** | [syzygyhack/open-foundry](https://github.com/syzygyhack/open-foundry) | `vendor/openfoundry/` | Ontology / ODL schema conventions, connector contract, JSONL KG conventions |
| **AlphaGeometry** | [google-deepmind/alphageometry](https://github.com/google-deepmind/alphageometry) | `vendor/alphageometry/` | Neuro-symbolic **pattern reference** (verifier before LLM); not run on order books |

Initialize both:

```bash
git submodule update --init vendor/openfoundry vendor/alphageometry
```

Pins: [`integrations/openfoundry/VENDOR.lock`](../../integrations/openfoundry/VENDOR.lock)

hft3 adapter: [`integrations/openfoundry/hft3-cme-mbo.yaml`](../../integrations/openfoundry/hft3-cme-mbo.yaml) · [`data_layer/openfoundry_bridge.py`](../../packages/data_layer/openfoundry_bridge.py)

hft3 domain pack: [`integrations/openfoundry/domain-packs/hft3/`](../../integrations/openfoundry/domain-packs/hft3/) with sidecar citations under `citations/<Type>.yaml`.

## What is NOT a vendored repo

| Name | What it is | Common mistake |
|------|------------|----------------|
| **GPT-5.5 (`gpt-5.5`)** | OpenAI-compatible runtime LLM for after-action, research, and model-development parsing | Treating GPT runtime as vendored OpenFoundry or AlphaGeometry code |
| **AlphaGeometry Meliad/JAX** | Upstream research LM inside `vendor/alphageometry/` | Running it on MBO order books in production |

**Google DeepMind** appears in this stack as the **AlphaGeometry submodule** (symbolic geometry research code) and as a pattern reference for slow neuro-symbolic verification. GPT-5.5 is a runtime LLM accessed through an OpenAI-compatible endpoint — not vendored in git.

## Three separate layers

| Layer | hft3 path | Authority boundary |
|-------|-----------|--------------------|
| OpenFoundry operational ontology | `integrations/openfoundry/` + `packages/data_layer/openfoundry_bridge.py` | Object/link/action/schema/governance adapter. This is not a relationship search engine. |
| Per-LLM closed-world contract | `packages/data_layer/packet/` + `packages/data_layer/llm/` | Lane-specific packet schema and closed-claim `kg_annotations[]`; prevents free-form LLM KG claims. |
| DeepMind-style relationship reasoning | `packages/research_pipeline/relationship_reasoning/` | Slow/offline candidate relationship lifecycle: proposal → defined evidence source → proof trace → validation → non-authoritative promotion record. No trading, no hot path, no direct KG/OpenFoundry write. |

The relationship-reasoning layer is hft3 code. It does not run AlphaGeometry on order books, does not replace the OpenFoundry ontology, and does not bypass packet closed-claim validation. It rejects evidence from undefined sources, spoofed refs, and definition-only evidence. `world_event` evidence is valid only through the hft3-authored backend `GDELT_WORLD_EVENTS` cache source.

FinceptTerminal was reviewed only as a public reference for connector categories. hft3 does not copy, vendor, import, or link FinceptTerminal code.

## LLM lanes (workstation, post-run / NL only)

| Lane | Default model | Env override | Entry |
|------|---------------|--------------|-------|
| After-action, analyst chat | `gpt-5.5` + `xhigh` reasoning | `HFT3_AAR_LLM_MODEL`, `HFT3_LLM_REASONING_EFFORT` | `data_layer.llm.packet_runner.run_llm_on_aar_packet` |
| Autoresearch pipeline | `gpt-5.5` + `xhigh` reasoning | `HFT3_RESEARCH_LLM_MODEL`, `HFT3_PIPELINE_LLM_MODEL` | `research_pipeline/llm.py` + `packets.py` |
| Model-development hypothesis parse | `gpt-5.5` + `xhigh` reasoning | `HFT3_MODEL_DEVELOPMENT_LLM_MODEL` | `data_layer.llm.packet_runner.run_llm_on_hypothesis_request` |

```bash
set HFT3_LLM_API_KEY=...       # or OPENAI_API_KEY
set HFT3_LLM_BASE_URL=https://api.openai.com/v1
set HFT3_LLM_REASONING_EFFORT=xhigh
```

Packet I/O spec: [PACKET_LLM_CONTRACT.md](PACKET_LLM_CONTRACT.md)

Ontology hardening: [ONTOLOGY_HARDENING.md](ONTOLOGY_HARDENING.md) · [ONTOLOGY_CITATIONS.md](ONTOLOGY_CITATIONS.md)

Open fixes: [PACKET_STRICT_FIX_CHECKLIST.md](PACKET_STRICT_FIX_CHECKLIST.md)

## KG authority

All graph slices (workbench runs, pipeline documents, after-action) append to **`research_cards/kg/`** using node types aligned with the OpenFoundry connector — not a parallel ad-hoc store. Pipeline code validates the OpenFoundry connector before persisting KG slices.

After-action LLM output may only persist closed-claim `kg_annotations[]` in the `{source_type, source_id, field, value, cite}` shape. Free-form KG relation proposals are dropped before persistence.

`narrative_md` is deterministic renderer output, not LLM-written prose.

Slow relationship-reasoning promotion records are review artifacts only. They can become KG/OpenFoundry relations only through a separate validated adapter path; this module performs no write. World-event backend details: [WORLD_EVENT_DATA_BACKEND.md](WORLD_EVENT_DATA_BACKEND.md).

## Quant X

`quant_data_refinery/` (Quant X) is **out of scope** — not vendored, not used.
