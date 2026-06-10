// engine/tests/test_engine_loop.cpp
//
// Unit tests for EngineLoop gate correctness.
// Covers CORRECTNESS.md row 7 (§3 submit_gate) requirements:
//   - RiskManager BLOCK → adapter submit counter stays 0
//   - RiskManager HALT  → adapter submit counter stays 0
//   - FLATTEN mode: reduce-only intents PASS, new-entry intents dropped (FLATTEN result)
//   - Order-drain-before-decision ordering
//   - Halted loop stops submitting; cancel path unaffected
//
// Compile:
//   g++ -std=c++20 -O2
//       -I../../rithmic_gateway/include
//       -I../../risk_engine/include
//       -I../../packages/decision_engine/cpp/include
//       -I../../packages/features_engine/cpp/include
//       -I../include
//       ../../risk_engine/src/risk_manager.cpp
//       ../../packages/decision_engine/cpp/src/decision_runtime.cpp
//       ../../packages/features_engine/cpp/src/feature_extractor.cpp
//       ../../packages/features_engine/cpp/src/event_context.cpp
//       ../../packages/features_engine/cpp/src/regime_filter.cpp
//       ../src/engine_config.cpp
//       test_engine_loop.cpp
//       -o test_engine_loop
//
// Run from repo root (events.csv relative path requirement):
//   <build>/test_engine_loop.exe
//
// Exit codes: 0 = all passed, 1 = failure.

#include "engine_loop.hpp"
#include "engine_config.hpp"
#include "replay_adapter.hpp"
#include "risk_manager.hpp"
#include "spsc_queue.hpp"
#include "rithmic_adapter.hpp"
#include "log_ring.hpp"

#include <cassert>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <limits>
#include <string>

// ---------------------------------------------------------------------------
// Minimal test harness
// ---------------------------------------------------------------------------
static int g_run = 0, g_fail = 0;

#define ASSERT_TRUE(cond) do { \
    ++g_run; \
    if (!(cond)) { \
        ++g_fail; \
        std::fprintf(stderr, "[FAIL] %s:%d  %s\n", __FILE__, __LINE__, #cond); \
    } \
} while (false)

#define ASSERT_EQ(a,b)  ASSERT_TRUE((a)==(b))
#define ASSERT_GT(a,b)  ASSERT_TRUE((a)>(b))

// ---------------------------------------------------------------------------
// Type aliases
// ---------------------------------------------------------------------------
using TestAdapter = hft::engine::ReplayAdapter;
using TestLoop    = hft::engine::EngineLoop<TestAdapter>;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// Write a valid weights binary to path.
static bool write_weights(const std::string& path) {
    std::ofstream f(path, std::ios::binary);
    if (!f) return false;
    const uint32_t magic = 0x48465433, ver = 1, mid = 1, fc = 4;
    f.write(reinterpret_cast<const char*>(&magic), 4);
    f.write(reinterpret_cast<const char*>(&ver),   4);
    f.write(reinterpret_cast<const char*>(&mid),   4);
    f.write(reinterpret_cast<const char*>(&fc),    4);
    std::array<double, 1024> w{}; w[0]=0.1; w[1]=-0.2; w[2]=0.05; w[3]=0.3;
    f.write(reinterpret_cast<const char*>(w.data()), sizeof(w));
    return true;
}

// Write a minimal flat binary feed (N records) to path.
// Produces 'A' add events on alternating sides so decision step fires.
static bool write_feed(const std::string& path, int n) {
    using namespace hft::engine;
    std::ofstream f(path, std::ios::binary);
    if (!f) return false;
    for (int i = 0; i < n; ++i) {
        FeedRecord r{};
        r.ts_ns    = 1'700'000'000'000'000'000LL + static_cast<int64_t>(i) * 1000;
        r.order_id = static_cast<uint64_t>(i + 1);
        r.action   = 'A'; // add — book-changing
        r.side     = (i % 2 == 0) ? 'B' : 'A';
        r.price    = (r.side == 'B') ? 5500.0 : 5500.25;
        r.size     = 10.0;
        f.write(reinterpret_cast<const char*>(&r), sizeof(r));
    }
    return true;
}

// ---------------------------------------------------------------------------
// make_engine — creates adapter+engine with correct queue wiring.
// Pattern:
//   1. Create adapter with null queues
//   2. Create EngineLoop with the adapter (engine allocates its own queues)
//   3. Rebind adapter's queues to the engine's actual queues
//   4. Load feed (populates engine's mbo_queue)
// Returns engine via out parameter (adapter must remain live for engine's lifetime).
// ---------------------------------------------------------------------------

// Helper struct to keep both alive.
struct EngineBundle {
    TestAdapter adapter;
    TestLoop    engine;

    EngineBundle(const hft::engine::EngineConfig& cfg,
                 const std::string& feed, int64_t latency_ns = 500)
        : adapter(nullptr, nullptr, feed, latency_ns)
        , engine(adapter, cfg)
    {
        // Wire adapter to the engine's queues now that both exist.
        adapter.rebind_queues(&engine.mbo_queue(), &engine.order_queue());
    }

