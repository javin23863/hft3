# CHI404_RUNTIME.md — `hft3_engine` Live Binary Behavioral Contract

Version: 2026-06-10. Authoritative for CHI404 bare-metal (Chicago colo, CME via Rithmic R|API+).
Stage-skip flags prohibited. Supersedes HOT_PATH.md §4 item 1 (per-stage core pinning).

---

## 1. Scope and Authority

1.1 **C++ only on the hot path.** No Python interpreter is present at runtime on CHI404.
    Python serves research, validation, and deployment tooling only; it is not loaded
    by `hft3_engine`.

1.2 **Strategy arrives as a weights binary only.** Format: 16-byte header
    (`magic 0x48465433` little-endian, `version`, `model_id`, `feature_count`)
    followed by 1024 × IEEE-754 little-endian doubles, zero-padded.
    Source: `packages/decision_engine/cpp/include/decision_runtime.hpp` `ModelHeader`,
    `packages/decision_engine/python/src/walk_forward.py` `export_weights_to_cpp()` (~line 339).
    No other strategy representation is accepted.

1.3 **Vault invariants (binding, repeated for runtime authority):**
    - Live/paper execution runs on CHI404 only; the research workstation never wires to
      live or paper adapters (vault: `CHI404 Infrastructure.md`).
    - Filtration F_t: the no-lookahead rule applied to research features applies
      identically to live features. Feature computation at time t may use only
      information whose exchange timestamp is strictly ≤ t.
    - Event-time ordering: all market-data events processed in nanosecond exchange-time
      order. Assert in debug builds; any ordering violation is a hard fault.

1.4 Does not duplicate PIPELINE.md §§1–6 or LATENCY.md §§1–6; cite those by section.

---

## 2. Process / Thread / Core Map

Source: `infrastructure/chi404/06_cpuset_systemd.sh`.

| Core(s) | cgroup / service | Occupant | Notes |
|---------|-----------------|----------|-------|
| 0 | OS default | OS scheduler, IRQ routing, log disk I/O | Never pinned to hft3 services |
| 1 | `hft3-gateway` `CPUAffinity=1` | Rithmic R|API+ callback threads (SPSC producers) | Write `mbo_queue_`, `order_queue_`; set safety flags |
| 2 | `hft3-hot` cpuset `2-11` (slot 0) | **ONE FUSED HOT THREAD** | See §2.1 |
| 3 | `hft3-hot` cpuset `2-11` (slot 1) | Log-drain thread | Reads log SPSC ring; formats/writes to disk |
| 4–11 | `hft3-hot` cpuset `2-11` (slots 2–9) | Reserved | Future symbol chains; no process may be pinned here in v1 |

`HOT_CPUS` defaults to `2-11`; the cgroup is created on every boot by
`hft3-cpuset.service` (oneshot, `Before=hft3-rithmic-trial.service`).

### 2.1 Fused Hot Thread Rationale

The entire compute chain — queue-pop → book update → `FeatureExtractorCpp` →
`DecisionEngine::evaluate_actions()` → `RiskManager::check_order()` →
`SafetyPoller::poll()` → `send_prepared_limit_order()` — runs on **one** busy-spinning
SCHED_FIFO thread on core 2.

Total compute budget ≤ 18 µs (cite: LATENCY.md §7 stage table: MD callback→book ≤5 µs +
book→features ≤10 µs + features→decision ≤2 µs + risk check ≤1 µs = 18 µs).
Wire floor is 2–10 ms (Rithmic R|API+, no kernel bypass; cite: LATENCY.md §5).

Splitting stages across cores adds inter-thread SPSC queues and ordering hazards for
zero benefit: the 2–10 ms wire floor dominates by two orders of magnitude. Each queue
boundary adds cache-miss latency and reordering risk. Single-thread fused eliminates
all of this. Supersedes HOT_PATH.md §4 item 1.

---

## 3. Hot-Loop Contract

Normative execution order per loop iteration. Deviations are defects.

