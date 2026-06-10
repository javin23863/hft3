// test_safety_failure_injection.cpp
//
// Failure-injection test suite for SafetyPoller + RiskManager.
// Covers CORRECTNESS.md §2 row 6 and CHI404_RUNTIME.md §5 failure-action table.
//
// Compile (standalone, no CMake required):
//   g++ -std=c++20 -O2 \
//       -I rithmic_gateway/include \
//       -I risk_engine/include \
//       risk_engine/src/risk_manager.cpp \
//       rithmic_gateway/tests/test_safety_failure_injection.cpp \
//       -o build/test_safety_failure_injection.exe
//   (run from repository root)
//
// Exit codes:
//   0  all assertions passed
//   1  one or more assertions failed
//
// HEADER-vs-SPEC DISCREPANCY (recorded here; tested per header semantics as
// required by the task spec):
//
//   adm_alert_severity == 2:
//     CHI404_RUNTIME.md §5 table says consumer action "Block new orders
//     (latency/throttle condition)" and atomics set "halted=true (soft)".
//     The spec row does NOT mention PollResult.halted being set.
//     safety_poller.hpp (the header, which is current truth) does NOT set
//     result.halted = true for sev == 2.  It calls
//     handle_failure_state(LATENCY_SPIKE) which sets risk.state_.halted = true,
//     but PollResult.halted remains false.
//     CONCLUSION: PollResult.halted is false for sev==2; risk.halted is true.
//     Tests below assert the header behaviour, not the spec wording.

#include "safety_poller.hpp"

#include <cassert>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <string>
#include <thread>

// ---------------------------------------------------------------------------
// Tiny test harness
// ---------------------------------------------------------------------------

static int g_tests_run    = 0;
static int g_tests_failed = 0;

#define ASSERT_TRUE(cond)                                           \
    do {                                                            \
        ++g_tests_run;                                              \
        if (!(cond)) {                                              \
            ++g_tests_failed;                                       \
            std::cerr << "[FAIL] " << __FILE__ << ":" << __LINE__  \
                      << "  " << #cond << "\n";                     \
        }                                                           \
    } while (false)

#define ASSERT_FALSE(cond) ASSERT_TRUE(!(cond))
#define ASSERT_EQ(a, b)    ASSERT_TRUE((a) == (b))
#define ASSERT_NE(a, b)    ASSERT_TRUE((a) != (b))

// ---------------------------------------------------------------------------
// FakeAdapter — duck-type of hft::RithmicAdapter; no RApiPlus.h dependency.
// Exposes the exact accessor surface SafetyPoller reads.
// ---------------------------------------------------------------------------
struct FakeAdapter {
    // 6 flag accessors
    bool order_halt_flag          = false;
    bool auto_liquidate_flag      = false;
    bool position_desync_flag     = false;
    bool order_desync_flag        = false;
    bool md_data_gap_flag         = false;
    int  adm_severity             = 0;

    // Drop counters
    uint64_t md_drops_val         = 0;
    uint64_t order_drops_val      = 0;

    // Accessors matching RithmicAdapter's const interface
    bool     order_halt()          const noexcept { return order_halt_flag; }
    bool     auto_liquidate_halt() const noexcept { return auto_liquidate_flag; }
    bool     position_desync()     const noexcept { return position_desync_flag; }
    bool     order_desync()        const noexcept { return order_desync_flag; }
    bool     md_data_gap()         const noexcept { return md_data_gap_flag; }
    int      adm_alert_severity()  const noexcept { return adm_severity; }
    uint64_t md_drops()            const noexcept { return md_drops_val; }
    uint64_t order_event_drops()   const noexcept { return order_drops_val; }

    // Clear methods (called by SafetyPoller ack_* methods)
    void clear_position_desync()     noexcept { position_desync_flag = false; }
    void clear_order_desync()        noexcept { order_desync_flag    = false; }
    void clear_md_data_gap()         noexcept { md_data_gap_flag     = false; }
    void clear_order_halt()          noexcept { order_halt_flag      = false; }
    void clear_auto_liquidate_halt() noexcept { auto_liquidate_flag  = false; }
    void clear_adm_alert_severity()  noexcept { adm_severity         = 0; }
};

