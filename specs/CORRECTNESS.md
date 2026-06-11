# CORRECTNESS.md — The No-Bugs Regime

Version: 2026-06-10. Companion to CHI404_RUNTIME.md and DEPLOYMENT.md.

---

## 1. Principle

**A rule without an enforcement command is not a rule.**

Every row in §2 names a runnable command or test path. If a row cannot be
exercised by running a script or test suite, it is not a correctness rule — it
is a wish. No row in this document is a wish.

---

## 2. Regime Table

| # | Rule | Enforcement command / test path |
|---|------|---------------------------------|
| 1 | **ASan + UBSan clean** on all first-party C++. Vendor SDK (Rithmic R\|API+) is isolated via fake adapter; link its header-only mock (pattern: `rithmic_gateway/tools/safety_poller_syntax_check.cpp`) when building sanitizer targets so SDK binary is never linked against ASan runtime. | `cmake -DCMAKE_BUILD_TYPE=Asan .. && ctest -R "asan"` on all first-party TUs; fail on any AddressSanitizer or UBSanitizer report. |
| 2 | **TSan clean** on SPSC queue, RiskManager atomics, and SafetyPoller stress tests. | `cmake -DCMAKE_BUILD_TYPE=Tsan .. && ctest -R "tsan"` targeting `spsc_queue_stress`, `risk_manager_atomic_stress`, `safety_poller_concurrent`. |
| 3 | **64-slot Python ↔ C++ parity** on real lake NPZ via `scripts/verify_cpp_parity.py`. Silent-skip prohibited: CI must assert the pybind module (`hft3_features_cpp`) is built before running parity. Exit 2 from `verify_cpp_parity.py` when module absent is the hard-fail signal. Regime slots 41–49 differ until pipeline integration lands (see §3 defect f-adjacent note); all other slots must match bit-identically. | `python -S scripts/verify_cpp_parity.py --npz <lake_npz>` — exits non-zero on any slot mismatch or absent module. CI gate: the following step MUST pass before invoking the script — `Test-Path build/hft3_features_cpp.cp312-win_amd64.pyd` (PowerShell) or equivalent Linux check (`test -f build/hft3_features_cpp.cp312-win_amd64.pyd`); module absence must never silently read as parity success. |
| 4 | **Determinism**: same NPZ + same weights → byte-identical decision log in REPLAY mode. | `python scripts/replay_determinism_check.py --npz <npz> --weights <weights.bin> --runs 2` — diffs decision logs of two runs; exits non-zero if any byte differs. |
| 5 | **Sim = real one-source rule**: feature and decision math compiled from a single source into both the research pybind module and the live binary. No `#ifdef`-divergent math paths between research and live builds. | Code-review gate: `grep -rn "ifdef.*RESEARCH\|ifdef.*LIVE"` over `packages/decision_engine/cpp/` and `packages/features_engine/cpp/` must return zero hits. CMake target `hft_features` and pybind target both compile the same `.cpp` sources. |
| 6 | **Failure injection**: one test per adapter flag (6 flags) + one test per Python monitor (5 monitors) + combined-flag ordering test asserting sticky hard-halt suppresses re-trigger. | `python -m pytest tests/test_failure_injection/ -q` — covers all 6 `RithmicAdapter` atomic safety flags, all 5 Python monitors (`production_safety.py`), and a combined-flag ordering scenario. |
| 7 | **Single-submission-gate proof**: order-call counter unchanged after BLOCK; `grep` / link-map check confirms exactly one `send_prepared_limit_order` call site in the binary. | `python -m pytest tests/test_submission_gate.py -q` (counter invariant); `nm -C hft3_engine \| grep send_prepared_limit_order \| wc -l` must output `1`. |
| 8 | **All-10-slot `ActionArray` regression** seeded with poisoned memory: all 10 slots must be written by `evaluate_actions`; no slot may retain the poison value after the call. | `python -m pytest tests/test_decision_runtime_all_slots.py -q` — C++ test binary `decision_runtime_slot_test` writes `0xDEADBEEF` sentinel to all 10 slots, calls `evaluate_actions`, asserts no slot equals sentinel and all non-selected slots equal `NEG_INFINITY_SENTINEL`. |
| 9 | **No-alloc post-init**: debug allocator hook aborts on any `new`/`malloc` called after `hft3_engine` enters run mode. | `cmake -DCMAKE_BUILD_TYPE=Debug -DCOUNT_ALLOC=ON .. && ctest -R "no_alloc_hot_loop"` — test replays 10k events through hot loop; debug hook aborts with non-zero exit on any allocation. |
| 10 | **Waterfall timestamps real**: reject records where `shadow_synthetic: true` in authoritative latency summaries. Synthetic offsets in `paper_latency_daemon._shadow_probe_mono_ns()` (lines ~53–57: `t1 = t0 + 1000; t2 = t1 + 500; t3 = t2 + 500`) must never populate `latency_summary.json` `paper_order_latency` when `measured=true`. | `python -m pytest tests/test_latency_waterfall.py -q` — loads `runtime/latency_reports/latency_summary.json`; fails if any entry has `shadow_synthetic: true` AND is referenced by an authoritative summary field. |
| 11 | **C++ paths in `core_engine_paths.py`**: changes to `rithmic_gateway/`, `risk_engine/`, `packages/decision_engine/cpp/`, and `packages/features_engine/cpp/` must stale T3 certification stamps. Currently these paths are absent from `packages/hft3/validation/core_engine_paths.py`; they must be added so the staleness checker marks stamps RESEARCH_ONLY on any C++ change. | `python -m pytest tests/test_staleness/test_cpp_paths_stale.py -q` — asserts all four C++ source trees appear in `CORE_BACKTESTER_PATHS`; fails until paths are added. T3 staleness check: `bash scripts/check_backtester_certification_status.sh`. **Note: `tests/test_staleness/test_cpp_paths_stale.py` does not yet exist — to be created in M2.** |
| 12 | **`-Wall -Wextra -Werror` + clang-tidy** on hot-path translation units: `decision_runtime.cpp`, `risk_manager.cpp`, `rithmic_adapter.cpp`, `feature_extractor.cpp`. | `cmake -DCMAKE_BUILD_TYPE=Release -DENABLE_CLANG_TIDY=ON .. && cmake --build . --target hft3_engine 2>&1 \| grep -E "error:|warning:" \| wc -l` must output `0`. |
| 13 | **Literature → code checklist** at T4: each of the 8 domains in vault `System Implications.md` must map to a named test or feature. Reviewer confirms no domain is orphaned. | `bash scripts/check_champion_promotion_gate.sh` (T4 gate) — promotion gate script must include a literature-checklist step; reviewed manually before GREEN stamp. 8 domains: MBO event dynamics, order flow imbalance, queue-reactive/Hawkes intensity, optimal execution, market making/stochastic control, HF econometrics, ML/LOB, latency/market design (cite: vault `library/System Implications.md`). |