```
loop forever (busy-spin, SCHED_FIFO, core 2):
  (1) DRAIN order_queue_ FULLY
      for each OrderEvent popped:
        route by event_type:
          'F' fill   → RiskManager.update_position(fill_qty, pnl_impact)
                       update local order table (ack pending → filled)
          'B' bust   → RiskManager.update_position(reversal)
                       set position_desync (handled in step 2)
                       update local order table
          'N' not-modified → set order_desync (handled in step 2)
                             update local order table
          'L' auto-liquidate → set auto_liquidate_halt (handled in step 2)
          'C','M','R','X','A','S' → update local order table accordingly
      [Position correctness must precede any decision in this iteration]

  (2) result = SafetyPoller.poll()
      act on result per §5 failure-action table:
        result.halted            → close submission gate; do not submit
        result.reconcile_required → schedule reconciliation (see §5)
        result.book_resync_required → mark book invalid; do not use quotes
        result.alert_severity    → log; act per §5 severity rows
        result.md_drops_delta > 0 → log delta; telemetry only
        result.order_drops_delta > 0 → log delta; telemetry only
      log every non-empty PollResult (see §7)

  (3) DRAIN mbo_queue_ (bounded batch, max B events per iteration)
      for each MarketDataEvent popped:
        assert event_time >= prev_event_time (debug builds only; hard fault on violation)
        book.apply(event)
        feature_extractor.update(event, book)
        if event is book-changing (action 'A','C','M','T'):
          → eligible for decision step (4)

  (4) DECISION (event-driven, only on book-changing events)
      gate — all conditions must hold:
        (a) book.is_valid()                  [not pending resync]
        (b) !risk.is_halted()                [no hard/soft halt]
        (c) !risk.state_.flatten_active      [not flatten-only mode]
        (d) armed == true                    [startup warm-up complete; see §8]
      if gate passes:
        state = build_MarketState(book, features, inventory, latency_state_ms)
        evaluate_actions(state, action_array)   [see §4 for all-10-slot requirement]
        best = get_optimal_action(action_array)
        if best.action != NO_TRADE:
          → submission gate (5)

  (5) SINGLE SUBMISSION GATE  [only reachable path to send_prepared_limit_order]
      function submit_gate(action, qty, price):
        precondition A: live_config validated at startup (§8 step 2); fail-closed
        precondition B: RiskManager.check_order(intent_qty):
          PASS    → send_prepared_limit_order(prepared, side, qty, price)
                    RiskManager.record_order_sent()
                    update local order table (pending)
                    log submission
          BLOCK   → drop; increment block_counter; log
          HALT    → close gate; log; no submission
          FLATTEN → flatten-only: only reduce-only intents may reach this gate;
                    all others dropped
        No other call site for send_prepared_limit_order may exist in the binary.
        Enforcement: CORRECTNESS.md (test cited there).
```

`order_queue_` is drained FULLY before any market-data processing because position
correctness is a precondition of correct decision-making; a fill received in the same
iteration as an MBO event must update risk state before that event drives a decision.

---

## 4. Action-Code Policy

Source: `packages/decision_engine/cpp/include/decision_runtime.hpp` `Action` enum (10 codes:
NO_TRADE=0, ENTER_LONG, ENTER_SHORT, ADD, REDUCE, FLATTEN, CANCEL, REPLACE,
PASSIVE_JOIN, MARKETABLE_LIMIT; `_COUNT=10`).

4.1 **evaluate_actions MUST write all 10 slots every call.** The current implementation
    (`packages/decision_engine/cpp/src/decision_runtime.cpp`) writes only slots 0–2 and
    leaves slots 3–9 uninitialized; `get_optimal_action` scans all 10, producing
    uninitialized-read undefined behavior. **This is a known bug requiring a fix before
    live deployment.** Fix: for each slot i in [3, 9], write
    `out_actions[i] = {static_cast<Action>(i), NEG_INFINITY_SENTINEL, 0.0, 0.0, 0.0}`
    where `NEG_INFINITY_SENTINEL = -std::numeric_limits<double>::infinity()`.

