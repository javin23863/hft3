# Reference documents

## Algorithmic Trading Strategy Development

**File:** [algorithmic_trading_strategy_development.pdf](algorithmic_trading_strategy_development.pdf)

Authoritative math for the seven PDF structural models (`PDF_MODEL_1` … `PDF_MODEL_7`) in
`features_engine/src/structural_models/`. These models are a **separate inventory** from the
44 `HYP_*` hypothesis families; see [docs/structural_models/PDF_MODELS.md](../structural_models/PDF_MODELS.md).

## HFT Framework Developer Prompt

**File:** [hft_framework_developer_prompt.pdf](hft_framework_developer_prompt.pdf)

Microsecond-tier CPU-bound framework: Transfer Entropy lead-lag (`PDF_MODEL_8`), quantum spread
defense (`PDF_MODEL_9`), stochastic thermodynamics / free energy (`PDF_MODEL_10`), and multivariate
Hawkes toxic flow (`PDF_MODEL_11`). Repo copy also at repo root `hft_framework_developer_prompt.pdf`.

Implementation specs and dependency map:

- [PDF_MODELS.md](../structural_models/PDF_MODELS.md)
- [MODEL_DEPENDENCY_MAP.md](../structural_models/MODEL_DEPENDENCY_MAP.md)

## Ultra-Low Latency Vector Search and HFT Engine Architecture

**File:** [ultra_low_latency_hft_vector_search_architecture.pdf](ultra_low_latency_hft_vector_search_architecture.pdf)

C++ memory architecture authority: zero-allocation hot path, cache alignment, lock-free SPSC queue
(PDF also specifies Disruptor ring buffer — design authority; SPSC only in repo today). Index:
[MEMORY_ARCHITECTURE.md](../workbench/MEMORY_ARCHITECTURE.md).

## Chicago Futures Hot-Memory Universe (market-state layer)

**File:** [chicago_futures_hot_memory_a_plus_developer_prompt.pdf](chicago_futures_hot_memory_a_plus_developer_prompt.pdf)

Market-state authority: HOT/WARM/COLD tiers, instrument registry, VIX/VVIX volatility sensors, promotion
audit, and degradation rules. Runtime config and gap table:
[HOT_MEMORY_UNIVERSE.md](../workbench/HOT_MEMORY_UNIVERSE.md).

## Full PDF bundle (after-action citation index)

| File | Status | Use |
|------|--------|-----|
| [algorithmic_trading_strategy_development.pdf](algorithmic_trading_strategy_development.pdf) | present | PDF_MODEL_1..7 structural specs |
| [hft_framework_developer_prompt.pdf](hft_framework_developer_prompt.pdf) | present | PDF_MODEL_8..11 framework extensions |
| [chicago_cme_microstructure_mathematical_model.pdf](chicago_cme_microstructure_mathematical_model.pdf) | present | Filtration, event-time, ns timestamps |
| [chicago_cme_microstructure_a_plus_developer_handoff.pdf](chicago_cme_microstructure_a_plus_developer_handoff.pdf) | present | Simulation fidelity, matching |
| [chicago_cme_a_plus_production_implementation_prompt.pdf](chicago_cme_a_plus_production_implementation_prompt.pdf) | present | C++ latency, gateway, injection sweep |
| [rithmic_trial_hftbacktest_pipeline_prompt.pdf](rithmic_trial_hftbacktest_pipeline_prompt.pdf) | present | Trial lane quarantine |
| [Ultimate_Quantitative_Finance_Researcher.pdf](Ultimate_Quantitative_Finance_Researcher.pdf) | present | Walk-forward validation |
| [ultra_low_latency_hft_vector_search_architecture.pdf](ultra_low_latency_hft_vector_search_architecture.pdf) | present | C++ memory, lock-free IPC, SIMD/MPHF design |
| [chicago_futures_hot_memory_a_plus_developer_prompt.pdf](chicago_futures_hot_memory_a_plus_developer_prompt.pdf) | present | Market-state HOT/WARM/COLD, instrument registry, sensors |

Field-level mapping: [MANIFEST.md](MANIFEST.md). After-action packets require all charter PDFs in this directory for `pdf_citations_complete: true`.
