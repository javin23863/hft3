---
document_type: "source_of_truth_policy"
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

# Source Of Truth Policy

```yaml
source_truth_hierarchy:
  tier_0_reality:
    description: "Direct observed market reality."
    examples:
      - executed trades
      - exchange-native order messages
      - official exchange timestamps
      - raw MBO events
      - raw MBP events

  tier_1_primary:
    description: "Primary official sources."
    examples:
      - exchange feeds
      - exchange rulebooks
      - SEC filings
      - EDGAR
      - FRED
      - BLS
      - Treasury
      - CME/Nasdaq/NYSE official data

  tier_2_vendor_normalized:
    description: "Vendor-normalized market data derived from primary sources."
    examples:
      - Databento
      - Polygon
      - OptionMetrics
      - Sharadar
      - AlgoSeek
      - TickData
      - NxCore

  tier_3_interpretive:
    description: "Research and interpretation."
    examples:
      - academic papers
      - analyst reports
      - news articles
      - broker research

  tier_4_untrusted_context:
    description: "Weak or noisy context."
    examples:
      - social media
      - blogs
      - forums
      - commentary
      - unsourced claims
```

```yaml
source_policy:
  - "Tier 0 and Tier 1 may define labels, events, and ground truth."
  - "Tier 2 may be used if lineage to Tier 0 or Tier 1 is known."
  - "Tier 3 may generate hypotheses but cannot define ground truth alone."
  - "Tier 4 may only be used as context and must never define labels or model targets."
  - "All features must carry source_tier and dataset_id."
  - "All labels must carry timestamp_policy and leakage_review_status."
  - "No LLM-generated claim is a source of truth."
```