// Helper: fresh RiskLimits with defaults
static hft::risk::RiskLimits default_limits() {
    hft::risk::RiskLimits l{};
    l.max_position            = 10;
    l.daily_loss_limit        = -500.0;
    l.max_order_rate_per_sec  = 100;
    l.max_cancel_rate_per_sec = 100;
    return l;
}

// Helper: build a fresh poller + risk pair (adapter must outlive poller)
struct Fixture {
    FakeAdapter                         adapter;
    hft::risk::RiskManager              risk;
    hft::gateway::SafetyPoller<FakeAdapter> poller;

    Fixture()
        : risk(default_limits())
        , poller(adapter, risk)
    {}
};

// ---------------------------------------------------------------------------
// Group A: each of the 6 flags set alone → PollResult + RiskManager state
// ---------------------------------------------------------------------------

// A1: order_halt → hard halt
static void test_order_halt_alone() {
    Fixture f;
    f.adapter.order_halt_flag = true;
    auto r = f.poller.poll();

    // PollResult
    ASSERT_TRUE(r.halted);
    ASSERT_FALSE(r.reconcile_required);
    ASSERT_FALSE(r.book_resync_required);
    ASSERT_EQ(r.alert_severity, 0);
    ASSERT_EQ(r.md_drops_delta, 0u);
    ASSERT_EQ(r.order_drops_delta, 0u);

    // RiskManager atomics: hard_halt=true, halted=true
    // (MISSING_FILL_RECONCILIATION path: handle_failure_state sets hard_halt+halted)
    ASSERT_TRUE(f.risk.is_halted());
    ASSERT_TRUE(f.poller.is_hard_halted());
    // check_order must return HALT
    ASSERT_EQ(f.risk.check_order(1), hft::risk::RiskStatus::HALT);
}

// A2: auto_liquidate_halt → hard halt (same FailureState as order_halt)
static void test_auto_liquidate_halt_alone() {
    Fixture f;
    f.adapter.auto_liquidate_flag = true;
    auto r = f.poller.poll();

    ASSERT_TRUE(r.halted);
    ASSERT_FALSE(r.reconcile_required);
    ASSERT_FALSE(r.book_resync_required);

    ASSERT_TRUE(f.risk.is_halted());
    ASSERT_TRUE(f.poller.is_hard_halted());
    ASSERT_EQ(f.risk.check_order(1), hft::risk::RiskStatus::HALT);
}

// A3: position_desync → reconcile_required; flatten_active set, NOT hard_halt
static void test_position_desync_alone() {
    Fixture f;
    f.adapter.position_desync_flag = true;
    auto r = f.poller.poll();

    ASSERT_FALSE(r.halted);
    ASSERT_TRUE(r.reconcile_required);
    ASSERT_FALSE(r.book_resync_required);
    ASSERT_EQ(r.alert_severity, 0);

    // POSITION_MISMATCH: halted=true, flatten_active=true, hard_halt=false
    ASSERT_TRUE(f.risk.is_halted());
    ASSERT_FALSE(f.poller.is_hard_halted());
    // check_order with a non-reducing intent → FLATTEN (not HALT)
    ASSERT_EQ(f.risk.check_order(1), hft::risk::RiskStatus::FLATTEN);
}

// A4: order_desync → reconcile_required; same POSITION_MISMATCH path
static void test_order_desync_alone() {
    Fixture f;
    f.adapter.order_desync_flag = true;
    auto r = f.poller.poll();

    ASSERT_FALSE(r.halted);
    ASSERT_TRUE(r.reconcile_required);
    ASSERT_FALSE(r.book_resync_required);

    ASSERT_TRUE(f.risk.is_halted());
    ASSERT_FALSE(f.poller.is_hard_halted());
    ASSERT_EQ(f.risk.check_order(1), hft::risk::RiskStatus::FLATTEN);
}

// A5: md_data_gap → book_resync_required; no RiskManager state change
static void test_md_data_gap_alone() {
    Fixture f;
    f.adapter.md_data_gap_flag = true;
    auto r = f.poller.poll();

    ASSERT_FALSE(r.halted);
    ASSERT_FALSE(r.reconcile_required);
    ASSERT_TRUE(r.book_resync_required);
    ASSERT_EQ(r.alert_severity, 0);

    // md_data_gap alone does not call handle_failure_state → risk stays clean
    ASSERT_FALSE(f.risk.is_halted());
    ASSERT_FALSE(f.poller.is_hard_halted());
    ASSERT_EQ(f.risk.check_order(1), hft::risk::RiskStatus::PASS);
}

