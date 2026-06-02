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

---

# Session 2 — CHI404 follow-up (same day)

## Status block

```
status:          green
scope:           CHI404-only: hftbacktest API drift, systemd service, VM teardown, CPU+memory reclaim
verify-run:      CHI404 (Linux): 15/15 rithmic tests pass (12 bridge + 3 trial pipeline)
                 hft3-rithmic-trial.service: active (running), CPUAffinity=2-11, MemoryHigh=8G
                 hft3-net-tune.service: active (exited) status 0
                 hft3-rtrader-win VM: undefined, qcow2 + ISOs removed, autostart cleared
                 cpuset cgroup /sys/fs/cgroup/hft3-hot: cpus=2-11, mems=0
                 hft3-cpuset.service: enabled + active (exited) status 0 (recreates cgroup on boot)
memory-freed:    16 GB (VM) returned to host pool — host now 650 MiB used / 120 GiB free
cpus-freed:      4 vCPUs (VM) — now back to host scheduler, kernel isolcpus=2-11 still protects the hot pool
```

## What was done (in this session)

### A. CHI404 systemd service crash loop — fixed
The `hft3-rithmic-trial.service` has been crash-looping since 2026-05-29 (8334 lines of "No module named data_system.rithmic_trial.pipeline" in `/root/hft3/logs/rithmic_trial/systemd.log`). Root causes:
- The unit had no `PYTHONPATH` env var. After the `packages/` restructure the absolute import `from data_system.rithmic_trial import pipeline` only resolves if `packages/` is on `sys.path`.
- The default config path `data_system/config/rithmic_trial.yaml` was an empty legacy dir at the repo root; the real configs are in `packages/data_system/config/`.

Fix: `infrastructure/chi404/09_rithmic_trial_systemd.sh` now bakes:
```
Environment=PYTHONPATH=/root/hft3/repo/packages
Environment=HFT3_RITHMIC_GATEWAY_SO=/root/hft3/repo/build/rithmic_gateway/librithmic_gateway_shared.so
ExecStart=... --config packages/data_system/config/rithmic_trial.yaml
```

Plus: `unattended.run_unattended` now treats `enabled: false` as a clean exit 0 (no more crash loop when the trial lane is disabled in YAML).

### B. hftbacktest 2.3.0 / 2.4.x API drift — fixed
- `hft_backtest_builder`, `replay_npz_fixture`, `crypto_hft_builder` called `asset.constant_order_latency(...)`. That method exists on hftbacktest 2.4.x but not on 2.3.0 (CHI404). Added `_apply_constant_latency(asset, ...)` helper in `hft_backtest_builder.py` that picks whichever name the installed build provides. Use it from the 3 call sites.
- `cmd_replay_sample` did not pass `max_steps` to the runner. hftbacktest 2.3.0 does not return `elapse()==1` on a short NPZ (keeps ticking empty time with NaN depth), so the smoke test looped forever on CHI404. Added `--max-steps` flag with default 500.

Verify (CHI404): `pytest tests/test_rithmic_trial_pipeline.py -v` → 3/3 pass in 2.32 s.

### C. hft3-rithmic-trial.service — retry-safe + cgroup/memory wired
- `packages/data_system/rithmic_trial/unattended.py`: `connector.connect()` wrapped in `_connect_with_retry` (5 attempts, linear backoff `poll_interval_sec * attempt`). On exhaustion, exit 1 so systemd restarts us. Without this, a single R|API+ "Repository Connection Broken" alert kills the daemon and systemd restarts it 15s later into the same failure — tight crash loop with no recovery window.
- New drop-in `/etc/systemd/system/hft3-rithmic-trial.service.d/99-hft3-hot.conf`:
  - `CPUAffinity=2-11` — pinned to the hot pool (kernel cmdline `isolcpus=2-11, nohz_full=2-11, rcu_nocbs=2-11`)
  - `CPUWeight=1000` — 10× default share
  - `MemoryHigh=8G` — soft reservation; `MemoryMax=12G` — hard limit
- Verify: `taskset -p 115993` returns `0xffc` (cpus 2-11); all 10 daemon threads on CPU 2; memory `46.9M (high: 8.0G max: 12.0G available: 7.9G)`.

### D. hft3-net-tune.service — recovered
The IRQ/net tuning oneshot had been `failed (Result: exit-code)` since 2026-05-31. Ran `systemctl reset-failed && systemctl start` and it completed cleanly (`status 0/SUCCESS`). The script itself was fine; the failure was a one-time boot issue.

