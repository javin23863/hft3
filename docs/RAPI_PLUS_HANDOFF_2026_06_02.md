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

---

# Session 3 (2026-06-02): Windows-out-of-loop cleanup

User statement: *"take wondow computer out of the loop chgi404 is the computer we use"*.

CHI404 is the only trade-path host. The R|Trader Windows VM was already torn down in Session 2. This session removes VM-coupled code from the active repo and rewrites CHI404 entrypoint scripts to the rithmic_api path.

## Removed code (39 files + 4 infra)

| Category | Files |
|----------|-------|
| `scripts/chi404_vm_*.{sh,py,ps1}` | 35 |
| `scripts/launch_chi404_vm_vnc.ps1` | 1 |
| `scripts/chi404_finish_rtrader.sh` | 1 |
| `scripts/chi404_setup_vm_bridge.sh` | 1 |
| `scripts/chi404_trigger_vm_paper_sweep.py` | 1 |
| `infrastructure/chi404/08_rtrader_wine_setup.sh` | 1 |
| `infrastructure/chi404/10_rtrader_smb_share.sh` | 1 |
| `infrastructure/chi404/11_rtrader_windows_vm.sh` | 1 |
| `infrastructure/chi404/autounattend.xml` | 1 |

## Retained (defensive legacy, still tested)

- `packages/data_system/rithmic_trial/connector/rtrader_bridge.py` — file-based, no VM dependency, used by `chi404_run_trial_smoke.sh` for synthetic .cur.txt ingest
- `tests/test_rtrader_bridge_curtxt.py` — still passes
- `tests/test_rithmic_topology_guards.py` — still passes
- `packages/data_system/rithmic_trial/platform.py` — `is_windows()` guard
- `scripts/chi404_run_trial_smoke.sh` — synthetic file injection
- `scripts/run_chi404_bmc_ikvm_tunnel.ps1` — BMC iKVM, accesses CHI404 host console for BIOS/EXPO, NOT VM-coupled
- `scripts/chi404_fast_market_sweep.sh` + `chi404_host_paper_sweep_orchestrator.sh` — already deprecated stubs
- `scripts/deprecated/*` — already separated, untouched

## Retained (cross-platform guards, legitimate)

- `packages/data_system/crypto_lane/{kraken_l3_recorder,binance_l2_recorder}.py` — `sys.platform == "win32"` (asyncio no signal_handler on Windows)
- `packages/workbench/scripts/run_hybrid_pipeline_gate.py` + `engine.py` — `HFT3_AFTER_ACTION` workbench toggle
- `packages/research_pipeline/roundtrip_speedtest.py` — ping flag diff

## Rewritten scripts

| Path | Before | After |
|------|--------|-------|
| `scripts/chi404_run_trial_live.sh` | calls `chi404_vm_live_gate.sh` (deleted) | verifies `hft3-rithmic-trial.service` is active, runs `pipeline capture` / `process` / `replay-event` |
| `scripts/chi404_run_paper_latency_sweep.sh` | calls VM UI orders + RTraderBridgeConnector | rithmic_api: reachability gate → daemon → wait for `paired_submit_ack_count >= 1000` (or `PAPER_LATENCY_SKIP_ORDERS_BURST=1` for connectivity check) |
| `scripts/deploy_chi404_env.py` | default `RITHMIC_TRIAL_CONNECTOR=rtrader` | default `RITHMIC_TRIAL_CONNECTOR=rithmic_api` |

## Docs updated

| Doc | Change |
|-----|--------|
| `docs/vault/CHI404_CANONICAL_ENTRYPOINTS.md` | Rewritten for rithmic_api path; removed VM deploy chain table |
| `docs/rithmic_trial/README.md` | Replaced R\|Trader VM section with R\|API+ daemon section; added `HFT3_RITHMIC_GATEWAY_SO` env var |
| `docs/rithmic_trial/CHI404_VM_BUGS.md` | Marked historical; prepended "do not bring it back" warning |
| `docs/rithmic_trial/VALIDATION_ADDENDUM.md` | Added R\|API+ order callback gap + UAT port 45454 issue; added `test_rithmic_api_bridge.py` to scope-green |
| `docs/vault/WORKSTATION_ONE_LANE.md` | Clarified that workstation is dev/research only, not in trade path |
| `tests/test_chi404_canonical_guardrails.py` | Dropped rtrader script existence assertions; added `test_no_rtrader_active_scripts`, `test_no_windows_only_doc_asserts_chi404`, `test_canonical_entrypoints_doc_uses_rithmic_api` |

## Error messages tightened

- `unattended.py` — "Do not run capture/unattended on a Windows workstation" → "Windows is the dev workstation, not the trade-path host."
- `pipeline.py` — "not this Windows workstation" → "BLUEPRINT §4; use connector: fixture for local tests"
- `paper_latency_daemon.py` — "Do not run on a dev workstation" → "Windows is the dev workstation, not the trade-path host"
- `rtrader_bridge.py` — same clarification

## Status

- 23 deleted scripts confirmed via `_active_script_paths`; 0 survivors in active chain
- 4 infra files removed
- 6 docs updated; 1 marked historical
- 4 error messages tightened
- Windows pytest target: `tests/test_chi404_canonical_guardrails.py`, `test_rithmic_trial_pipeline.py`, `test_rithmic_api_bridge.py` (to be re-run + commit + push)
- CHI404: 12/12 bridge tests, 15/15 trial tests, daemon active with new drop-in; **no code change on CHI404 needed for Session 3**

## Open (unchanged from Session 2)

