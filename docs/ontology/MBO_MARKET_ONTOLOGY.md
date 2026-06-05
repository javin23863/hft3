---
document_type: "ontology"
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

# MBO Market Ontology

| Mental Model Term | Formal Ontology Term | Measurable MBO Object |
| --- | --- | --- |
| Battlefield | Adversarial microstructure environment | venue, instrument, session, regime |
| Terrain | Visible liquidity landscape | bid/ask depth, level structure, book shape |
| Shield | Passive resting liquidity | add events, resting order size, queue depth |
| Shield disappears | Liquidity withdrawal | cancel events, modify-down events |
| Laser strike | Aggressive liquidity-taking | trade events, marketable order flow |
| Hidden shield generator | Hidden/replenishing liquidity | iceberg score, same-level refresh, replenishment |
| Camouflage | Fleeting/deceptive liquidity | add-then-cancel, low order age, high cancel ratio |
| Supply line | Queue position | volume ahead, volume behind, queue rank |
| Breach | Queue depletion | best bid/ask depletion probability |
| Pressure gauge | Order flow imbalance | OFI, normalized OFI, multi-level OFI |
| Reaction speed | Latency budget | event rate, stale probability, latency haircut |
| Trap | Adverse selection | post-fill adverse move probability |
| Commander view | Trade-manager packet | final behavior summary and action context |

```yaml
entities:
  Instrument:
    fields: [symbol, asset_class, venue, tick_size, contract_specs]

  Venue:
    fields: [venue_id, matching_rules, timestamp_source, order_id_semantics]

  MBOEvent:
    fields:
      - event_id
      - sequence_number
      - exchange_timestamp
      - receive_timestamp
      - instrument
      - venue
      - order_id
      - side
      - price
      - size
      - action
      - flags

  Order:
    fields:
      - order_id
      - side
      - price
      - visible_size
      - birth_timestamp
      - last_update_timestamp
      - queue_priority
      - order_age
      - lifecycle_state

  PriceLevel:
    fields:
      - side
      - price
      - level_index
      - total_size
      - order_count
      - average_order_age
      - entropy
      - cancel_rate
      - add_rate
      - trade_rate

  QueueState:
    fields:
      - price
      - side
      - volume_ahead
      - volume_behind
      - queue_rank
      - expected_fill_time
      - depletion_probability

  LiquidityBehavior:
    allowed_values:
      - durable_liquidity
      - fleeting_liquidity
      - replenishing_liquidity
      - absorbing_liquidity
      - liquidity_vacuum
      - queue_breach
      - toxic_flow
      - neutral_flow

  RegimeState:
    allowed_values:
      - calm
      - directional
      - sweep
      - absorption
      - fragile
      - toxic
      - auction
      - news_event
      - unknown

  Feature:
    fields:
      - feature_id
      - feature_family
      - formula
      - required_inputs
      - timestamp_policy
      - source_tier
      - leakage_risk
      - validation_status

  Hypothesis:
    fields:
      - hypothesis_id
      - mechanism
      - expected_footprint
      - required_features
      - falsification_tests
      - asset_scope
      - regime_scope
      - execution_dependency

  ModelCandidate:
    fields:
      - model_id
      - hypothesis_ids
      - feature_ids
      - label_definition
      - validation_results
      - known_failure_modes
      - execution_assumptions
```