// A6: adm_alert_severity=3 → hard halt (ORDER_REJECT_ESCALATION)
static void test_adm_alert_sev3_alone() {
    Fixture f;
    f.adapter.adm_severity = 3;
    auto r = f.poller.poll();

    ASSERT_TRUE(r.halted);
    ASSERT_EQ(r.alert_severity, 3);
    ASSERT_FALSE(r.reconcile_required);
    ASSERT_FALSE(r.book_resync_required);

    ASSERT_TRUE(f.risk.is_halted());
    ASSERT_TRUE(f.poller.is_hard_halted());
    ASSERT_EQ(f.risk.check_order(1), hft::risk::RiskStatus::HALT);
}

// ---------------------------------------------------------------------------
// Group B: adm_alert_severity boundaries
//   sev=1: no action (header: sev < 2 → neither branch fires)
//   sev=2: LATENCY_SPIKE → risk.halted=true, hard_halt=false
//          NOTE: PollResult.halted is FALSE (header-vs-spec discrepancy; see file header)
//   sev>=3: hard halt
// ---------------------------------------------------------------------------

static void test_adm_sev0_no_action() {
    Fixture f;
    // default severity=0
    auto r = f.poller.poll();
    ASSERT_FALSE(r.halted);
    ASSERT_EQ(r.alert_severity, 0);
    ASSERT_FALSE(f.risk.is_halted());
}

static void test_adm_sev1_no_action() {
    Fixture f;
    f.adapter.adm_severity = 1;
    auto r = f.poller.poll();
    // sev=1 < 2: no branch fires in safety_poller.hpp
    ASSERT_FALSE(r.halted);
    ASSERT_EQ(r.alert_severity, 1);
    ASSERT_FALSE(f.risk.is_halted());
    ASSERT_FALSE(f.poller.is_hard_halted());
}

static void test_adm_sev2_latency_spike() {
    Fixture f;
    f.adapter.adm_severity = 2;
    auto r = f.poller.poll();

    // HEADER TRUTH: PollResult.halted is NOT set for sev==2
    // (only sev>=3 sets result.halted in safety_poller.hpp)
    ASSERT_FALSE(r.halted);
    ASSERT_EQ(r.alert_severity, 2);

    // But LATENCY_SPIKE → risk.halted = true, hard_halt = false
    ASSERT_TRUE(f.risk.is_halted());
    ASSERT_FALSE(f.poller.is_hard_halted());

    // check_order: halted=true, flatten_active=false → HALT (no flatten carve-out)
    ASSERT_EQ(f.risk.check_order(1), hft::risk::RiskStatus::HALT);
}

static void test_adm_sev3_hard_halt() {
    Fixture f;
    f.adapter.adm_severity = 3;
    auto r = f.poller.poll();
    ASSERT_TRUE(r.halted);
    ASSERT_EQ(r.alert_severity, 3);
    ASSERT_TRUE(f.poller.is_hard_halted());
    ASSERT_EQ(f.risk.check_order(1), hft::risk::RiskStatus::HALT);
}

static void test_adm_sev5_hard_halt() {
    // sev >= 3: same hard halt path
    Fixture f;
    f.adapter.adm_severity = 5;
    auto r = f.poller.poll();
    ASSERT_TRUE(r.halted);
    ASSERT_EQ(r.alert_severity, 5);
    ASSERT_TRUE(f.poller.is_hard_halted());
    ASSERT_EQ(f.risk.check_order(1), hft::risk::RiskStatus::HALT);
}

// ---------------------------------------------------------------------------
// Group C: combined flags — hard-halt sticky
//   Once hard_halt is set, subsequent polls with only lower-severity flags
//   do NOT clear or downgrade the halt.
// ---------------------------------------------------------------------------

