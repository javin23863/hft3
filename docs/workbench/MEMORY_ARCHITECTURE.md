# Workbench memory architecture

## Dual authority index

| Concern | Authority PDF | hft3 doc |
|---------|---------------|----------|
| Market-state HOT/WARM/COLD, instrument registry, VIX/VVIX sensors, degradation | [chicago_futures_hot_memory_a_plus_developer_prompt.pdf](../references/chicago_futures_hot_memory_a_plus_developer_prompt.pdf) | [HOT_MEMORY_UNIVERSE.md](HOT_MEMORY_UNIVERSE.md) |
| C++ zero-allocation, SPSC, SIMD design | [ultra_low_latency_hft_vector_search_architecture.pdf](../references/ultra_low_latency_hft_vector_search_architecture.pdf) | This document (§C++ mapping below) |
| Colo kernel idle / GRUB gap-fill | ultra PDF §2–3 (advisory) | [MEMORY_UPGRADE.md](../chi404/MEMORY_UPGRADE.md) |

Python is allowed for **research, orchestration, visualization, parameter sweeps, and dashboarding**.

Python runtime must **not** be the source of truth for C++ hot-path memory layout, allocation, or lock-free concurrency when the live path is C++.

**Supplementary reference PDF:** [ultra_low_latency_hft_vector_search_architecture.pdf](../references/ultra_low_latency_hft_vector_search_architecture.pdf)

This PDF **supplements** memory-optimization design and review — cite it when planning or auditing C++ hot-path upgrades. It is **not** a blanket mandate to rewrite existing production paths; hft3 implements what is mapped below and defers optional PDF sections until an explicit upgrade task says otherwise.

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
| §2–3 Hardware / kernel tuning | CHI404 gap-fill runbook [MEMORY_UPGRADE.md](../chi404/MEMORY_UPGRADE.md) | **partial on CHI404** — gap-fill only (restore point + append missing PDF §3 tokens); does not re-run full tuning |

### CHI404 PDF §2–3 gap status

| Item | Full tuning (`01_kernel_tuning.sh`) | Memory gap-fill (`12_memory_gap_fill.sh`) |
|------|-------------------------------------|---------------------------------------------|
| `isolcpus`, `nohz_full`, `rcu_nocbs` | already applied | skip (do not redo) |
| `processor.max_cstate=0`, `amd_idle.max_cstate=0`, `cpuidle.off=1` | already applied | skip |
| `cpupower frequency-set -g performance` | already applied | re-assert only |
| `rcu_nocb_poll`, `idle=poll`, `acpi_irq_nobalance` | missing | append if absent; **required** on live cmdline after upgrade PASS |
| `cpupower idle-set -D 0` | missing | apply at runtime; **re-applied after reboot** via `12_memory_idle_apply.sh` |
| BIOS SMI / memory pre-failure | — | manual operator checklist |

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

After-action packets include these concepts via [MANIFEST.md](../references/MANIFEST.md): `cpp_memory_optimization`, `lock_free_ipc`, `simd_vector_search`, `hot_memory_universe`, `instrument_registry`, `volatility_sensor_layer`, `hot_memory_degradation`.
