# C++ hot-path audit (workbench vs live)

## Topology (BLUEPRINT §4)

| Host | Role |
|------|------|
| **CHI404** | Live/paper MBO capture, order submit, Rithmic trial lane, latency probes |
| **Dev workstation** | Offline replay, pytest, git, after-action LLM — **never** in exchange hot loop |

Python trial capture (`data_system/rithmic_trial/`) is quarantined non-hot
wiring on CHI404 only. Production order placement and placement-speed authority
are **C++** via `rithmic_gateway/`; no Python broker wrapper is allowed in the
measured send path.

## CMake targets

| Target | Purpose | CI verify |
|--------|---------|-----------|
| `hft_features` | MBO feature extraction | `hft_feature_golden` pytest |
| `hft_risk` | Risk manager | `hft_research_sim --verify-stack` |
| `hft_decision` | Zero-alloc decision engine | same |
| `hft_rithmic_gateway` | R\|API+ adapter + SPSC queue | same (queue roundtrip in self-test) |
| `hft_research_sim` | Runtime stack self-test | **required** in `.github/workflows/cpp_stack_verify.yml` |
| `hft_rithmic_latency_probe` | Tick→send placement and send→ack confirmation on colo | C++ build + observed broker summary |
| `hft_feature_golden` | Python/C++ feature parity | pytest on CI |

Memory/concurrency authority: [MEMORY_ARCHITECTURE.md](MEMORY_ARCHITECTURE.md) (`SPSCQueue` padding, zero-alloc decision path).

Build locally:

```bash
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release
./build/hft_research_sim --verify-stack
```

## Stack verify (not replay)

Module: `workbench/src/sim/cpp_stack_verify.py` → `hft_research_sim --verify-stack`

Runtime checks (all must pass):

1. `RithmicAdapter::initialize`
2. `SPSCQueue` roundtrip via `on_market_data_update` (synthetic event — not full MBO marked-point replay)
3. `FeatureExtractorCpp` on synthetic ADD events
4. `DecisionEngine::evaluate_actions` + `get_optimal_action`
5. `RiskManager::check_order` on intent

Subprocess policy: `HFT3_CPP_STACK_VERIFY=once` (default) — one verify per Python process. Use `off` to skip, `always` to re-run (CI).

## Workbench simulation lanes

| Lane | Module | Hot path? |
|------|--------|-----------|
| C++ latency injection | `cpp_latency_profile.py`, `latency_injector.py` | Measured C++ distributions; Python sim |
| C++ stack verify | `cpp_stack_verify.py` | Self-test only |
| C++ NPZ replay (approach 3) | **Not implemented** | Pending |
| Python backtest | `engine.py` | Research — not promotion gate alone |

Diagnostics:

| Field | Meaning |
|-------|---------|
| `cpp_stack_verified` | All five runtime checks passed |
| `cpp_replay_available` | Always `false` until NPZ→queue replay ships |
| `queue_tracker_status` (AAR) | `link_only` / `stub_or_unverified` / `available` |

## Remaining gaps

1. Full NPZ replay through C++ queue
2. C++ feature/model parity expansion beyond current stack verify
3. CHI404 tuning until native C++ `tick_to_send_us` meets the operating envelope
4. Cancel/replace callback pairing hardening for larger sample runs

## Tests

```bash
pytest tests/test_hot_path_topology.py -q -m "not integration"
pytest tests/test_hot_path_topology.py -q   # includes live binary when built
pytest tests/test_rithmic_topology_guards.py -q
pytest tests/test_cpp_feature_golden.py -q
```

See [LATENCY_ARCHITECTURE.md](LATENCY_ARCHITECTURE.md).
