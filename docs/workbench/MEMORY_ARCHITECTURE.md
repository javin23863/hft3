# Workbench memory architecture

Python is allowed for **research, orchestration, visualization, parameter sweeps, and dashboarding**.

Python runtime must **not** be the source of truth for C++ hot-path memory layout, allocation, or lock-free concurrency when the live path is C++.

**Authority PDF:** [ultra_low_latency_hft_vector_search_architecture.pdf](../references/ultra_low_latency_hft_vector_search_architecture.pdf)

Latency measurement authority is separate: [LATENCY_ARCHITECTURE.md](LATENCY_ARCHITECTURE.md).

## PDF section → hft3 mapping

| PDF section | hft3 anchor | Status |
|-------------|-------------|--------|
| §4.1 Ban heap on hot path | `DecisionEngine::evaluate_actions` + pre-allocated `weights_` in [decision_engine/cpp/include/decision_runtime.hpp](../../decision_engine/cpp/include/decision_runtime.hpp) | **implemented** |
| §4.2 False sharing / cache alignment | `SPSCQueue` `pad0_`/`pad1_`, `MarketState alignas(64)` in [rithmic_gateway/include/spsc_queue.hpp](../../rithmic_gateway/include/spsc_queue.hpp) and `decision_runtime.hpp` | **implemented** |
| §4.4 Lock-free SPSC queue | `SPSCQueue`, stack check `spsc_queue_roundtrip` in [tools/research_sim_main.cpp](../../tools/research_sim_main.cpp) | **implemented** |
| §4.4 Disruptor ring buffer (full pattern) | — | **not implemented** |
| §4.3 Cache warming / prefetch | — | **not implemented** |
| §5 MPHF | — | **not implemented** (future lookup layer) |
| §6 AVX-512 vector SIMD | — | **not in features_engine C++ today** |
| §2–3 Hardware / kernel tuning | CHI404 bare metal ([BLUEPRINT.md](../../BLUEPRINT.md) §4) supersedes AMD-specific prose | **advisory only** |

## Runtime verification gate

Stack self-test validates memory/concurrency patterns on the C++ path:

| Check | Contract | Module |
|-------|----------|--------|
| `spsc_queue_roundtrip` | [data_layer/stack_check_contract.py](../../data_layer/stack_check_contract.py) | [workbench/src/sim/cpp_stack_verify.py](../../workbench/src/sim/cpp_stack_verify.py) → `hft_research_sim --verify-stack` |
| `decision_evaluate` | same | zero-allocation `evaluate_actions` path |
| `gateway_init` | same | Rithmic adapter init (no heap on hot path) |

See [HOT_PATH_AUDIT.md](HOT_PATH_AUDIT.md) for CMake targets and topology.

## Reviewer citations

Pass B memory/concurrency/SIMD disputes cite `ultra_low_latency_hft_vector_search_architecture.pdf` + section (see [REVIEWER_CHARTER.md](../REVIEWER_CHARTER.md)).

After-action packets include these concepts via [MANIFEST.md](../references/MANIFEST.md): `cpp_memory_optimization`, `lock_free_ipc`, `simd_vector_search`.
