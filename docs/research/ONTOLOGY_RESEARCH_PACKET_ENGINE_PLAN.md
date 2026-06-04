---
title: Ontology Research Packet Engine Plan
intention: Durable planning document for an ontology-governed, evidence-bound research packet compiler.
status: planning
source: Codex attachment c237035a-1b45-4b6a-bda2-6a816ec3806c/pasted-text.txt
---

## Harsh review of the current idea

The idea is directionally correct, but it is **not A+ yet**.

Right now, it still sounds like:

> “Give the LLM normalized data, PDFs, logs, and news, then let it reason.”

That is not strong enough for financial markets.

An A+ version must look more like:

> “Compile every data source into deterministic, schema-validated research packets. The LLM is only allowed to reason over those packets. Every claim, variable, formula, event, timestamp, and market relationship must be typed, sourced, validated, and reproducible.”

The major flaw is not the ontology idea. The flaw is that the **LLM still has too much interpretive freedom**.

You do **not** want the LLM browsing files, loosely retrieving documents, reading news headlines, guessing causal relationships, and then producing an idea. That becomes a messy research assistant. You want a **packetized research compiler** where the LLM receives the same structured object every cycle.

---

# The core correction

Your system needs three separate layers:

```text
1. Raw Source Layer
   L3 market data, macro data, PDFs, GitHub repo, news feed, filings, events

2. Deterministic Compiler Layer
   Normalizes, validates, timestamps, deduplicates, maps entities, builds structured packets

3. LLM Research Layer
   Reads only approved packets, proposes hypotheses, writes sandbox tests, emits structured outputs
```

The LLM should **not** organize the world.

The system should organize the world **before** the LLM sees it.

That is the A+ distinction.

---

# Critical failure modes in the current plan

## 1. “RAG” is not enough

RAG alone does not prevent hallucination. It only gives the LLM more text to possibly misuse.

The correct design is:

```text
RAG retrieval → source validation → concept extraction → ontology mapping → formula registry lookup → packet compilation → LLM reasoning
```

The LLM should not be allowed to say:

> “This looks like a basis arbitrage opportunity.”

It should only be allowed to say that if the packet already contains:

```json
{
  "concept": "basis_arbitrage",
  "source_id": "approved_quant_source_017",
  "formula_id": "basis_formula_v3",
  "required_variables_available": true,
  "data_validation_status": "passed"
}
```

---

## 2. News cannot be fed in as “news”

A live world-events feed is dangerous if it is just dumped into context.

Raw news is:

* duplicated
* contradictory
* delayed
* revised
* vague
* politically biased
* often irrelevant to tradable instruments
* easy for an LLM to overinterpret

The news feed must be converted into a **Canonical Event Packet** before the research model sees it.

The LLM should not see:

```text
"Oil jumps as Middle East tensions rise..."
```

It should see something like:

```json
{
  "event_id": "evt_20260604_001923",
  "event_type": "geopolitical_supply_risk",
  "event_time_utc": "2026-06-04T13:42:11Z",
  "first_seen_utc": "2026-06-04T13:43:02Z",
  "sources": [
    {
      "source_id": "newswire_001",
      "reliability_score": 0.91,
      "headline_hash": "abc123",
      "timestamp_utc": "2026-06-04T13:43:02Z"
    }
  ],
  "entities": [
    {
      "entity": "crude_oil",
      "entity_type": "commodity",
      "mapping_confidence": 0.94
    },
    {
      "entity": "middle_east",
      "entity_type": "region",
      "mapping_confidence": 0.88
    }
  ],
  "market_channels": [
    "energy_supply_risk",
    "inflation_expectation",
    "risk_off",
    "usd_strength"
  ],
  "affected_instruments_candidate": [
    "CL",
    "BZ",
    "USO",
    "XLE",
    "ES",
    "NQ",
    "DXY",
    "Treasury_futures"
  ],
  "novelty_score": 0.73,
  "contradiction_score": 0.12,
  "staleness_seconds": 51,
  "tradability_status": "research_candidate",
  "llm_allowed_interpretation": "The event may justify testing short-horizon changes in energy futures liquidity, cross-asset lead-lag, volatility, and risk-off behavior. It does not prove direction."
}
```