// C1: hard halt set by order_halt; subsequent poll with only md_data_gap
//     does not downgrade halted to false in PollResult
static void test_hard_halt_sticky_after_order_halt() {
    Fixture f;
    f.adapter.order_halt_flag = true;
    auto r1 = f.poller.poll();
    ASSERT_TRUE(r1.halted);
    ASSERT_TRUE(f.poller.is_hard_halted());

    // Clear order_halt flag; set only md_data_gap (lower severity)
    f.adapter.order_halt_flag = false;
    f.adapter.md_data_gap_flag = true;
    auto r2 = f.poller.poll();

    // hard_halted_ is sticky in SafetyPoller — poller still hard-halted
    ASSERT_TRUE(f.poller.is_hard_halted());
    // PollResult.halted: order_halt() is false AND auto_liquidate_halt() is false
    // so step-1 branch produces halted=false in result for this poll.
    // But the key invariant is that handle_failure_state is NOT called again
    // (sticky guard), and check_order still returns HALT.
    ASSERT_EQ(f.risk.check_order(1), hft::risk::RiskStatus::HALT);
    // book_resync is still signaled
    ASSERT_TRUE(r2.book_resync_required);
}

// C2: hard halt set; position_desync also set — desync flag surfaces
//     in PollResult (reconcile_required=true) but no additional handle_failure_state
static void test_hard_halt_with_position_desync() {
    Fixture f;
    f.adapter.order_halt_flag      = true;
    f.adapter.position_desync_flag = true;
    auto r = f.poller.poll();

    ASSERT_TRUE(r.halted);
    // position_desync is surfaced even when hard-halted (see safety_poller.hpp §2 else branch)
    ASSERT_TRUE(r.reconcile_required);
    ASSERT_TRUE(f.poller.is_hard_halted());
    // check_order still HALT
    ASSERT_EQ(f.risk.check_order(1), hft::risk::RiskStatus::HALT);
}

// C3: sev=3 sets hard halt; next poll with sev=2 does not fire sev==2 branch
//     (hard_halted_ guard in safety_poller.hpp prevents LATENCY_SPIKE from
//     triggering on top of an existing hard halt)
static void test_sev3_then_sev2_no_extra_handle() {
    Fixture f;
    f.adapter.adm_severity = 3;
    auto r1 = f.poller.poll();
    ASSERT_TRUE(r1.halted);
    ASSERT_TRUE(f.poller.is_hard_halted());

    // Drop to sev=2
    f.adapter.adm_severity = 2;
    auto r2 = f.poller.poll();
    // sev=2 branch is guarded by !hard_halted_, so LATENCY_SPIKE not re-fired
    // PollResult.halted: sev < 3, and order/autoLiq flags are false,
    // so result.halted = false here (step-1 and step-4 >= 3 not triggered)
    ASSERT_FALSE(r2.halted);
    ASSERT_EQ(r2.alert_severity, 2);
    // But poller is still hard-halted
    ASSERT_TRUE(f.poller.is_hard_halted());
    ASSERT_EQ(f.risk.check_order(1), hft::risk::RiskStatus::HALT);
}

// C4: hard halt persists across many polls with no flags set
static void test_hard_halt_persists_clean_polls() {
    Fixture f;
    f.adapter.auto_liquidate_flag = true;
    f.poller.poll(); // sets hard halt
    f.adapter.auto_liquidate_flag = false;

    for (int i = 0; i < 10; ++i) {
        f.poller.poll(); // all flags clear
    }
    ASSERT_TRUE(f.poller.is_hard_halted());
    ASSERT_EQ(f.risk.check_order(1), hft::risk::RiskStatus::HALT);
}

// ---------------------------------------------------------------------------
// Group D: ack_reconcile() / ack_book_resync() clear protocol
//   Flag clears only via ack, not via poll.
// ---------------------------------------------------------------------------

// D1: position_desync persists across polls until ack_reconcile()
static void test_position_desync_persists_until_ack() {
    Fixture f;
    f.adapter.position_desync_flag = true;

    auto r1 = f.poller.poll();
    ASSERT_TRUE(r1.reconcile_required);
    // Flag still set on adapter
    ASSERT_TRUE(f.adapter.position_desync_flag);

    // Second poll: flag still set, still surfaces
    auto r2 = f.poller.poll();
    ASSERT_TRUE(r2.reconcile_required);
    ASSERT_TRUE(f.adapter.position_desync_flag);

    // ack_reconcile clears the adapter flag
    f.poller.ack_reconcile();
    ASSERT_FALSE(f.adapter.position_desync_flag);

    // Next poll: reconcile_required is false
    auto r3 = f.poller.poll();
    ASSERT_FALSE(r3.reconcile_required);
}