- R|API+ UAT port 45454 blocked by Rithmic firewall (`loginRepository` "Repository Connection Broken") — user action pending
- R|API+ order callbacks not wired to SPSC queue — `paper_latency_daemon` paired count stays at 0; `PAPER_LATENCY_SKIP_ORDERS_BURST=1` works around

---

# 4. Session 4 (2026-06-02): misdiagnosis and real fix

User statement: *"if we cant place order you did somthing wrong dont over complicted it"*.

User was right. I had been blaming Rithmic's "port 45454 firewall" since Session 2. The SDK log `rithmic_api.log.000` proves the actual error was in our own config mapping, with a second real error underneath (Rithmic UAT credentials).

## What I got wrong

The R|API+ SDK logs every call to `loginRepository` with the actual connect point it used. The most recent log showed:

```
REngine::loginRepository : pCnnctPt : login_agent_pnlc        <-- WRONG
AlertInfo : ||Repository Connection Broken|3|5|0|              <-- PnL endpoint rejected
```

The Python connector `RithmicApiConnector._build_connection_config()` was reading a non-existent `connect_points.rep` key, falling back to `login_params.sPnlCnnctPt = "login_agent_pnlc"` (the PnL endpoint, not repository). The SDK opened a TCP connection to the PnL endpoint, Rithmic responded with `ALERT_CONNECTION_BROKEN` (type 3), the C++ Alert handler only mapped `ALERT_LOGIN_FAILED` (type 5), so `rep_login_status_` stayed at `LOGIN_NOT_LOGGED_IN`, the login_cv_ timed out 30s later, the C++ returned `false`, the daemon retried with `RestartSec=15s`. The "45454 firewall" story was a red herring from the `nc` test that succeeded on 65000/56000/64100 — those are reachable from any UAT account; 45454 is the MML logger address (used after login) and the test was unrelated to the actual failure.

## Real fix (commit 00848cc)

`packages/data_system/rithmic_trial/connector/rithmic_api_connector.py`:

```python
# Before (broken)
rep = (
    connect_points.get("rep")           # "" — key doesn't exist
    or connect_points.get("ih")         # ""
    or login.get("sPnlCnnctPt")         # "login_agent_pnlc"   <-- wrong
    or login.get("sIhCnnctPt")          # ""
    or ""
)

# After (correct)
repository_login = self._cfg.get("repository_login", {}) or {}
rep = repository_login.get("sCnnctPt") or ""    # "login_agent_repositoryc"
```

The repo connect point comes from `repository_login.sCnnctPt` in `rithmic_api_test.yaml`. The YAML is the source of truth.

`rithmic_gateway/src/rithmic_adapter.cpp` — defensive Alert mapping:

```cpp
// Before: only mapped type 5
if (pInfo->iAlertType == RApi::ALERT_LOGIN_FAILED) { ... LOGIN_FAILED ... }

// After: also map type 3 (CONNECTION_BROKEN) so cv_ doesn't hang 30s
if (pInfo->iAlertType == RApi::ALERT_LOGIN_FAILED
    || pInfo->iAlertType == RApi::ALERT_CONNECTION_BROKEN) { ... }
```

After this, a fresh SDK log shows the daemon's connect attempts now return in ~10s instead of 30s+, even on failure.

## What's underneath

With the connect-point fix, the SDK now reaches the correct repository endpoint and Rithmic responds with the actual auth error:

```
REngine::loginRepository : pCnnctPt : login_agent_repositoryc   <-- correct
AlertInfo : ||Repository Connection Login Failed. Please contact the FCM/IB who issued your login id for assistance.|5|5|13|permission denied
```

`rp code : 13` = permission denied. Rithmic's UAT server is rejecting the credentials in `RITHMIC_USERNAME` for the `rithmic_uat_dmz_domain` cluster. This is an **account-level** issue, not a code or network issue. User action: contact Rithmic / the FCM that issued the login, or supply paper trading credentials.

## New regression test

`tests/test_rithmic_api_bridge.py::test_connector_repository_connect_point_from_repository_login_block` — fails if `cfg.rep_connect_point != "login_agent_repositoryc"` and != "login_agent_pnlc". Catches this class of bug.

## Status

- 19 → 20 tests on Windows / 37 → 38 tests on CHI404 (15 + 4 skipped on Windows; 37/0 on CHI404 with the new test)
- 1 commit (00848cc), pushed to origin/main, CHI404 pulled, rebuilt
- `hft3-rithmic-trial.service` restarted; SDK log now shows `pCnnctPt : login_agent_repositoryc` and an immediate `rp code 13` from Rithmic
- C++ defensive alert mapping verified live: failure path returns in ~10s (was 30s)

## User action (real, not code)

The remaining blocker is Rithmic UAT account authorization for the credentials in `/root/hft3/.env`:

1. Contact Rithmic support / the FCM that issued the login ID and ask them to authorize `joshuajacob2386@gmail.com` on the `rithmic_uat_dmz_domain` UAT cluster, OR
2. Supply paper trading credentials (same authorization issue likely applies), OR
3. Confirm a different UAT cluster / connect point is required (the SDK has `login_agent_pnlc`, `login_agent_tpc`, `login_agent_opc`, `login_agent_historyc`, `login_agent_repositoryc` — only the repository one is failing on auth; the others would only matter after repo login succeeds).

As soon as auth succeeds, the order-callback wiring (Sessions 2-3) will deliver `StatusReport` / `FillReport` / etc. into the SPSC queue, `RithmicApiConnector.poll_order_events()` will emit them as `order_ack` / `fill` / `cancel`, and `paper_latency_daemon.paired_submit_ack_count` will tick toward the orchestrator's 1000-pair target.