4.2 **v1 strategy-selectable set:** {NO_TRADE, ENTER_LONG, ENTER_SHORT}. Only these
    three codes may be returned as the argmax in v1. All other codes receive
    `expected_value = -∞` sentinel so they never win the argmax scan.

4.3 **FLATTEN and CANCEL are not EV-selectable.** These actions are reachable only via
    risk/safety paths (RiskManager `flatten_active`, SafetyPoller halt, supervisor
    CANCEL_ALL_AND_HALT). They must never be returned by `get_optimal_action` in normal
    trading. If FLATTEN or CANCEL appear as argmax, it is a misconfiguration defect.

4.4 **Enablement gate for additional action codes:** Each code beyond {NO_TRADE,
    ENTER_LONG, ENTER_SHORT} requires:
    - A calibrated EV model: EV_t(a) = P_fill · E[PnL|fill] − (1−P_fill) · C_miss − Costs
      (per BLUEPRINT.md EV decomposition).
    - Replay coverage: the action must be exercised in replay across all walk-forward
      periods (PIPELINE.md §5: Discovery 2018–2020, Confirmation 2021–2022,
      Holdout 2023–2024, Recent holdout 2025).
    - A parity test confirming C++ and Python EV computation agree to within 1e-9.
    - Re-certification (T2 full, T4 champion gate) after enablement.

---

## 5. Failure-Action Table

Sources: `rithmic_gateway/include/safety_poller.hpp` (poll() ordering, FailureState mapping,
ack protocol, sticky hard-halt); `rithmic_gateway/include/rithmic_adapter.hpp` (6 atomic
safety flags, drop counters); `risk_engine/include/risk_manager.hpp`
(RiskStatus/FailureState enums, RiskState atomics).

SafetyPoller.poll() ordering (most severe first; early-exit safe): (1) `order_halt`/`auto_liquidate_halt` → hard halt; (2) `position_desync`/`order_desync` → flatten (skip if hard-halted); (3) `md_data_gap` → book resync; (4) `adm_alert_severity` ≥ 2 → escalation; (5) drop deltas → telemetry.

### 5.1 Adapter Flag Rows

| Trigger | Condition | FailureState | Atomics set in RiskState | Consumer action | Ack/clear protocol | Restart required? |
|---------|-----------|-------------|--------------------------|-----------------|-------------------|------------------|
| Order-event queue drop | `order_halt() == true` | `MISSING_FILL_RECONCILIATION` | `hard_halt=true`, `halted=true` | Stop all new order submissions immediately | No clear until operator restart; `hard_halted_` sticky in SafetyPoller | Yes |
| MD queue overrun | `md_data_gap() == true` | (book resync, no FailureState call) | (none via RiskManager) | Discard in-memory book; wait for full snapshot; mark book invalid | Call `ack_book_resync()` after book rebuilt from snapshot; clears `md_data_gap_` | No |
| Trade bust ('B') | `position_desync() == true` | `POSITION_MISMATCH` | `flatten_active=true` (via handle_failure_state) | Halt new entries; schedule full position reconciliation | Call `ack_reconcile()` after reconciliation complete; clears `position_desync_`, `order_desync_` | No (after successful reconcile) |
| Modify rejected ('N') | `order_desync() == true` | `POSITION_MISMATCH` | `flatten_active=true` | Halt new entries; reconcile local order state | Call `ack_reconcile()` after reconciliation; clears both desync flags | No (after successful reconcile) |
| Broker force-flatten ('L') | `auto_liquidate_halt() == true` | `MISSING_FILL_RECONCILIATION` | `hard_halt=true`, `halted=true` | Stop all submissions; do not attempt re-entry | No clear until operator restart; sticky | Yes |
| ADM alert severity ≥ 3 | `adm_alert_severity() >= 3` | `ORDER_REJECT_ESCALATION` | `hard_halt=true`, `halted=true` | Stop all submissions; operator must investigate | No clear until operator restart; sticky | Yes |
| ADM alert severity = 2 | `adm_alert_severity() == 2` | `LATENCY_SPIKE` | `halted=true` (soft) | Block new orders (latency/throttle condition) | Not cleared automatically; operator intervention | No (if transient) |