// D2: order_desync persists until ack_reconcile(); clears both desync flags
static void test_order_desync_cleared_by_ack_reconcile() {
    Fixture f;
    f.adapter.order_desync_flag = true;

    auto r1 = f.poller.poll();
    ASSERT_TRUE(r1.reconcile_required);
    ASSERT_TRUE(f.adapter.order_desync_flag);

    f.poller.ack_reconcile();
    ASSERT_FALSE(f.adapter.order_desync_flag);
    ASSERT_FALSE(f.adapter.position_desync_flag); // also cleared by ack_reconcile
}

// D3: ack_reconcile clears BOTH position_desync and order_desync simultaneously
static void test_ack_reconcile_clears_both_desync_flags() {
    Fixture f;
    f.adapter.position_desync_flag = true;
    f.adapter.order_desync_flag    = true;

    f.poller.poll();
    ASSERT_TRUE(f.adapter.position_desync_flag);
    ASSERT_TRUE(f.adapter.order_desync_flag);

    f.poller.ack_reconcile();
    ASSERT_FALSE(f.adapter.position_desync_flag);
    ASSERT_FALSE(f.adapter.order_desync_flag);
}

// D4: md_data_gap persists across polls until ack_book_resync()
static void test_md_data_gap_persists_until_ack() {
    Fixture f;
    f.adapter.md_data_gap_flag = true;

    auto r1 = f.poller.poll();
    ASSERT_TRUE(r1.book_resync_required);
    ASSERT_TRUE(f.adapter.md_data_gap_flag);

    auto r2 = f.poller.poll();
    ASSERT_TRUE(r2.book_resync_required);

    f.poller.ack_book_resync();
    ASSERT_FALSE(f.adapter.md_data_gap_flag);

    auto r3 = f.poller.poll();
    ASSERT_FALSE(r3.book_resync_required);
}

// D5: ack_book_resync does NOT clear position/order desync flags
static void test_ack_book_resync_does_not_clear_desync() {
    Fixture f;
    f.adapter.position_desync_flag = true;
    f.adapter.md_data_gap_flag     = true;

    f.poller.poll();
    f.poller.ack_book_resync();

    // md_data_gap cleared; position_desync still set
    ASSERT_FALSE(f.adapter.md_data_gap_flag);
    ASSERT_TRUE(f.adapter.position_desync_flag);
}

// D6: ack_reconcile does NOT clear md_data_gap
static void test_ack_reconcile_does_not_clear_md_data_gap() {
    Fixture f;
    f.adapter.order_desync_flag = true;
    f.adapter.md_data_gap_flag  = true;

    f.poller.poll();
    f.poller.ack_reconcile();

    ASSERT_FALSE(f.adapter.order_desync_flag);
    ASSERT_TRUE(f.adapter.md_data_gap_flag); // not cleared by ack_reconcile
}

// ---------------------------------------------------------------------------
// Group E: queue-drop counter deltas → PollResult fields
// ---------------------------------------------------------------------------

// E1: md_drops delta computed correctly between polls
static void test_md_drops_delta() {
    Fixture f;
    // Baseline: prev_md_drops_ initialised to 0 at poller construction
    f.adapter.md_drops_val = 5;
    auto r1 = f.poller.poll();
    ASSERT_EQ(r1.md_drops_delta, 5u);

    f.adapter.md_drops_val = 7;
    auto r2 = f.poller.poll();
    ASSERT_EQ(r2.md_drops_delta, 2u);

    // No new drops
    auto r3 = f.poller.poll();
    ASSERT_EQ(r3.md_drops_delta, 0u);
}

// E2: order_drops delta computed correctly
static void test_order_drops_delta() {
    Fixture f;
    f.adapter.order_drops_val = 10;
    auto r1 = f.poller.poll();
    ASSERT_EQ(r1.order_drops_delta, 10u);

    f.adapter.order_drops_val = 10; // no change
    auto r2 = f.poller.poll();
    ASSERT_EQ(r2.order_drops_delta, 0u);

    f.adapter.order_drops_val = 13;
    auto r3 = f.poller.poll();
    ASSERT_EQ(r3.order_drops_delta, 3u);
}