That is the difference between a hobby system and an institutional research system.

---

## 3. “If edge is negative, refine and retest” creates p-hacking

This line is dangerous:

> “If the edge is negative, it refines the code and re-tests.”

That can easily become automated overfitting.

Replace it with:

```text
The system may refine implementation errors, but it may not repeatedly mutate the hypothesis until it finds significance.
```

Every hypothesis needs:

* pre-registration
* hypothesis ID
* fixed variables
* fixed test window
* fixed rejection criteria
* maximum retry count
* multiple-testing adjustment
* failed-test logging

Otherwise the LLM will accidentally data-mine.

---

## 4. AlphaGeometry must not be misused

AlphaGeometry or formal reasoning tools can help with **logic validation**, but they do not prove market edges.

Correct use:

```text
Validate symbolic consistency, variable definitions, dimensional compatibility, formula transformations, and dependency chains.
```

Incorrect use:

```text
Use AlphaGeometry to prove that a trading strategy works.
```

Your developer needs to understand this clearly.

The formal layer should ask:

```text
Are the variables defined?
Are the units compatible?
Is the timestamp alignment valid?
Is the formula internally consistent?
Is the causal claim supported?
Is the transformation mathematically legal?
```

It should not pretend to prove alpha.

---

## 5. The final output should not be “raw Python logic”

Raw Python is not a safe model handoff format.

The final output should include the Python script as an audit artifact, but the risk layer should consume a **model specification**, not arbitrary code.

Use:

```json
{
  "entry_condition": {
    "type": "threshold_cross",
    "variable": "cointegration_residual_zscore",
    "operator": "<",
    "value": -2.1
  }
}
```

Not only:

```python
if zscore < -2.1:
    buy()
```

The risk layer needs typed logic, not code blobs.

---

# A+ architecture

The refined architecture should be:

```text
Raw feeds
    ↓
Source registry
    ↓
Entity resolution
    ↓
Ontology mapping
    ↓
Event compiler
    ↓
Market-state compiler
    ↓
Research packet builder
    ↓
LLM hypothesis engine
    ↓
Sandbox validation
    ↓
Model behavior profiler
    ↓
Risk-manager handoff packet
```

The core object is the **Research Decision Packet**.

Every cycle, the LLM sees the same type of object.

Not files.

Not loose text.

Not scattered PDFs.

Not raw headlines.

A structured packet.

---

# The new central concept

## Research Decision Packet

The packet should be the only input the LLM is allowed to use for research decisions.

It should contain:

```text
1. Packet metadata
2. Market state
3. L3 order-book state
4. Cross-asset state
5. Macro state
6. Live event state
7. Source evidence
8. Allowed formulas
9. Allowed variables
10. Candidate hypotheses
11. Required validation tests
12. Rejection gates
13. Risk-layer handoff requirements
14. Audit metadata
```

All keys must always exist.

Optional values should be `null`, not missing.

This matters because consistency reduces LLM confusion.

---

# Refined developer prompt

Copy this to your developer.

````markdown
# Development Prompt: Ontology-Governed Research Packet Engine for Multi-Asset Quant Research

## Objective

Harden the existing local agentic quantitative research system into a deterministic, ontology-governed, evidence-bound research engine.

The LLM must not operate on scattered files, raw news, raw PDFs, raw market feeds, or loosely structured context.

The system must compile every input into a standardized Research Decision Packet before the LLM is allowed to reason.

Execution routing remains out of scope.

The system is responsible for:

1. Ingesting real-time L3 market data, historical training data, macro data, approved PDFs, approved mathematical sources, AlphaGeometry/formal reasoning repo outputs, and live world-event/news feeds.
2. Normalizing all information into strict schemas.
3. Mapping all fields to a formal financial-market ontology.
4. Building identical structured packets for the LLM every research cycle.
5. Forcing every hypothesis, variable, formula, event interpretation, and statistical conclusion to trace back to approved evidence.
6. Running sandboxed statistical validation.
7. Emitting machine-readable model behavior packets for the existing risk layer.

The LLM is not the source of truth.

The source of truth is:

- validated L3/training data
- approved quantitative finance documents
- approved mathematical documents
- formal formula/variable registry
- approved event ontology
- sandboxed Python output
- audit logs
- versioned source registry

If something cannot be traced, validated, or reproduced, the system must reject it.

---

# 1. Hard Architectural Rule

The LLM must never directly browse or reason over scattered files.

Forbidden workflow:

```text
LLM reads files → LLM reads news → LLM reads data → LLM invents hypothesis
````

Required workflow:

```text
raw sources
→ deterministic ingestion
→ entity resolution
→ ontology mapping
→ source validation
→ event compilation
→ market-state compilation
→ Research Decision Packet
→ LLM hypothesis generation
→ sandbox validation
→ behavior packet
→ risk-layer handoff
```

The Research Decision Packet is the only research input the LLM may use.

---

# 2. Source Layers

Build ingestion adapters for the following source classes:

## Market Data Sources

* Real-time L3 order book data
* Historical L3 training data
* Futures data
* Equities data if available
* Options data if available
* Crypto spot/perpetual data if available
* Macro and fixed-income time series
* Rates, yields, funding, currency indices

## Knowledge Sources

* Approved quantitative finance PDFs
* Approved mathematical PDFs
* Internal research papers
* Data schemas
* Existing model documentation
* AlphaGeometry/formal reasoning repo already inside the codebase

## Live Event Sources

* Real-time news feed
* Macro release feed
* Central bank communications
* Government/policy events
* Geopolitical events
* Weather/supply-chain events if available
* Earnings/corporate events if available
* Regulatory events
* Market-structure events

Raw event feeds must not be passed to the LLM directly.

They must be compiled into canonical event packets.

---

# 3. Canonical Event Ontology

Create an event ontology for live world information.

Required event classes:

* macro_release
* central_bank_policy
* inflation_event
* employment_event
* treasury_auction
* geopolitical_risk
* military_conflict
* sanctions_event
* energy_supply_event
* weather_disruption
* shipping_logistics_event
* corporate_earnings
* corporate_guidance
* regulatory_event
* exchange_outage
* liquidity_event
* volatility_event
* credit_event
* banking_stress_event
* crypto_exchange_event
* digital_asset_protocol_event
* options_expiration_event
* futures_roll_event

Each event must be normalized into this structure:

```json
{
  "event_id": "string",
  "event_type": "string",
  "event_subtype": "string",
  "event_time_utc": "timestamp|null",
  "first_seen_utc": "timestamp",
  "last_updated_utc": "timestamp",
  "source_count": "integer",
  "sources": [
    {
      "source_id": "string",
      "source_type": "newswire|filing|calendar|official_release|market_data|internal_feed",
      "headline_or_description_hash": "string",
      "published_at_utc": "timestamp",
      "received_at_utc": "timestamp",
      "reliability_score": "float",
      "is_primary_source": "boolean"
    }
  ],
  "entities": [
    {
      "entity_id": "string",
      "entity_name": "string",
      "entity_type": "country|company|commodity|currency|rate|index|future|equity|crypto|sector|region",
      "mapping_confidence": "float"
    }
  ],
  "market_channels": [
    "risk_on_off",
    "rates",
    "inflation",
    "growth",
    "energy_supply",
    "credit",
    "liquidity",
    "volatility",
    "currency",
    "sector_rotation",
    "funding",
    "basis",
    "microstructure"
  ],
  "affected_instruments_candidate": ["string"],
  "directional_claim_allowed": false,
  "novelty_score": "float",
  "severity_score": "float",
  "certainty_score": "float",
  "contradiction_score": "float",
  "staleness_seconds": "integer",
  "dedup_cluster_id": "string",
  "tradability_status": "ignore|monitor|research_candidate|urgent_research_candidate",
  "allowed_llm_interpretation": "string",
  "forbidden_llm_interpretations": ["string"]
}
```

The LLM may not infer directional trades directly from event text.

The event packet may only authorize research questions such as:

* Did liquidity change after the event?
* Did spreads widen?
* Did volatility change?
* Did cross-asset lead-lag behavior appear?
* Did basis dislocate?
* Did order-book imbalance become predictive?
* Did correlation structure break?
* Did regime classification change?

---

# 4. Entity Resolution Layer

Implement deterministic entity resolution before event packets are created.

The same entity must always map to the same canonical ID.

Examples:

```text
"Fed", "Federal Reserve", "FOMC", "Powell" 
→ entity_id: US_FEDERAL_RESERVE