Flags `position_desync` and `order_desync` persist until `ack_reconcile()` is called post-reconciliation (`safety_poller.hpp`).

### 5.2 Drop Counter Rows

| Trigger | PollResult field | Consumer action |
|---------|-----------------|-----------------|
| `md_drops_delta > 0` | `md_drops_delta` | Log delta; telemetry only (book resync is via `md_data_gap` flag, not counter alone) |
| `order_drops_delta > 0` | `order_drops_delta` | Log delta; telemetry only (halt is via `order_halt` flag) |

### 5.3 Python-Side Monitor Mapping

Python monitors (`packages/execution/production_safety.py`) run in the supervisor process (not the hot thread). Priority: DisconnectMonitor (1s grace) → DailyLossLimitFlatten → PositionMismatchGuard → ClockDriftMonitor → StaleDataMonitor (5 ms).

| Python monitor | C++ loop native equivalent? | Notes |
|---------------|----------------------------|-------|
| DisconnectMonitor | Partial: `order_halt` / `auto_liquidate_halt` cover broker disconnect; full TCP disconnect detected via Rithmic SDK callbacks | Supervisor-side for graceful 1s grace; C++ side for immediate halt on queue drop |
| DailyLossLimitFlatten | Yes: `RiskManager.state_.current_daily_pnl` vs `limits_.daily_loss_limit`; `FailureState::DAILY_LOSS_LIMIT` | C++ limit loaded at startup from config; Python supervisor provides cross-session accounting |
| PositionMismatchGuard | Yes: `FailureState::POSITION_MISMATCH` via `position_desync()` | C++ reconcile-required path is canonical; Python guard is belt-and-suspenders |
| ClockDriftMonitor | Yes: `FailureState::CLOCK_DRIFT` (via handle_failure_state); exchange timestamps from MarketDataEvent.timestamp_ns | C++ checks every event; Python monitor is supervisor-side |
| StaleDataMonitor (5 ms) | Yes: last-MBO-event age tracked in hot loop; stale → `FailureState::STALE_MARKET_DATA` | C++ is authoritative; Python monitor for supervisor alerting |

---

## 6. Post-Init Prohibitions

After startup (§8) completes and the loop enters run mode:

| Category | Prohibition | Enforcement |
|----------|-------------|-------------|
| Memory | No heap allocation | Debug builds: counting allocator hook aborts on any `new`/`malloc` |
| Synchronization | No mutex, no condition variable, no spinlock | Code review + TSAN in CI |
| Formatting | No `std::string`, `std::iostream`, `printf`, or any formatting function | Static analysis |
| I/O | No file or network I/O except inside Rithmic SDK `send_prepared_limit_order()` | Code review |
| Scheduling | No `sleep()`, `sched_yield()`, `nanosleep()` | Code review + TSAN |
| Clock | Only `std::chrono::steady_clock` or `rdtsc`; no `gettimeofday`, no `time()` | Code review |
| Syscalls | Only those inside Rithmic SDK send path | seccomp allowlist (future) |

---

## 7. Logging

7.1 **Format:** fixed-size POD `{uint64_t ts_ns; uint32_t event_enum; uint64_t a,b,c,d}` — 40 bytes, no allocation.

7.2 **Transport:** dedicated SPSC ring (pre-allocated at startup). Hot thread = producer; core-3 drain thread = sole consumer.

7.3 **Overflow:** ring full → increment `log_drop_counter` atomically; never block or yield.

7.4 **Mandatory events:** every non-empty `PollResult`; every submission attempt (PASS/BLOCK/HALT/FLATTEN); every fill/bust/not-modified/auto-liquidate; book resync start/end; every `evaluate_actions()` call with best action + EV (determinism artifact: byte-identical across replays of identical inputs).

7.5 Drain thread (core 3) formats and writes; format errors do not propagate to hot thread.

---

## 8. Startup Sequence

Ordered; each step is fail-closed (abort on failure, no partial-init trading).