// E3: drop deltas do NOT set halted or reconcile_required (telemetry only)
//     per CHI404_RUNTIME.md §5.2
static void test_drop_deltas_telemetry_only() {
    Fixture f;
    f.adapter.md_drops_val    = 100;
    f.adapter.order_drops_val = 50;
    auto r = f.poller.poll();

    // Drops alone do not trigger any halt/resync path
    ASSERT_FALSE(r.halted);
    ASSERT_FALSE(r.reconcile_required);
    ASSERT_FALSE(r.book_resync_required);
    ASSERT_EQ(r.alert_severity, 0);
    ASSERT_FALSE(f.risk.is_halted());
    ASSERT_FALSE(f.poller.is_hard_halted());
}

// E4: drop delta wraps cleanly from non-zero baseline (counter monotonically increases)
static void test_md_drops_delta_from_nonzero_baseline() {
    // Simulate adapter already having 1000 drops before poller construction
    FakeAdapter adapter;
    adapter.md_drops_val = 1000;
    hft::risk::RiskManager risk(default_limits());
    hft::gateway::SafetyPoller<FakeAdapter> poller(adapter, risk);
    // poller now has prev_md_drops_=1000

    adapter.md_drops_val = 1003;
    auto r = poller.poll();
    ASSERT_EQ(r.md_drops_delta, 3u);
}

// ---------------------------------------------------------------------------
// Group F: RiskManager.check_order direct tests
// ---------------------------------------------------------------------------

// F1: position limit breach → BLOCK
static void test_check_order_position_limit_block() {
    hft::risk::RiskLimits lim{};
    lim.max_position           = 5;
    lim.daily_loss_limit       = -1e9; // won't trigger
    lim.max_order_rate_per_sec = 1000;
    hft::risk::RiskManager rm(lim);

    // Push position to the limit
    rm.update_position(5, 0.0);

    // Buying 1 more would put |position| at 6 > max_position=5 → BLOCK
    ASSERT_EQ(rm.check_order(1), hft::risk::RiskStatus::BLOCK);
    // Selling is fine (reduces position towards 0)
    ASSERT_EQ(rm.check_order(-1), hft::risk::RiskStatus::PASS);
}

// F2: rate limit breach → BLOCK
static void test_check_order_rate_limit_block() {
    hft::risk::RiskLimits lim{};
    lim.max_position           = 10000;
    lim.daily_loss_limit       = -1e9;
    lim.max_order_rate_per_sec = 3; // very low for test
    hft::risk::RiskManager rm(lim);

    // Fill the rate window
    for (int i = 0; i < 3; ++i) {
        ASSERT_EQ(rm.check_order(1), hft::risk::RiskStatus::PASS);
        rm.record_order_sent();
    }

    // Next order hits the rate limit (count_in_window >= max_order_rate_per_sec)
    ASSERT_EQ(rm.check_order(1), hft::risk::RiskStatus::BLOCK);
}

// F3: post-hard-halt → HALT regardless of qty
static void test_check_order_post_halt_returns_halt() {
    hft::risk::RiskLimits lim{};
    lim.max_position           = 100;
    lim.daily_loss_limit       = -1e9;
    lim.max_order_rate_per_sec = 1000;
    hft::risk::RiskManager rm(lim);

    // Trigger hard halt via MISSING_FILL_RECONCILIATION
    rm.handle_failure_state(hft::risk::FailureState::MISSING_FILL_RECONCILIATION);

    ASSERT_EQ(rm.check_order(1),   hft::risk::RiskStatus::HALT);
    ASSERT_EQ(rm.check_order(-1),  hft::risk::RiskStatus::HALT);
    ASSERT_EQ(rm.check_order(0),   hft::risk::RiskStatus::HALT);
}

