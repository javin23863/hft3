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
| 3 | **64-slot Python ↔ C++ parity** on real lake NPZ via `scripts/verify_cpp_parity.py`. Silent-skip prohibited: CI must assert the pybind module (`hft3_features_cpp`) is built before running parity. Exit 2 from `verify_cpp_parity.py` when module absent is the hard-fail signal. Regime slots 41–49 differ until pipeline integration lands (see §3 defect f-adjacent note); all other slots must match bit-identically. | `python scripts/verify_cpp_parity.py --npz <lake_npz>` — exits non-zero on any slot mismatch or absent module. CI gate: the following step MUST pass before invoking the script — `Test-Path build/hft3_features_cpp*.pyd` (PowerShell) or equivalent Linux check (`test -f build/hft3_features_cpp*.so`); module absence must never silently read as parity success. |
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
| g | `model_08` Transfer Entropy estimator (`conditional_entropy_3d`) | Severe positive bias at default `bins=16`: independent series (N=1000) measure TE ≈ 0.56 nats vs theoretical 0.0 (mean over 20 seeds). Root cause: 16³ = 4,096 histogram cells for ~1,000 samples violates the ~N^(1/3) cells rule, systematically underestimating H(Y\|X₁,X₂). Any independence threshold calibrated at bins=16 is unreliable. Found by limiting-case battery 2026-06-11 (Ultimate PDF p18/20 mandate). | **OPEN** — bias documented by `tests/structural_models/test_limiting_cases.py::test_te_large_bias_at_default_bins16_documented`; fix = bias-corrected estimator (Miller–Madow or KSG) or bins scaled to N^(1/3) with re-calibrated thresholds. |
| h | `model_05_dealer_hedging.py` BS Greek set | Implements `bs_gamma`/`bs_vanna`/`bs_charm`/`bs_d1_d2` but omits `bs_delta` and `bs_call_price`; PDF mandate requires the full Greek set, and the published reference values (S=K=100, r=0, σ=0.2, T=1: call 7.9656, delta 0.5398) cannot be verified against the repo. Found by limiting-case battery 2026-06-11. | **OPEN** — `tests/structural_models/test_limiting_cases.py::test_bs_call_price_skipped` (SKIP with reason); fix = add `bs_delta`/`bs_call_price` + reference-value tests. |
| prop-i | Prop-slot feature store (slots 31–34): `cutoff_pressure_score` (31), `prop_reentry_score` (32), `news_restriction_flatten_score` (33), `max_contract_trade_imbalance` (34) | Feature slots 31–34 have hypothesis readers (`modules.py:614,677,703,497`) but no writer anywhere in the repo. All four slots are all-zero in the feature store. Empirical proof: 0 of 1,340,349 rows non-zero across 200 MES feature-store files (control check: `aggressor_volume_imbalance`(0)=69.3%, `book_slope`(13)=99.5%, `distance_to_vwap`(30)=98.9% non-zero). Hypotheses 20, 30, 35, 36, 38 reading these slots are structurally dead inputs. See `scripts/audit_prop_slots.py` for the repeatable proof. Fix in PC2 (`features/prop_features.py`). | **OPEN** — blocks M10/M12 arm. |
| prop-ii | Event contexts `TPT_FLATTEN`, `APEX_FLATTEN`, `NEWS_RESTRICTION` | These three context labels are referenced by hypothesis gate logic (`modules.py:612,702`) but are never created in `event_context_labels.json` or `events.csv`. As a result, HYP 30 (`CutoffPanicExits`) and HYP 38 (`EconomicEventRestrictionFlattening`) never fire — their context gate is permanently false. Fix in PC4 (derive `NEWS_RESTRICTION` from the macro calendar T−2 min offset; source Topstep/Apex cutoff times for `TPT_FLATTEN`/`APEX_FLATTEN` or fold into `PROP_FLATTEN_TOPSTEP`). | **OPEN** — blocks M10/M12 arm. |
| prop-iii | HYP 32 `DailyLossLimitDefense` (`modules.py:630–640`) | Every code path in `DailyLossLimitDefense.evaluate()` terminates with `return 0.0`. The hypothesis is a confirmed no-op: it emits no signal regardless of market state. Fix in PC4 (replace the hardcoded `0.0` with real loss-limit-defense logic gated on `prop_cohort_active`). | **OPEN** — blocks M10/M12 arm. |
| prop-iv | `cross_asset_features['ES']['institutional_flow_score']` read by HYP 20 | HYP 20 (`MicroContractRetailLag`, `modules.py:486`) reads `cross_asset_features['ES']['institutional_flow_score']`. That key has no producer anywhere in the repo. HYP 20 is dead on every event. Fix in PC3 (define `institutional_flow_score` as the normalized ES aggressor flow from the new `micro_vs_leader_flow_divergence` producer). | **OPEN** — blocks M10/M12 arm. |

**Note on M6 masking:** These four defects were masked in the in-flight M6 Stage B run because the `HypothesisRegistry.generate_research_card` approval gate scores any hypothesis with `num_trades=0` as FAIL. A structurally dead hypothesis (never fires) is indistinguishable from a tested-and-legitimately-rejected hypothesis without this ledger. The four prop-i..iv items here are the explicit record that FAILs on HYPs 20, 30, 32, 35, 36, 38 from M6 mean "never alive," not "tested and rejected."
| i | `packages/backtest_pipeline/src/hft_backtest_builder.py` | Builder used `log_prob_queue_model2()` (Level-2 queue model) unconditionally. Real lake NPZs from CME MBO feed contain Level-3 events only (ADD=10, CANCEL=11, MODIFY=12, FILL=13; zero DEPTH=1 rows). L2 engine ignores all L3 events → book permanently empty (best_bid/best_ask NaN on every step) → no simulated fill ever executed on real lake data. All prior real-NPZ replay results are no-fill artifacts; re-baseline required. Secondary: orphan CANCEL/MODIFY/FILL events (order_id had no ADD in the capture window) caused L3 engine error 12; filtered by `_filter_l3_orphans()`. | **FIXED** — `hft_backtest_builder.py` rewritten: auto-detects L3 (ADD_ORDER_EVENT present, DEPTH_EVENT absent) vs L2; L3 path uses `asset.l3_fifo_queue_model()` + orphan filtering + temp-file lifecycle; L2 path unchanged. Evidence: `tests/backtest_pipeline/test_l3_backtest_builder.py` (8 tests: quote-sanity NaN ratio 0/1000, fill proof exec @ 7545.50 position=1.0, session wall-clock), `tests/test_hftbacktest_adapter_order_lifecycle.py` (4), `tests/backtester_validation/fast` (21), `tests/backtest_pipeline` (41), `tests/test_run_event_universe.py` (44); all green on both HFT3_QUOTE_STEPPING modes (event, grid). |

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