| Step | Action | Failure behavior |
|------|--------|-----------------|
| 1 | Parse config file and environment | Abort with error if required keys missing |
| 2 | Validate LIVE_* contract | Call `assert_live_config()` semantic equivalent in C++: require `LIVE_MAX_ORDER_SIZE`, `LIVE_DAILY_LOSS_LIMIT`, `LIVE_KILL_SWITCH`, `LIVE_RISK_ENABLED` (source: `packages/execution/safety.py` `assert_live_config()`); copy validated values into `RiskLimits` struct; abort if any missing or invalid |
| 3 | Load weights binary | Validate `magic == 0x48465433`, `feature_count ≤ 64`; compute SHA-256; compare vs deployment manifest (§DEPLOYMENT.md §1); abort on mismatch |
| 4 | Verify certification stamp | Re-read stamp JSON from bundle; assert `promotion_eligible == true` and `status == "GREEN"` and `stale == false` (source: `packages/hft3/validation/research_stamp.py`); abort if any condition fails |
| 5 | Init RiskManager | Load `RiskLimits` from validated config; zero all state; abort if limits out of range |
| 6 | Preallocate queues / order table / log ring | `mbo_queue_` and `order_queue_` (capacity 8192 each, source: `rithmic_adapter.hpp`); pre-allocate log ring; abort if any allocation fails |
| 7 | Pin threads and verify affinity | Hot thread → core 2; log-drain thread → core 3; verify via `sched_getaffinity`; abort if mismatch |
| 8 | Connect Rithmic | `RithmicAdapter.initialize()` + `connect()`; abort if connection fails |
| 9 | Login + book snapshot | Login to MD + TS plants; `subscribe_mbo()` + `warm_price_increment()`; receive full book snapshot; assert `has_account() && has_trade_route()` |
| 10 | Feature warm-up | Process N events (N configurable, minimum TBD by feature windows) without submitting orders; `armed = false` throughout |
| 11 | Enter run mode | Set `armed = true`; hot loop begins |

PAPER mode and REPLAY mode follow the same sequence; step 2 LIVE_* check is skipped in
PAPER (assert_paper_safe equivalent applied instead).

### 9. Shutdown Sequence

Ordered; invoked on SIGTERM, SIGINT, or internal hard-halt with `restart_required=false`.

1. Cancel all open orders (cancel-all sweep via Rithmic cancel_order)
2. Optional flatten: if position non-zero and shutdown is operator-initiated (not
   hard-halt), submit flatten order
3. Drain both SPSC queues until empty or 5 s timeout
4. Final reconcile: compare local position vs last known exchange position; log discrepancy
5. Flush log ring: drain all buffered records to disk
6. Disconnect Rithmic
7. Exit with defined code:
   - 0 = clean shutdown
   - 1 = operator halt (kill-switch fired)
   - 2 = hard halt (restart required)
   - 3 = startup validation failure

---

## 10. Modes

The same binary (`hft3_engine`) runs REPLAY, PAPER, and LIVE.

Mode differences are limited to the PIPELINE.md §8 sanctioned set:

| Aspect | REPLAY | PAPER | LIVE |
|--------|--------|-------|------|
| Safety monitors | Audit-only (log, no halt) | Enforce identically to LIVE | Enforce |
| Live adapters | Forbidden (`assert_replay_safe`) | Paper adapter only (`assert_paper_safe`) | Live adapter |
| LIVE_* env contract | Not checked | Not checked | Required (step 2) |
| Order submission | Simulated (no wire) | Paper broker | Exchange wire |
| Log determinism | Byte-identical on same inputs | Not guaranteed | Not guaranteed |

**REPLAY consumes an NPZ-derived event stream through the SAME hot loop** (same
queue-pop → book → feature → decision → risk → safety-check code path). The only
difference is the event source and the simulated order sink. Decision logs produced
in REPLAY mode are determinism artifacts: byte-identical output for identical inputs
is a correctness invariant (cite: §7.4 mandatory log events).

Simulation fidelity: REPLAY mode is the canonical test bed; any behavior difference
between REPLAY and LIVE that is not in the PIPELINE.md §8 sanctioned set is a defect.
