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

> **Parser note:** The citation parser captures 17 rows (those with `**present**` or `**absent**` in the Present column). Row `live_topology` uses `repo doc` and is excluded from automated on-disk checks. Visual row count: 18.
>
> **Field classification:** Rows 1–5 (`event_context` through `simulation_fidelity`) are materialized as top-level packet fields. Rows 6–18 are concept-only citations — the codebase modules/configs exist but are not serialized into the AAR packet.

## Authority copies

Charter PDFs live in this directory (`docs/references/`). Repo-root copies remain for legacy links; after-action citation checks use `docs/references/` only.
