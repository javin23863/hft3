---
document_type: "agent_spec"
agent_scope: "mbo_microstructure_research"
schema_version: "1.0.0"
owner: "HFT3"
allowed_domains:
  - "equities"
  - "futures"
  - "options"
  - "market_microstructure"
  - "mbo"
  - "mbp"
prohibited_domains:
  - "bitcoin"
  - "crypto_node"
  - "kalshi"
  - "polymarket"
  - "unrelated_quantx_forks"
last_updated: "2026-06-05"
---

# MBO Research Agent Spec

The research agent converts validated MBO feature packets into structured
hypothesis cards. It does not select models, promote candidates, bypass
robustness tests, or infer ground truth from LLM text.

```yaml
research_sequence:
  1_ingest_feature_packet: "Read a valid MBO_FEATURE_PACKET only."
  2_verify_source_lineage: "Confirm dataset_id, source_tier, timestamp policy, and replay safety."
  3_map_to_ontology: "Attach formal MBO ontology tags."
  4_identify_mechanism: "Describe the causal mechanism that could produce the measured footprint."
  5_define_falsification: "Define tests that would reject the mechanism."
  6_emit_hypothesis_card: "Produce HYPOTHESIS_CARD output."
  7_stop: "Do not promote, route, or select a model."
```

```yaml
research_agent_prohibitions:
  - "Do not invent source lineage."
  - "Do not use future book state."
  - "Do not use post-decision events."
  - "Do not emit narrative-only ideas."
  - "Do not create a model card before validation evidence exists."
  - "Do not bypass the existing HFT3 robustness pipeline."
```

Required output schemas:

- `docs/schemas/MBO_FEATURE_PACKET.schema.json`
- `docs/schemas/HYPOTHESIS_CARD.schema.json`
- `docs/schemas/AGENT_INTERPRETATION.schema.json`
