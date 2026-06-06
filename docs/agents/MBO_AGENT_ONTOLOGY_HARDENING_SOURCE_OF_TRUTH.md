---
document_type: "source_of_truth"
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
last_updated: "2026-06-06"
---

# MBO Agent Ontology Hardening Source Of Truth

This document is the canonical source of truth for HFT3 LLM agent ontology
hardening for MBO and microstructure research. It converts the user-provided
developer prompt into a repository contract: embedded LLM agents must interpret
market state through formal ontology, source lineage, strict feature packets,
hypothesis cards, feature cards, validation cards, and model cards.

## Mission

Harden the LLM-facing research and model-development layer so embedded LLM
agents interpret MBO/MBP microstructure data through a fixed ontology, fixed
source-of-truth hierarchy, fixed feature schema, and fixed reasoning contract.

The goal is not to make the agent sound like a battlefield intelligence
officer. The goal is to make it behave like one by forcing all research,
feature proposals, model ideas, and MBO interpretations through structured
machine-readable objects.

## Non-Goals

- Do not redesign the pipeline.
- Do not add new execution gates.
- Do not create a separate strategy framework.
- Do not mix in QuantX, Bitcoin, Kalshi, Polymarket, or unrelated forks.
- Do not create narrative-only prompts.
- Do not let the LLM reason from vibes, metaphors, or loose language.

## Required Transformation

```text
raw market data
-> canonical event state
-> order-book ontology
-> feature packet
-> hypothesis card
-> validation card
-> model card
-> robustness result
-> trade-manager-readable behavior summary
```

The LLM is allowed to interpret structured evidence. The LLM is not allowed to
invent evidence.

## Required Deliverables

- `docs/agents/MBO_AGENT_OPERATING_DOCTRINE.md`
- `docs/agents/MBO_RESEARCH_AGENT_SPEC.md`
- `docs/agents/MBO_MODEL_DEVELOPMENT_AGENT_SPEC.md`
- `docs/ontology/MBO_MARKET_ONTOLOGY.md`
- `docs/ontology/SOURCE_OF_TRUTH_POLICY.md`
- `docs/schemas/MBO_FEATURE_PACKET.schema.json` as a synced mirror of `packages/data_layer/packet/schema_mbo_feature_packet_v1.json`
- `docs/schemas/HYPOTHESIS_CARD.schema.json`
- `docs/schemas/FEATURE_CARD.schema.json`
- `docs/schemas/MODEL_CARD.schema.json`
- `docs/schemas/VALIDATION_CARD.schema.json`
- `docs/schemas/AGENT_INTERPRETATION.schema.json`
- `tests/fixtures/mbo_minimal_replay_fixture.json`
- `tests/test_mbo_agent_schemas.py`

## Required Discovery Sources

The following docs are required discovery sources for this source-of-truth. They
are not additional deliverables, execution gates, promotion gates, or robustness
gates.

- `docs/research/PACKET_LLM_CONTRACT.md`
- `docs/workbench/PRODUCTION_READINESS_CHECKLIST.md`

## Boundary

The canonical MBO feature packet schema is
`packages/data_layer/packet/schema_mbo_feature_packet_v1.json`. The
LLM-facing `docs/schemas/MBO_FEATURE_PACKET.schema.json` file is a synced
mirror of that runtime contract, not an alternate packet shape. Other schemas
under `docs/schemas/` are LLM-facing research contracts. They do not add
execution routing, model-promotion gates, or a parallel robustness pipeline.
Existing HFT3 validation and robustness infrastructure remains the authority
for candidate promotion or rejection.

The packet/readiness discovery sources add source-of-truth visibility only; they
do not create new execution authority, promotion authority, or robustness gates.

## Acceptance Criteria

```yaml
acceptance_criteria:
  - "All required docs exist."
  - "All schemas exist."
  - "All schemas validate."
  - "docs/schemas/MBO_FEATURE_PACKET.schema.json mirrors the runtime MBO packet schema exactly."
  - "Minimal MBO replay fixture exists."
  - "Tests pass."
  - "The LLM-facing doctrine uses structured ontology, not loose metaphor."
  - "The battlefield analogy is mapped into formal market concepts."
  - "Source-of-truth hierarchy is explicit."
  - "Every hypothesis requires source lineage."
  - "Every feature requires point-in-time policy."
  - "Every feature requires dataset_id and source_tier lineage."
  - "Every model requires validation_card_id and validation_status output."
  - "Narrative-only LLM outputs are treated as invalid."
  - "No new robustness gates are added."
  - "No execution routing changes are added."
  - "Existing HFT3 robustness pipeline remains the authority for promotion/rejection."
```

## Final Rule

Embedded LLM agents must think in this order:

```text
source -> event -> book state -> mechanism -> measurable footprint -> feature -> label -> hypothesis -> validation -> model -> execution survival
```

They must never think in this order:

```text
idea -> model -> backtest -> explanation
```

The purpose of this work is to harden agent cognition, not to add complexity.
The agent should become more structured, less imaginative in output, more
ruthless in validation, and more useful to the existing HFT3 research pipeline.