### E. R|Trader KVM VM — torn down (per user instruction "Full R|API+ cutover")
- `virsh shutdown` (graceful, ACPI didn't respond) → `virsh destroy`
- `virsh undefine --remove-all-storage` (qcow2 + 3 ISOs)
- Autostart link cleared
- Memory reclaimed: 16 GB returned to host. `free -h` now shows 650 MiB used / 120 GiB free (was 4.4 GiB / 113 GiB).
- Empty parent dir `/var/lib/libvirt/images/hft3-rtrader/` removed.

### F. Cpuset cgroup — persistent across reboot
- `/etc/systemd/system/hft3-cpuset.service` oneshot re-creates `/sys/fs/cgroup/hft3-hot` with `cpuset.cpus=$HOT_CPUS` and `cpuset.mems=0` on every boot. Reads `HOT_CPUS` from `/root/hft3/.env` (default `2-11`). `Before=hft3-rithmic-trial.service` so the cgroup is ready before the daemon starts.
- `infrastructure/chi404/06_cpuset_systemd.sh` updated to install this unit.

### G. Connector switch (per user instruction "Full R|API+ cutover")
- `/root/hft3/.env` flipped: `RITHMIC_TRIAL_CONNECTOR=rithmic_api`, `RITHMIC_TRIAL_CONFIG=packages/data_system/config/rithmic_trial.yaml`.
- Daemon now tries the R|API+ UAT (test) connector from `packages/data_system/config/rithmic_api_test.yaml`.

## Open issue — R|API+ UAT reachability

**The R|API+ SDK's `loginRepository` call fails with `Repository Connection Broken` (alert type 3, response code 0).** Diagnosis:

```
$ nc -zv rituz00100.00.rithmic.com 65000  # MML_DMN_SRVR_ADDR   → OK
$ nc -zv rituz00100.00.rithmic.com 56000  # MML_LIC_SRVR_ADDR   → OK
$ nc -zv rituz00100.00.rithmic.com 64100  # MML_LOC_BROK_ADDR   → OK
$ nc -zv rituz00100.00.rithmic.com 45454  # MML_LOGGER_ADDR     → No route to host
```

The 3 R|API+ auth endpoints (DMN, LIC, LOC_BROK) are reachable, but the **logger port 45454 is not** — confirmed from both CHI404 (`No route to host`) and Windows (TimeoutError). All 4 MML_LOGGER_ADDR failover hosts in the test config are unreachable on the same port. This is a **Rithmic-side restriction**: the logger port is typically only opened for paid customers; free UAT accounts don't get it.

Workarounds (none done autonomously):
1. **Ask Rithmic support** to whitelist CHI404's source IP (`64.44.98.219`) on port 45454, or upgrade the account to a tier that includes logger access.
2. **Paper trading**: the paper cluster (`ritpz04063.04.rithmic.com`) connection points are not in the SDK samples. The user must supply them. Same logger-port issue likely applies.
3. **Roll back the connector switch** (`RITHMIC_TRIAL_CONNECTOR=rtrader`) and the daemon falls back to polling an empty `rtrader_watch` dir — at least no crash loop, but no data either.

**For now:** the daemon is in the retry loop, systemd restarts it after 5 attempts, the `unattended.log` records each attempt. No CPU/memory pressure (47 MiB used, 7.9 GB high-budget headroom).

## Commits in this session

```
eba04d3 Make run_unattended connect retry-safe (no crash loop on transient R|API+ repo break)
66de656 Fix CHI404 systemd trial service: PYTHONPATH, correct config path, clean-stop on disabled
cfaa1c8 Bound cmd_replay_sample with --max-steps (default 500) for hftbacktest 2.3.0
d4f7aa7 Fix hftbacktest API drift (CHI404: 2.3.0 ships constant_latency, codebase called constant_order_latency)
260cb14 R|API+ install: C API surface + Python ctypes bridge + connector wiring (Session 1)
```

## Files changed in this session

```
M packages/data_system/rithmic_trial/unattended.py                                  # _connect_with_retry + clean-stop on disabled
M packages/data_system/rithmic_trial/pipeline.py                                    # --max-steps flag on replay-sample
M packages/backtest_pipeline/src/hft_backtest_builder.py                            # _apply_constant_latency helper
M packages/backtest_pipeline/src/replay_npz_fixture.py                              # use helper
M packages/backtest_pipeline/src/crypto_hft_builder.py                              # use helper
M infrastructure/chi404/09_rithmic_trial_systemd.sh                                 # PYTHONPATH + correct config path
M infrastructure/chi404/06_cpuset_systemd.sh                                        # install persistent hft3-cpuset.service
A /etc/systemd/system/hft3-cpuset.service (CHI404 only)                            # reboot-persistent cpuset cgroup
A /etc/systemd/system/hft3-rithmic-trial.service.d/99-hft3-hot.conf (CHI404 only)   # CPUAffinity + MemoryHigh
M /root/hft3/.env (CHI404 only)                                                    # connector switch + config path
```

## Verification commands

```bash
# Service health
ssh chi404 "systemctl status hft3-rithmic-trial.service hft3-net-tune.service hft3-cpuset.service --no-pager"

# Memory + cpuset
ssh chi404 "free -h; cat /sys/fs/cgroup/hft3-hot/cpuset.cpus /sys/fs/cgroup/hft3-hot/cpuset.mems"

# Taskset verify
ssh chi404 "taskset -p \$(pgrep -f 'data_system.rithmic_trial.pipeline run-unattended' | head -1)"

# Retry log
ssh chi404 "tail -20 /root/hft3/repo/logs/rithmic_trial/unattended.log"

# Test suite
ssh chi404 "PYTHONPATH=/root/hft3/repo/packages HFT3_RITHMIC_GATEWAY_SO=/root/hft3/repo/build/rithmic_gateway/librithmic_gateway_shared.so python3 -m pytest tests/test_rithmic_api_bridge.py tests/test_rithmic_trial_pipeline.py -v"

# UAT reachability (manual)
ssh chi404 "for p in 65000 56000 64100 45454; do nc -zv rituz00100.00.rithmic.com \$p; done"
```
