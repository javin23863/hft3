# R|API+ Install Handoff — Session 2026-06-02

## Status block

```
merge-ready:     yes
scope-green:     yes
scope:           rithmic_gateway/ (C++), packages/data_system/rithmic_trial/connector/ (Python), tests/test_rithmic_api_bridge.py
verify-run:      CHI404 (Linux): pytest tests/test_rithmic_api_bridge.py -v -> 12 passed in 0.04s
                 CHI404 (Linux): cmake -S . -B build -G 'Unix Makefiles' && cmake --build build -> 100% built
                 CHI404 (Linux): nm -D build/rithmic_gateway/librithmic_gateway_shared.so | grep hft_rithmic_adapter_ -> 10 symbols exported
                 Windows (MinGW): pytest tests/test_rithmic_api_bridge.py tests/test_rithmic_trial_pipeline.py -v -> 11 passed, 4 skipped
                 Windows (MinGW): hft_feature_golden.exe still builds (hft_research_sim and rithmic_gateway_shared do not link — pre-existing, no MSVC)
data-mode:       fixture (no live Rithmic)
known-gaps:      Windows link of librithmic_gateway_shared.so impossible without MSVC (SDK ships MSVC .lib only). On Linux/MinGW, use the bundled linux-gnu-4.18-x86_64 .a archives.
                 hftbacktest API drift on CHI404: test_replay_sample_smoke fails with 'BacktestAsset has no attribute constant_order_latency' (pre-existing, not from this work; Linux hftbacktest pkg is older than the Windows one).
                 R|Trader VM (hft3-rtrader-win) is still RUNNING on CHI404; with R|API+ the VM is no longer needed for the trade path but was not torn down (out of scope).
```

## Topology change

**Before:** R|Trader Windows VM (KVM on CHI404) → SMB → `RTraderBridgeConnector` → capture.

**After:** R|API Plus C++ adapter → `librithmic_gateway_shared.so` → Python ctypes → `RithmicApiConnector` → capture. No VM required.

The R|API+ path bypasses the R|Trader GUI and the `.cur.txt` log scrape. The C++ adapter subscribes to MBO directly via the Rithmic R|API+ SDK; events are pushed to an `SPSCQueue<MarketDataEvent, 8192>`; Python drains via `try_pop_event`.

## What was added

### C++ (`rithmic_gateway/`)
- `include/c_api.hpp` (49 lines) — `extern "C"` wrapper exposing 10 functions over an opaque `void*` handle.
- `src/c_api.cpp` (200 lines) — implementation; handle is an `AdapterEntry*` owning the `RithmicAdapter`, a `last_error` string, an `SPSCQueue<MarketDataEvent, 8192>`, and an `env_storage` vector keeping `config_.env_vars` strings alive across `connect()` calls.
- `CMakeLists.txt` — added `rithmic_gateway_shared` SHARED target (compiles both `c_api.cpp` and `rithmic_adapter.cpp`, links the same RApiPlus .a archives as the static `rithmic_gateway` target, with `POSITION_INDEPENDENT_CODE ON`). Static target unchanged. ALIAS `hft_rithmic_gateway_shared`.

### Python (`packages/data_system/rithmic_trial/connector/`)
- `_rithmic_api_bridge.py` (360 lines) — ctypes wrapper. `ConnectionConfig` (dataclass) + `CConnectionConfig` (ctypes.Structure) + `MarketDataEvent` (dataclass) + `CMarketDataEvent` (ctypes.Structure) + `RithmicApiBridge` class + `RithmicApiError` + `RithmicApiLibraryNotFoundError`. `to_c()` keeps a `_refs` list of bytes for GC anchoring; env_vars built as null-terminated `c_char_p * (N+1)` array.
- `rithmic_api_connector.py` — `NotImplementedError` stubs replaced with bridge calls. `send_order` validates side against `_SIDE_MAP = {"BUY": "B", "B": "B", "SELL": "A", "A": "A"}`; raises `ValueError` on unmapped side (no silent SELL fallthrough). `connect()` calls `bridge.create(cfg).initialize().connect()`. `poll_events` drains up to 1000 events per call.

