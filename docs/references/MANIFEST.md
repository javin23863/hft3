# PDF citation manifest (after-action packet fields)

Each packet field cites a source PDF section. Missing PDFs on disk → `pdf_citations_complete: false` → LLM narrative skipped.

| Packet field / concept | PDF | Section | Present |
|------------------------|-----|---------|---------|
| `event_context` | `chicago_cme_microstructure_mathematical_model.pdf` | Event-time filtration | **present** |
| `per_trade_audit` | `chicago_cme_microstructure_mathematical_model.pdf` | Timestamp discipline | **present** |
| `latency_authority` | `chicago_cme_a_plus_production_implementation_prompt.pdf` | Gateway latency | **present** |
| `injection_sweep` | `chicago_cme_a_plus_production_implementation_prompt.pdf` | Injection sweep | **present** |
| `simulation_fidelity` | `chicago_cme_microstructure_a_plus_developer_handoff.pdf` | Replay fidelity | **present** |
| `rithmic_trial_quarantine` | `rithmic_trial_hftbacktest_pipeline_prompt.pdf` | Trial lane | **present** |
| `walk_forward_validation` | `Ultimate_Quantitative_Finance_Researcher.pdf` | Validation | **present** |
| `structural_models` | `algorithmic_trading_strategy_development.pdf` | Ch. structural | **present** |
| `framework_extensions` | `hft_framework_developer_prompt.pdf` | Framework prompt | **present** |
| `cpp_memory_optimization` | `ultra_low_latency_hft_vector_search_architecture.pdf` | §4 Memory architecture | **present** |
| `lock_free_ipc` | `ultra_low_latency_hft_vector_search_architecture.pdf` | §4.4 Disruptor / SPSC | **present** |
| `simd_vector_search` | `ultra_low_latency_hft_vector_search_architecture.pdf` | §6 AVX-512 (design authority) | **present** |
| `hot_memory_universe` | `chicago_futures_hot_memory_a_plus_developer_prompt.pdf` | Phase 2 — Required HOT Universe | **present** |
| `instrument_registry` | `chicago_futures_hot_memory_a_plus_developer_prompt.pdf` | Phase 1 — Instrument Registry | **present** |
| `volatility_sensor_layer` | `chicago_futures_hot_memory_a_plus_developer_prompt.pdf` | HOT_SENSOR / VIX-VVIX-VX separation | **present** |
| `hot_memory_degradation` | `chicago_futures_hot_memory_a_plus_developer_prompt.pdf` | Phase 7 — Memory and Degradation Rules | **present** |
| `live_topology` | `BLUEPRINT.md` | Live architecture §4 | repo doc |
| `autoresearch_pipeline` | `dev_instructions.pdf` | Pipeline overview §1–6 | **present** |

## hft3 ontology extensions (ODL object types)

The hft3 OpenFoundry domain pack at `integrations/openfoundry/domain-packs/hft3/` declares 9 object types. Each has a sidecar citation under `citations/<Type>.yaml`; the table below mirrors those sidecars. See [`docs/research/ONTOLOGY_CITATIONS.md`](../research/ONTOLOGY_CITATIONS.md) for the rationale and ground section for every type.

| ODL extension | PDF | Section | Page | Sidecar present |
|---------------|-----|---------|------|-----------------|
| `MarkedMicroEvent` | `chicago_cme_microstructure_mathematical_model.pdf` | §4 MBO Marked Point Process | 2 | **present** |
| `BookSnapshotAtDecision` | `chicago_cme_microstructure_mathematical_model.pdf` | §3 Limit Order Book | 1 | **present** |
| `QueuePositionEstimate` | `chicago_cme_microstructure_mathematical_model.pdf` | §6 Queue / Fill model | 3 | **present** |
| `LatencyChainUs` | `chicago_cme_microstructure_mathematical_model.pdf` | §4 MBO Marked Point Process | 2 | **present** |
| `CppLatencyBudget` | `chicago_cme_microstructure_mathematical_model.pdf` | §19 Validation framework | 7 | **present** |
| `InjectionSweepResult` | `chicago_cme_microstructure_mathematical_model.pdf` | §10 Optimization | 5 | **present** |
| `StrategySignal` | `chicago_cme_microstructure_mathematical_model.pdf` | §8 Action space | 4 | **present** |
| `FillOutcome` | `chicago_cme_microstructure_mathematical_model.pdf` | §6 Queue / Fill model | 3 | **present** |
| `EventContext` | `chicago_cme_microstructure_mathematical_model.pdf` | §1 Information set (also §15, §17) | 1 | **present** |

> **Parser note (post phase 4):** The packet-field citation parser still captures 17 rows in the first table (rows 1–5 + 6–17 are auto-parsed; row 18 `live_topology` is `repo doc` and excluded from on-disk checks). The ODL-extension table adds 9 rows; the full visual row count is now 18 + 9 = 27.
>
> **Field classification (post phase 4):** The first 5 packet-field rows (1–5) are materialized as top-level packet fields; rows 6–17 are concept-only; row 18 is a repo-doc cite. The new ODL-extension rows (19–27) are schema entities, not packet fields — they are loaded by `validate_ontology_citations()` (planned phase 5) and used as the closure set for closed-claim LLM `kg_annotations[].source_type=ONTOLOGY_EXTENSION` (planned phase 7).
>
> **Sidecar rule:** Every ODL extension row in this table must have a `citations/<Type>.yaml` sidecar file with non-empty `primary.{pdf,section,page}` and at least one `claims[]` entry. The validator fails the connector if a sidecar is missing or stale.

## Authority copies

Charter PDFs live in this directory (`docs/references/`). Repo-root copies remain for legacy links; after-action citation checks use `docs/references/` only.