"WTI", "Crude Oil", "CL", "NYMEX Light Sweet Crude"
→ entity_id: WTI_CRUDE_OIL

"Nasdaq", "NQ", "MNQ", "Nasdaq futures"
→ separate but linked entities:
   NASDAQ_100_INDEX
   NQ_FUTURE
   MNQ_FUTURE
```

Entity resolution output must include confidence.

Low-confidence mappings must be rejected or quarantined.

---

# 5. Market State Packet

Create a normalized market state packet from L3 and cross-asset data.

Required structure:

```json
{
  "market_state_id": "string",
  "as_of_utc": "timestamp",
  "lookback_window": "string",
  "instruments": [
    {
      "symbol": "string",
      "canonical_instrument_id": "string",
      "asset_class": "string",
      "venue": "string",
      "session_state": "open|closed|pre_market|post_market|halted|null",
      "mid_price": "float|null",
      "spread": "float|null",
      "spread_bps": "float|null",
      "top_of_book_imbalance": "float|null",
      "depth_imbalance_10": "float|null",
      "order_flow_imbalance": "float|null",
      "microprice": "float|null",
      "realized_volatility": "float|null",
      "book_update_rate": "float|null",
      "trade_rate": "float|null",
      "cancel_rate": "float|null",
      "toxicity_metric": "float|null",
      "liquidity_regime": "normal|thin|stressed|unknown",
      "volatility_regime": "low|normal|high|extreme|unknown",
      "data_quality_status": "passed|failed|degraded"
    }
  ],
  "cross_asset_features": {
    "basis_spreads": [],
    "rolling_correlations": [],
    "cointegration_residuals": [],
    "lead_lag_candidates": [],
    "funding_rate_features": [],
    "macro_alignment_features": []
  },
  "data_validation": {
    "status": "passed|failed",
    "failed_checks": [],
    "warnings": []
  }
}
```

The LLM should never calculate these from raw data inside its context.

The system should calculate them before packet creation.

---

# 6. Research Decision Packet

Every LLM research cycle must receive one and only one packet type:

```json
{
  "packet_schema_version": "research_decision_packet_v1",
  "packet_id": "string",
  "created_at_utc": "timestamp",
  "decision_context": {
    "mode": "research|validation|monitoring",
    "allowed_actions": [
      "generate_hypothesis",
      "write_validation_code",
      "interpret_sandbox_result",
      "reject_hypothesis"
    ],
    "forbidden_actions": [
      "invent_variable",
      "invent_formula",
      "infer_trade_direction_from_news_only",
      "skip_validation",
      "modify_hypothesis_after_failure_without_audit"
    ]
  },
  "market_state": {},
  "event_state": {
    "active_events": [],
    "event_clusters": [],
    "ignored_events": [],
    "contradictory_events": []
  },
  "knowledge_state": {
    "approved_sources_retrieved": [],
    "formulas_available": [],
    "concepts_available": [],
    "source_gaps": []
  },
  "ontology_state": {
    "allowed_entities": [],
    "allowed_variables": [],
    "allowed_formulas": [],
    "allowed_transformations": [],
    "forbidden_variables": []
  },
  "candidate_research_questions": [
    {
      "question_id": "string",
      "question": "string",
      "trigger_source": "market_state|event_state|knowledge_state",
      "required_variables": [],
      "required_sources": [],
      "testability_status": "testable|not_testable|insufficient_data"
    }
  ],
  "validation_requirements": {
    "required_tests": [],
    "minimum_sample_size": "integer|null",
    "walk_forward_required": true,
    "out_of_sample_required": true,
    "multiple_testing_correction_required": true,
    "regime_break_test_required": true,
    "liquidity_conditioning_required": true
  },
  "risk_handoff_requirements": {
    "must_emit_typed_logic": true,
    "must_emit_failure_conditions": true,
    "must_emit_regime_sensitivity": true,
    "must_emit_liquidity_sensitivity": true,
    "must_emit_latency_sensitivity": true
  },
  "audit": {
    "data_snapshot_ids": [],
    "source_registry_version": "string",
    "ontology_version": "string",
    "code_commit": "string"
  }
}
```

All keys must always exist.

Optional values must be `null`, empty arrays, or explicit `"unknown"` values.

No missing keys.

---

# 7. Hypothesis Generation Rules

The LLM may only generate a hypothesis if the packet contains:

1. Valid market data.
2. Valid event or market trigger.
3. Approved ontology variables.
4. Approved source documents.
5. Approved formula definitions.
6. Defined rejection conditions.
7. Testable sample window.

The LLM output must follow this structure:

```json
{
  "hypothesis_id": "string",
  "status": "proposed|rejected",
  "hypothesis_text": "string",
  "trigger": {
    "trigger_type": "market_state|event_state|cross_asset_state|macro_state",
    "trigger_ids": []
  },
  "market_mechanism": "string",
  "required_variables": [],
  "required_formulas": [],
  "source_evidence": [
    {
      "source_id": "string",
      "concept_supported": "string",
      "page_or_section": "string"
    }
  ],
  "test_plan": {
    "method": "string",
    "sample_period": {},
    "in_sample_period": {},
    "out_of_sample_period": {},
    "walk_forward_plan": {},
    "null_hypothesis": "string",
    "alternative_hypothesis": "string",
    "rejection_criteria": []
  },
  "anti_p_hacking_controls": {
    "pre_registered": true,
    "max_code_retries": 2,
    "hypothesis_mutation_allowed": false,
    "multiple_testing_correction": "required"
  }
}
```

If required fields are unavailable, the LLM must reject the hypothesis.

---

# 8. News/Event Interpretation Rules

The system must enforce the following:

## Allowed

The LLM may say:

```text
This event is a candidate trigger for testing whether liquidity, volatility, basis, spread, correlation, or lead-lag behavior changed after the event.
```

## Forbidden

The LLM may not say:

```text
This news means crude oil will go up.
This headline is bullish for equities.
This event proves a short signal.
```

Directional conclusions require market-data validation.

News creates research candidates.

News does not create truth.

---

# 9. Formal Math / AlphaGeometry Guardrail

Use the AlphaGeometry/formal reasoning repo as a mathematical consistency guardrail where applicable.

Required checks:

* variable existence
* formula definition
* unit compatibility
* timestamp compatibility
* transformation validity
* dependency graph validity
* no undefined intermediate terms
* no circular definitions
* no asset-class mismatch
* no currency mismatch
* no contract mismatch

Example:

```text
basis = futures_price - spot_price
```

Before allowing this formula, validate:

* futures_price exists
* spot_price exists
* both are mapped to comparable underlying exposure
* timestamps are aligned
* currencies match or FX adjustment is explicit
* contract expiry is known
* carry/funding adjustment is explicit if used
* units are compatible

AlphaGeometry/formal reasoning is not allowed to certify alpha.

It only certifies mathematical consistency.

---

# 10. Statistical Validation Rules

The sandbox result is the authority.

The LLM may not report any metric unless Python calculated it.

Required validation outputs:

```json
{
  "sandbox_run_id": "string",
  "hypothesis_id": "string",
  "status": "pass|fail|error",
  "metrics": {
    "expected_value": "float|null",
    "t_stat": "float|null",
    "p_value": "float|null",
    "confidence_interval": "object|null",
    "sharpe_ratio": "float|null",
    "sortino_ratio": "float|null",
    "max_drawdown": "float|null",
    "turnover": "float|null",
    "capacity_estimate": "float|null",
    "decay_half_life": "float|null",
    "hit_rate": "float|null",
    "payoff_ratio": "float|null",
    "skew": "float|null",
    "kurtosis": "float|null"
  },
  "diagnostics": {
    "stationarity_test": "object|null",
    "cointegration_test": "object|null",
    "autocorrelation_test": "object|null",
    "heteroskedasticity_test": "object|null",
    "regime_break_test": "object|null",
    "multiple_testing_adjustment": "object|null"
  },
  "data_used": {
    "snapshot_ids": [],
    "sample_start_utc": "timestamp",
    "sample_end_utc": "timestamp",
    "rows_used": "integer",
    "rows_rejected": "integer"
  },
  "code_artifacts": {
    "script_path": "string",
    "script_hash": "string",
    "environment_hash": "string"
  }
}
```

If a metric was not calculated, it must be `null`.

The LLM may not fill it in.

---

# 11. Model Behavior Packet

After validation, create a model behavior packet for the existing risk layer.

The risk layer should consume typed behavior, not raw Python.

Required output:

```json
{
  "model_id": "string",
  "hypothesis_id": "string",
  "status": "validated|rejected|research_only",
  "model_spec": {
    "entry_logic": [
      {
        "condition_id": "string",
        "variable": "string",
        "operator": ">|<|>=|<=|==|crosses_above|crosses_below",
        "threshold": "float|string",
        "lookback_window": "string",
        "source_formula_id": "string"
      }
    ],
    "exit_logic": [],
    "sizing_logic": {
      "method": "fixed_fraction|vol_target|risk_budget|none",
      "parameters": {}
    },
    "allowed_instruments": [],
    "forbidden_instruments": []
  },
  "behavior_profile": {
    "regime_sensitivity": {},
    "liquidity_sensitivity": {},
    "volatility_sensitivity": {},
    "latency_sensitivity": {},
    "event_sensitivity": {},
    "failure_conditions": [],
    "kill_conditions": [],
    "known_blind_spots": []
  },
  "validation_summary": {
    "sample_period": {},
    "methods_used": [],
    "metrics": {},
    "diagnostics": {},
    "robustness_tests": {},
    "out_of_sample_result": {}
  },
  "risk_layer_notes": {
    "confidence_level": "float|null",
    "deployment_recommendation": "reject|research_only|paper_trade_candidate",
    "reason": "string"
  },
  "audit": {
    "packet_id": "string",
    "ontology_version": "string",
    "source_registry_version": "string",
    "data_snapshot_ids": [],
    "sandbox_run_id": "string",
    "code_commit": "string",
    "created_at_utc": "timestamp"
  }
}
```

Raw Python may be attached as an audit artifact, but the risk layer should not be required to parse arbitrary Python.

---

# 12. Anti-Hallucination Gates

Implement these as hard blockers.

## Gate 1: Packet Gate

Reject if the LLM input is not a valid Research Decision Packet.

## Gate 2: Ontology Gate

Reject if any variable, entity, formula, instrument, or event type is not defined in the ontology.

## Gate 3: Source Gate

Reject if any formula or market concept lacks approved source support.

## Gate 4: Data Gate

Reject if required data is missing, stale, invalid, duplicated, or not point-in-time safe.

## Gate 5: Event Gate

Reject if a live event cannot be deduplicated, timestamped, entity-mapped, or confidence-scored.

## Gate 6: Temporal Gate

Reject if event time, publish time, receive time, market-data time, and decision time are not ordered correctly.

## Gate 7: Math Gate

Reject if formulas are dimensionally inconsistent, undefined, circular, or mismatched across assets.

## Gate 8: Code Gate

Reject if sandbox code fails, produces non-deterministic output, or omits required result files.

## Gate 9: Statistical Gate

Reject if the hypothesis does not survive required validation.

## Gate 10: P-Hacking Gate

Reject if the hypothesis was repeatedly mutated after failure without audit approval.

## Gate 11: Behavior Gate

Reject if model behavior cannot be summarized across regime, liquidity, volatility, latency, and event sensitivity.

## Gate 12: Audit Gate

Reject if the full lineage cannot be reconstructed.

---

# 13. Required Developer Deliverables

The implementation is not complete until the following exist:

1. Formal ontology schema.
2. Source-of-truth registry.
3. Formula registry.
4. Entity registry.
5. Event ontology.
6. Entity-resolution engine.
7. News/event deduplication engine.
8. Event-confidence scoring engine.
9. Market-state compiler.
10. Research Decision Packet builder.
11. Packet schema validator.
12. RAG citation enforcement.
13. Formal math consistency checker.
14. Data validation layer.
15. Sandbox statistical test runner.
16. Hypothesis generator constrained by packet schema.
17. Anti-p-hacking controls.
18. Model behavior packet writer.
19. Risk-layer handoff writer.
20. Full audit logger.
21. Unit tests for every gate.
22. Integration test using real L3 data and live-event sample data.
23. Failure tests proving hallucinated variables, unsupported claims, stale events, bad timestamps, and missing formulas are rejected.

---

# 14. Acceptance Criteria

The system is not accepted unless all of the following are true:

1. The LLM never sees scattered raw files as its primary research input.
2. Every research cycle uses the same Research Decision Packet schema.
3. Raw news is converted into canonical event packets before LLM exposure.
4. The LLM cannot infer directional trades from news alone.
5. Every variable is ontology-defined.
6. Every formula is source-backed and registry-approved.
7. Every event is timestamped, deduplicated, entity-mapped, and confidence-scored.
8. Every hypothesis is pre-registered before testing.
9. Every statistical result comes from sandbox output, not LLM interpretation.
10. Failed hypotheses remain logged and cannot be silently mutated.
11. Every accepted model has a full audit trail.
12. Every rejected model has a machine-readable rejection reason.
13. The final risk-layer handoff is typed JSON, not free-form prose.
14. The system can prove, through tests, that hallucinated variables and unsupported claims are rejected.

```

---

# The clean version of the idea

The thing you are building should not be described as:

> “An LLM research agent with RAG and live news.”

That is too weak.

Describe it this way:

> **An ontology-governed market intelligence compiler that converts L3 data, training data, approved quant/math sources, formal reasoning outputs, and live world events into standardized Research Decision Packets. The LLM only reasons over those packets, and every hypothesis must be source-backed, mathematically valid, point-in-time safe, sandbox-tested, behavior-profiled, and audit-reproducible before it can be handed to the existing risk layer.**

That is the sharper concept.

---

# Final grader verdict

Current version: **B-/C+**

Reason: the direction is good, but it still trusts the LLM too much and treats RAG/news/data as things the LLM can browse and organize.

A+ version: **packetized, deterministic, ontology-first, evidence-bound, source-registered, event-aware, and audit-reproducible.**

The strongest implementation principle is:

> **The LLM does not organize information. The system organizes information into validated packets, and the LLM reasons only inside those boundaries.**
```