### Tests (`tests/test_rithmic_api_bridge.py`, 220 lines, 12 tests)
- 8 tests run on both Windows and CHI404 (env-free)
- 4 tests `skipif(so_not_available)` — only pass on CHI404 with the .so built

## Build instructions (for the operator)

On CHI404:
```bash
cd /root/hft3/repo
cmake -S . -B build -G "Unix Makefiles" && cmake --build build -j
ls -la build/rithmic_gateway/librithmic_gateway_shared.so
export HFT3_RITHMIC_GATEWAY_SO=/root/hft3/repo/build/rithmic_gateway/librithmic_gateway_shared.so
python3 -m pytest tests/test_rithmic_api_bridge.py -v
```

To use the connector (no live Rithmic, just smoke):
```bash
python3 -c "
from data_system.rithmic_trial.connector.rithmic_api_connector import RithmicApiConnector
c = RithmicApiConnector()
print('connector created, config loaded')
print('SSL cert:', c._ssl_cert_path)
print('env vars (preview):', c._build_connection_config().env_vars[:3])
"
```

## Decision log (key choices made)

- **Opaque `void*` handle pointing to `AdapterEntry`** (not to `RithmicAdapter` directly). The entry owns the queue, last_error, and env_storage alongside the adapter. Cleaner RAII; one `new` / one `delete` per handle.
- **Env-var strict mode**: if `HFT3_RITHMIC_GATEWAY_SO` is set, it's the SOLE candidate (no fallback to default paths). An explicit env var should not be silently ignored.
- **Side validation in Python before reaching C**: `BUY`/`B` → `'B'`, `SELL`/`A` → `'A'`. Anything else raises `ValueError`. The C side maps `'B' ? "Buy" : "Sell"`; any non-`'B'` char becomes SELL, which is the silent-bug case the reviewer caught.
- **Bridge is not thread-safe**: documented in class docstring. R|API+ has its own internal threading; Python polls from a single thread.
- **Static `rithmic_gateway` target preserved**: the existing `hft_research_sim` continues to link against it. The new `rithmic_gateway_shared` is a separate target; no dependency on the static.

## What is NOT done (and why)

- **Live paper order/ack loop**: the R|API+ path can now issue paper orders, but this is a live-Rithmic action. The `RithmicApiConnector.connect()` requires `RITHMIC_USERNAME` and `RITHMIC_PASSWORD` env vars (already set in `/root/hft3/.env` on CHI404). A live test would issue real paper orders and consume the user's allotment. Not done in this turn.
- **VM teardown**: the KVM R|Trader VM is still RUNNING on CHI404. With R|API+ it is not needed. Tearing it down is a separate decision (might be kept for parity tests, or for fallback).
- **`hft_research_sim` Windows build**: pre-existing breakage; not from this diff. Static `rithmic_gateway` target still uses MSVC-format `.lib` archives. Fix is to add a Linux-branch-aware static target as well, but that is a separate concern.
- **Sync to CHI404**: the new Python files were scp'd to `/root/hft3/repo/` for the test run, but they are not yet committed. Operator should commit on the workstation, push to origin, then `git pull` on CHI404.

## Files changed in this commit

```
M rithmic_gateway/CMakeLists.txt
A rithmic_gateway/include/c_api.hpp
A rithmic_gateway/src/c_api.cpp
A packages/data_system/rithmic_trial/connector/_rithmic_api_bridge.py
M packages/data_system/rithmic_trial/connector/__init__.py
M packages/data_system/rithmic_trial/connector/rithmic_api_connector.py
A tests/test_rithmic_api_bridge.py
A docs/RAPI_PLUS_HANDOFF_2026_06_02.md
```