---

## 3. Known-Defect Ledger

This ledger must be **EMPTY** before live arm (DEPLOYMENT.md §5 references this requirement).

**Lane scoping**: this ledger is CME-lane scoped. The crypto lane maintains a
lane-scoped ledger in CRYPTO_LIVE.md §9. The EMPTY-before-arm gate applies per
lane — CME live arm (ALPHA_CME.md M10) gates on this ledger; crypto live arm
(ALPHA_CRYPTO.md C12) gates on CRYPTO_LIVE.md §9. Neither lane's defects block
the other lane's arm.

| ID | Component | Description | Status |
|----|-----------|-------------|--------|
| a | `DecisionEngine::evaluate_actions` | Writes only slots 0–2 of 10; slots 3–9 zero-initialized; `get_optimal_action` scans all 10, producing UB on uninitialized reads; EV=0.0 codes can tie or beat legitimate actions. Fix: write `NEG_INFINITY_SENTINEL` to slots 3–9 every call (CHI404_RUNTIME.md §4.1). | **FIXED** — `packages/decision_engine/cpp/tests/test_decision_runtime_hardening.cpp` (poisoned 0xCD memory, 10k randomized argmax sweep) + `tests/test_decision_runtime_hardening.py`; 10053 assertions green. |
| b | `PaperLatencyDaemon._shadow_probe_mono_ns` | Synthetic waterfall offsets (`t1 = t0+1000; t2 = t1+500; t3 = t2+500`) are fabricated nanosecond deltas, not real callback timestamps. If these populate the authoritative `latency_summary.json`, measured=true is false. | **FIXED** — `_shadow_probe_mono_ns` deleted; records carry only real connector-callback monotonics (waterfall fields `None` absent real boundaries); `shadow_synthetic` never emitted; enforced by `tests/test_latency_waterfall.py` (§2 row 10) green on workstation + CHI404 against measured `latency_summary.json` (1002-pair native-probe campaign `order_ack_campaign_20260611T072116Z`, 2026-06-11); commit 9268cb7. |
| c | `RiskManager::check_order` | Return value is not enforced at the call site in the hot loop; BLOCK and HALT results are silently dropped rather than closing the submission gate. | **FIXED (engine path)** — `EngineLoop` `submit_gate` is sole submit call site, switches on `RiskManager::check_order` PASS/BLOCK/HALT/FLATTEN; evidence `engine/tests/test_engine_loop.cpp` (gate-proof: BLOCK/HALT → submit_count 0; 11 tests). Live-binary instantiation verified at M8. |
| d | `assert_live_config` | Not tied to the order submission path in the current `hft3_engine` startup sequence; live config validation can be bypassed if startup step 2 is skipped. | **FIXED (engine path)** — `EngineConfig` contract validated fail-closed at startup before gate can open; same evidence + `engine/src/engine_config.cpp`. Live env wiring verified at M8. |
| e | `scripts/verify_cpp_feature_parity.py` (older script) | Exits 0 when the C++ module is absent (silent-skip hazard). CI suites that invoke this script instead of `scripts/verify_cpp_parity.py` silently certify parity without running any C++ comparison. Script must not be invoked in any CI gate; `verify_cpp_parity.py` (exit 2 on absent module) is canonical. **Remediation**: `scripts/verify_cpp_feature_parity.py` is **deprecated** and must be **DELETED** at M2. Until deletion it must not be cited by any CI lane or promotion script. `scripts/verify_cpp_parity.py` is the only canonical parity driver. | **FIXED** — `scripts/verify_cpp_feature_parity.py` deleted; `scripts/verify_cpp_parity.py` sole canonical driver (hard-fails exit 2 when module absent); enforced by `scripts/run_c_lane.ps1` precondition check. |
| f | `packages/hft3/validation/core_engine_paths.py` | Contains no C++ source paths (`rithmic_gateway/`, `risk_engine/`, `packages/decision_engine/cpp/`, `packages/features_engine/cpp/`). C++ changes do not stale certification stamps; a corrupt or divergent C++ binary can hold a GREEN stamp. | **FIXED** — `rithmic_gateway/`, `risk_engine/`, `packages/decision_engine/cpp/`, `packages/features_engine/cpp/` added to `core_engine_paths.py`; evidence `tests/test_staleness/test_cpp_paths_stale.py` (12 tests). |