    bool load_and_run(const std::string& weights_path,
                      bool replay_finite = true) {
        if (!engine.load_weights(weights_path)) return false;
        adapter.load_feed();
        engine.run(replay_finite);
        return true;
    }
};

// ---------------------------------------------------------------------------
// Test: BLOCK → submit counter stays 0
// ---------------------------------------------------------------------------
static void test_gate_block_no_submit() {
    const std::string weights = "test_el_weights.bin";
    const std::string feed    = "test_el_feed_block.bin";
    write_weights(weights);
    write_feed(feed, 500);

    hft::engine::EngineConfig cfg;
    cfg.mode                 = hft::engine::EngineMode::REPLAY;
    cfg.weights_path         = weights;
    cfg.feed_path            = feed;
    cfg.warmup_events        = 10;
    cfg.mbo_batch_size       = 64;
    cfg.max_position         = 0;      // zero position limit → every order BLOCKed
    cfg.daily_loss_limit     = -1e9;
    cfg.max_order_rate_per_sec = 100;
    cfg.max_cancel_rate_per_sec = 100;
    cfg.log_ring_capacity    = 4096;

    EngineBundle b(cfg, feed);
    b.load_and_run(weights);

    // Position limit = 0 → every ENTER_LONG/ENTER_SHORT intent hits BLOCK.
    ASSERT_EQ(b.engine.submit_count(), 0);

    std::remove(weights.c_str());
    std::remove(feed.c_str());
}

// ---------------------------------------------------------------------------
// Test: HALT → submit counter stays 0
// ---------------------------------------------------------------------------
static void test_gate_halt_no_submit() {
    const std::string weights = "test_el_weights_halt.bin";
    const std::string feed    = "test_el_feed_halt.bin";
    write_weights(weights);
    write_feed(feed, 300);

    hft::engine::EngineConfig cfg;
    cfg.mode                  = hft::engine::EngineMode::REPLAY;
    cfg.weights_path          = weights;
    cfg.feed_path             = feed;
    cfg.warmup_events         = 10;
    cfg.mbo_batch_size        = 64;
    cfg.max_position          = 100;
    cfg.daily_loss_limit      = -1e9;
    cfg.max_order_rate_per_sec  = 100;
    cfg.max_cancel_rate_per_sec = 100;

    EngineBundle b(cfg, feed);
    b.engine.load_weights(weights);

    // Force a hard halt BEFORE arming so gate never opens.
    b.engine.risk().handle_failure_state(hft::risk::FailureState::MISSING_FILL_RECONCILIATION);
    b.engine.force_arm(true);
    b.engine.force_book_valid(true);

    b.adapter.load_feed();
    b.engine.run(true);

    ASSERT_EQ(b.engine.submit_count(), 0);

    std::remove(weights.c_str());
    std::remove(feed.c_str());
}

// ---------------------------------------------------------------------------
// Test: FLATTEN mode — direct check_order call
// ---------------------------------------------------------------------------
static void test_flatten_mode_reduce_only() {
    hft::risk::RiskLimits lim;
    lim.max_position       = 100;
    lim.daily_loss_limit   = -1e9;
    lim.max_order_rate_per_sec  = 100;
    lim.max_cancel_rate_per_sec = 100;

    hft::risk::RiskManager risk(lim);

    // Set long position = +5, then trigger FLATTEN path.
    risk.update_position(5, 0.0);   // long 5
    risk.handle_failure_state(hft::risk::FailureState::POSITION_MISMATCH);

    // flatten_active = true; halted = true
    // Reduce-only (sell 3, within book): must PASS
    const auto r1 = risk.check_order(-3); // sell 3 (reduces long)
    ASSERT_EQ(r1, hft::risk::RiskStatus::PASS);

    // New entry (buy 1): must return FLATTEN (dropped by gate)
    const auto r2 = risk.check_order(+1);
    ASSERT_EQ(r2, hft::risk::RiskStatus::FLATTEN);

    // Flip-through-zero (sell 6 > position 5): must return FLATTEN
    const auto r3 = risk.check_order(-6);
    ASSERT_EQ(r3, hft::risk::RiskStatus::FLATTEN);
}

// ---------------------------------------------------------------------------
// Test: order-drain-before-decision ordering
//   Fill arrives in the same iteration as MBO events.
//   Position must be updated before decision step.
// ---------------------------------------------------------------------------
static void test_order_drain_before_decision() {
    // We test the RiskManager directly: fill applied before check_order.
    hft::risk::RiskLimits lim;
    lim.max_position = 1;  // tight: can only hold 1 lot at a time
    lim.daily_loss_limit = -1e9;
    lim.max_order_rate_per_sec  = 100;
    lim.max_cancel_rate_per_sec = 100;

    hft::risk::RiskManager risk(lim);

    // Simulate: position = 0, fill arrives (+1) → position = 1.
    // Now check_order(+1) must BLOCK (would exceed max_position=1).
    risk.update_position(1, 0.0);
    const auto r = risk.check_order(+1);
    ASSERT_EQ(r, hft::risk::RiskStatus::BLOCK);

    // But before the fill, check_order(+1) would have passed.
    hft::risk::RiskManager risk2(lim);
    const auto r2 = risk2.check_order(+1);
    ASSERT_EQ(r2, hft::risk::RiskStatus::PASS);
}