// F4: POSITION_MISMATCH → flatten_active → FLATTEN for non-reducing orders;
//     PASS for strictly reduce-only
static void test_check_order_flatten_active_reduce_only_pass() {
    hft::risk::RiskLimits lim{};
    lim.max_position           = 100;
    lim.daily_loss_limit       = -1e9;
    lim.max_order_rate_per_sec = 1000;
    hft::risk::RiskManager rm(lim);

    // Set a known position
    rm.update_position(5, 0.0); // long 5

    // Trigger flatten via POSITION_MISMATCH
    rm.handle_failure_state(hft::risk::FailureState::POSITION_MISMATCH);

    // Buying more → FLATTEN
    ASSERT_EQ(rm.check_order(1),  hft::risk::RiskStatus::FLATTEN);
    // Selling 5 (strictly reduce) → PASS (reduce-only carve-out)
    ASSERT_EQ(rm.check_order(-5), hft::risk::RiskStatus::PASS);
    // Selling more than position → FLATTEN (would flip sign: not strictly reduce)
    ASSERT_EQ(rm.check_order(-6), hft::risk::RiskStatus::FLATTEN);
}

// F5: LATENCY_SPIKE → halted=true but NOT hard_halt; check_order returns HALT
//     (halted=true, flatten_active=false → generic HALT branch)
static void test_check_order_latency_spike_halt() {
    hft::risk::RiskLimits lim{};
    lim.max_position           = 100;
    lim.daily_loss_limit       = -1e9;
    lim.max_order_rate_per_sec = 1000;
    hft::risk::RiskManager rm(lim);

    rm.handle_failure_state(hft::risk::FailureState::LATENCY_SPIKE);

    // halted=true, flatten_active=false → HALT (no flatten carve-out)
    ASSERT_EQ(rm.check_order(1), hft::risk::RiskStatus::HALT);
}

// F6: daily loss limit breach → FLATTEN returned from check_order
static void test_check_order_daily_loss_limit_flatten() {
    hft::risk::RiskLimits lim{};
    lim.max_position           = 100;
    lim.daily_loss_limit       = -10.0; // tight limit
    lim.max_order_rate_per_sec = 1000;
    hft::risk::RiskManager rm(lim);

    // Drive PnL below limit
    rm.update_position(0, -20.0); // pnl_impact = -20

    ASSERT_EQ(rm.check_order(1), hft::risk::RiskStatus::FLATTEN);
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

int main() {
    std::cout << "[safety_failure_injection] Running tests...\n";

    // Group A: each of the 6 flags alone
    test_order_halt_alone();
    test_auto_liquidate_halt_alone();
    test_position_desync_alone();
    test_order_desync_alone();
    test_md_data_gap_alone();
    test_adm_alert_sev3_alone();

    // Group B: adm_alert_severity boundaries
    test_adm_sev0_no_action();
    test_adm_sev1_no_action();
    test_adm_sev2_latency_spike();
    test_adm_sev3_hard_halt();
    test_adm_sev5_hard_halt();

    // Group C: hard-halt sticky / combined flags
    test_hard_halt_sticky_after_order_halt();
    test_hard_halt_with_position_desync();
    test_sev3_then_sev2_no_extra_handle();
    test_hard_halt_persists_clean_polls();

    // Group D: ack clear protocol
    test_position_desync_persists_until_ack();
    test_order_desync_cleared_by_ack_reconcile();
    test_ack_reconcile_clears_both_desync_flags();
    test_md_data_gap_persists_until_ack();
    test_ack_book_resync_does_not_clear_desync();
    test_ack_reconcile_does_not_clear_md_data_gap();

    // Group E: drop counter deltas
    test_md_drops_delta();
    test_order_drops_delta();
    test_drop_deltas_telemetry_only();
    test_md_drops_delta_from_nonzero_baseline();

    // Group F: RiskManager.check_order direct
    test_check_order_position_limit_block();
    test_check_order_rate_limit_block();
    test_check_order_post_halt_returns_halt();
    test_check_order_flatten_active_reduce_only_pass();
    test_check_order_latency_spike_halt();
    test_check_order_daily_loss_limit_flatten();

    if (g_tests_failed == 0) {
        std::cout << "[safety_failure_injection] ALL " << g_tests_run << " tests PASSED\n";
        return 0;
    } else {
        std::cout << "[safety_failure_injection] FAILED " << g_tests_failed
                  << " / " << g_tests_run << " tests\n";
        return 1;
    }
}