---

## 4. Certification Tier Mapping

Source: vault `validation/Backtester Certification.md` (T0–T4 tier table).

| Regime row | T0 (every commit) | C-lane (per C++ commit) | T2 (weekly/manual) | T3 (every stamp) | T4 (before promotion) |
|------------|:-----------------:|:-----------------------:|:------------------:|:----------------:|:---------------------:|
| 1 ASan/UBSan | | ✓ | | | |
| 2 TSan | | ✓ | | | |
| 3 64-slot parity | ✓ | ✓ | | | |
| 4 Determinism | | | ✓ | | ✓ |
| 5 One-source rule | | ✓ | | | ✓ |
| 6 Failure injection | ✓ | | | | |
| 7 Submission gate | ✓ | ✓ | | | |
| 8 All-10-slot regression | ✓ | ✓ | | | |
| 9 No-alloc post-init | | ✓ | | | |
| 10 Real waterfall timestamps | ✓ | | | ✓ | |
| 11 C++ paths in core_engine_paths | ✓ | ✓ | | ✓ | |
| 12 -Wall/-Wextra/-Werror + clang-tidy | | ✓ | | | |
| 13 Literature → code checklist | | | | | ✓ |

**C-lane** definition: any commit that modifies a path under `rithmic_gateway/`,
`risk_engine/`, `packages/decision_engine/cpp/`, or `packages/features_engine/cpp/`
triggers the C-lane column in addition to T0.

**K-lane** (crypto analog, defined in CRYPTO_LIVE.md §10): any commit that
modifies a path under `packages/crypto_lane/` or
`packages/execution/adapters/crypto_*` triggers
`python -m pytest tests/test_crypto_lane/ tests/test_crypto_l2/ -q`
in addition to T0. The crypto regime rows K1–K12 and their tier mapping live
in CRYPTO_LIVE.md §8/§10.

---

## 5. Review Gates

5.1 **Subagent edit policy**: all hft3 code edits are performed via Sonnet subagents;
    the main thread verifies, tests, and commits only. This matches the current process
    documented in the user's memory stack.

5.2 **Adversarial review pass**: before any live arm, an adversarial review of the
    full defect ledger (§3) must confirm EMPTY status. A reviewer who did not author
    the fix performs the adversarial pass.

5.3 **Defect ledger as gate**: DEPLOYMENT.md §5 pre-arm checklist requires this
    ledger to be empty. Any item with status OPEN blocks live arm regardless of
    shadow results or certification tier.