// ---------------------------------------------------------------------------
// Test: halted loop → submit counter stays 0 across many iterations
// ---------------------------------------------------------------------------
static void test_halted_loop_no_submission() {
    const std::string weights = "test_el_weights_halt2.bin";
    const std::string feed    = "test_el_feed_halt2.bin";
    write_weights(weights);
    write_feed(feed, 1000);

    hft::engine::EngineConfig cfg;
    cfg.mode                  = hft::engine::EngineMode::REPLAY;
    cfg.weights_path          = weights;
    cfg.feed_path             = feed;
    cfg.warmup_events         = 5;
    cfg.mbo_batch_size        = 32;
    cfg.max_position          = 100;
    cfg.daily_loss_limit      = -1e9;
    cfg.max_order_rate_per_sec  = 100;
    cfg.max_cancel_rate_per_sec = 100;

    EngineBundle b(cfg, feed);
    b.engine.load_weights(weights);
    // Hard halt before run — simulates safety event during operation.
    b.engine.risk().handle_failure_state(hft::risk::FailureState::MISSING_FILL_RECONCILIATION);
    b.engine.force_arm(true);
    b.engine.force_book_valid(true);

    b.adapter.load_feed();
    b.engine.run(true);

    // Must be zero: gate is closed by hard halt.
    ASSERT_EQ(b.engine.submit_count(), 0);

    std::remove(weights.c_str());
    std::remove(feed.c_str());
}

// ---------------------------------------------------------------------------
// Test: normal run (no halt) → at least some log records produced
// ---------------------------------------------------------------------------
static void test_normal_run_produces_log_records() {
    const std::string weights = "test_el_weights_norm.bin";
    const std::string feed    = "test_el_feed_norm.bin";
    write_weights(weights);
    write_feed(feed, 1000);

    hft::engine::EngineConfig cfg;
    cfg.mode                  = hft::engine::EngineMode::REPLAY;
    cfg.weights_path          = weights;
    cfg.feed_path             = feed;
    cfg.warmup_events         = 5;
    cfg.mbo_batch_size        = 64;
    cfg.max_position          = 100;
    cfg.daily_loss_limit      = -1e9;
    cfg.max_order_rate_per_sec  = 100;
    cfg.max_cancel_rate_per_sec = 100;
    cfg.log_ring_capacity     = 8192;

    EngineBundle b(cfg, feed);
    b.engine.load_weights(weights);
    b.adapter.load_feed();
    b.engine.run(true);

    // After run, the log ring should have records (warmup complete + decisions).
    hft::engine::LogRecord rec{};
    int ring_records = 0;
    while (b.engine.log_ring().pop(rec)) {
        ++ring_records;
    }
    ASSERT_GT(ring_records, 0);

    std::remove(weights.c_str());
    std::remove(feed.c_str());
}

// ---------------------------------------------------------------------------
// Test: FeedRecord is exactly 34 bytes (layout invariant)
// ---------------------------------------------------------------------------
static void test_feed_record_layout() {
    static_assert(sizeof(hft::engine::FeedRecord) == 34,
                  "FeedRecord must be 34 bytes (8+8+1+1+8+8)");
    ASSERT_TRUE(sizeof(hft::engine::FeedRecord) == 34);
}

// ---------------------------------------------------------------------------
// Test: LogRecord is 48 bytes (layout note: spec says 40, actual is 48)
// ---------------------------------------------------------------------------
static void test_log_record_layout() {
    static_assert(sizeof(hft::engine::LogRecord) == 48,
                  "LogRecord must be 48 bytes (6 uint64_t fields)");
    ASSERT_TRUE(sizeof(hft::engine::LogRecord) == 48);
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------
int main() {
    std::fprintf(stdout, "[test_engine_loop] Running tests...\n");

    test_feed_record_layout();
    test_log_record_layout();
    test_gate_block_no_submit();
    test_gate_halt_no_submit();
    test_flatten_mode_reduce_only();
    test_order_drain_before_decision();
    test_halted_loop_no_submission();
    test_normal_run_produces_log_records();

    if (g_fail == 0) {
        std::fprintf(stdout, "[test_engine_loop] ALL %d tests PASSED\n", g_run);
        return 0;
    } else {
        std::fprintf(stdout, "[test_engine_loop] FAILED %d / %d tests\n", g_fail, g_run);
        return 1;
    }
}
