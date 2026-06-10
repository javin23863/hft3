// test_decision_runtime_hardening.cpp
//
// Regression test for CORRECTNESS.md §3 defect (a) and
// CHI404_RUNTIME.md §4 action-code policy.
//
// Compile (standalone, no CMake required):
//   g++ -std=c++20 -O2 -I../include ../src/decision_runtime.cpp
//       test_decision_runtime_hardening.cpp -o test_decision_runtime_hardening
//
// Exit codes:
//   0  all assertions passed
//   1  one or more assertions failed

#include "decision_runtime.hpp"

#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <limits>
#include <random>

// ---------------------------------------------------------------------------
// Tiny test harness
// ---------------------------------------------------------------------------

static int g_tests_run   = 0;
static int g_tests_failed = 0;

#define ASSERT_TRUE(cond)                                          \
    do {                                                           \
        ++g_tests_run;                                             \
        if (!(cond)) {                                             \
            ++g_tests_failed;                                      \
            std::cerr << "[FAIL] " << __FILE__ << ":" << __LINE__ \
                      << "  " << #cond << "\n";                    \
        }                                                          \
    } while (false)

#define ASSERT_EQ(a, b)  ASSERT_TRUE((a) == (b))
#define ASSERT_NE(a, b)  ASSERT_TRUE((a) != (b))

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

static constexpr double NEG_INF = -std::numeric_limits<double>::infinity();
static constexpr int    NUM_ACTIONS = static_cast<int>(hft::Action::_COUNT); // 10

// Poison an ActionArray with 0xCD bytes (CORRECTNESS.md §2 row 8).
static void poison_actions(hft::ActionArray& arr) {
    std::memset(arr.data(), 0xCD, sizeof(arr));
}

// Build a minimal valid MarketState with non-zero book so the dot product runs.
static hft::MarketState make_state(hft::DecisionEngine& /*engine*/) {
    hft::MarketState s{};
    s.bid_price_1  = 5500.0;
    s.ask_price_1  = 5500.25;
    s.bid_qty_1    = 10;
    s.ask_qty_1    = 8;
    s.current_inventory = 0;
    s.latency_state_ms  = 1.0;
    for (auto& f : s.model_features) f = 0.01;
    return s;
}

// Write a minimal valid weights binary to a temp file; returns path.
// format: magic(4) version(4) model_id(4) feature_count(4) + 1024*8 bytes weights
static std::string write_valid_model(const std::string& path,
                                     uint32_t magic        = 0x48465433,
                                     uint32_t version      = 1,
                                     uint32_t model_id     = 1,
                                     uint32_t feature_count= 4,
                                     bool     truncate_weights = false) {
    std::ofstream f(path, std::ios::binary);
    f.write(reinterpret_cast<const char*>(&magic),         4);
    f.write(reinterpret_cast<const char*>(&version),       4);
    f.write(reinterpret_cast<const char*>(&model_id),      4);
    f.write(reinterpret_cast<const char*>(&feature_count), 4);
    if (!truncate_weights) {
        std::array<double, 1024> w{};
        w[0] = 0.1; w[1] = -0.2; w[2] = 0.05; w[3] = 0.3;
        f.write(reinterpret_cast<const char*>(w.data()), sizeof(w));
    }
    return path;
}

// ---------------------------------------------------------------------------
// Test group A: evaluate_actions writes all 10 slots
// ---------------------------------------------------------------------------

static void test_all_slots_written_poisoned_memory() {
    hft::DecisionEngine engine;
    hft::ActionArray arr;
    poison_actions(arr);

    hft::MarketState s = make_state(engine);
    engine.evaluate_actions(s, arr);  // engine not initialised — feature_count=0, default weights

    // Every slot must have been overwritten (no 0xCD bytes in expected_value).
    // We check that expected_value is NOT the 0xCD bit pattern.
    const double poison_double = []() -> double {
        uint64_t raw = 0xCDCDCDCDCDCDCDCDULL;
        double   d;
        std::memcpy(&d, &raw, sizeof(d));
        return d;
    }();

    for (int i = 0; i < NUM_ACTIONS; ++i) {
        ASSERT_TRUE(arr[i].expected_value != poison_double);
    }
}

static void test_promoted_slots_have_correct_action_codes() {
    hft::DecisionEngine engine;
    hft::ActionArray arr;
    poison_actions(arr);
    engine.evaluate_actions(make_state(engine), arr);

    ASSERT_EQ(arr[0].action, hft::Action::NO_TRADE);
    ASSERT_EQ(arr[1].action, hft::Action::ENTER_LONG);
    ASSERT_EQ(arr[2].action, hft::Action::ENTER_SHORT);
}

static void test_non_promoted_slots_are_neg_inf() {
    hft::DecisionEngine engine;
    hft::ActionArray arr;
    poison_actions(arr);
    engine.evaluate_actions(make_state(engine), arr);

    // Slots 3–9 must have EV = -inf (CHI404_RUNTIME.md §4.1 / §4.2).
    for (int i = 3; i < NUM_ACTIONS; ++i) {
        ASSERT_TRUE(std::isinf(arr[i].expected_value));
        ASSERT_TRUE(arr[i].expected_value < 0.0);
    }
}

static void test_non_promoted_slots_have_correct_action_codes() {
    hft::DecisionEngine engine;
    hft::ActionArray arr;
    poison_actions(arr);
    engine.evaluate_actions(make_state(engine), arr);

    // Verify the action field in each non-promoted slot matches the slot index.
    ASSERT_EQ(arr[3].action,  hft::Action::ADD);
    ASSERT_EQ(arr[4].action,  hft::Action::REDUCE);
    ASSERT_EQ(arr[5].action,  hft::Action::FLATTEN);
    ASSERT_EQ(arr[6].action,  hft::Action::CANCEL);
    ASSERT_EQ(arr[7].action,  hft::Action::REPLACE);
    ASSERT_EQ(arr[8].action,  hft::Action::PASSIVE_JOIN);
    ASSERT_EQ(arr[9].action,  hft::Action::MARKETABLE_LIMIT);
}

static void test_no_trade_ev_is_zero() {
    hft::DecisionEngine engine;
    hft::ActionArray arr;
    poison_actions(arr);
    engine.evaluate_actions(make_state(engine), arr);

    ASSERT_TRUE(arr[0].expected_value == 0.0);
}

// ---------------------------------------------------------------------------
// Test group B: get_optimal_action never returns non-promoted code
// ---------------------------------------------------------------------------

static void test_argmax_never_returns_non_promoted_random_features() {
    // Fixed seed for reproducibility (CORRECTNESS.md §2 row 8 — "fixed seed").
    std::mt19937_64 rng(0xDEADBEEF12345678ULL);
    std::uniform_real_distribution<double> feat_dist(-1.0, 1.0);

    hft::DecisionEngine engine;

    constexpr int ITERS = 10000;
    for (int iter = 0; iter < ITERS; ++iter) {
        hft::MarketState s{};
        s.bid_qty_1 = 1;
        s.ask_qty_1 = 1;
        for (auto& f : s.model_features) f = feat_dist(rng);

        hft::ActionArray arr;
        poison_actions(arr);
        engine.evaluate_actions(s, arr);

        const hft::ActionValue best = engine.get_optimal_action(arr);

        // The result must be one of the three promoted codes.
        const bool is_promoted =
            best.action == hft::Action::NO_TRADE   ||
            best.action == hft::Action::ENTER_LONG  ||
            best.action == hft::Action::ENTER_SHORT;

        ASSERT_TRUE(is_promoted);
    }
}

static void test_argmax_all_neg_inf_falls_back_to_no_trade() {
    // Degenerate case: manually set all slots to -inf except none being NO_TRADE.
    // get_optimal_action must return NO_TRADE (slot 0 seed).
    hft::DecisionEngine engine;
    hft::ActionArray arr;

    // Fill everything with -inf.
    for (int i = 0; i < NUM_ACTIONS; ++i) {
        arr[i] = {static_cast<hft::Action>(i), NEG_INF, 0.0, 0.0, 0.0};
    }
    // Even NO_TRADE is -inf here.
    const hft::ActionValue best = engine.get_optimal_action(arr);

    // Must be slot 0 (NO_TRADE) because we seed with index 0 and use strict >.
    ASSERT_EQ(best.action, hft::Action::NO_TRADE);
    ASSERT_TRUE(std::isinf(best.expected_value) && best.expected_value < 0.0);
}

static void test_argmax_negative_ev_promoted_actions_returns_no_trade() {
    // When ENTER_LONG and ENTER_SHORT both have EV < 0, NO_TRADE (EV=0) should win.
    hft::DecisionEngine engine;
    hft::ActionArray arr;
    poison_actions(arr);

    hft::MarketState s = make_state(engine);
    engine.evaluate_actions(s, arr);

    // Force both long and short EV negative to guarantee NO_TRADE wins.
    arr[1].expected_value = -1.0;
    arr[2].expected_value = -1.0;

    const hft::ActionValue best = engine.get_optimal_action(arr);
    ASSERT_EQ(best.action, hft::Action::NO_TRADE);
}

// ---------------------------------------------------------------------------
// Test group C: load_model header validation
// ---------------------------------------------------------------------------

static void test_load_wrong_magic() {
    const std::string path = "test_wrong_magic.bin";
    write_valid_model(path, /*magic=*/0xDEADBEEF);
    hft::DecisionEngine engine;
    ASSERT_TRUE(!engine.load_model(path));
    std::remove(path.c_str());
}

static void test_load_wrong_version() {
    const std::string path = "test_wrong_version.bin";
    write_valid_model(path, 0x48465433, /*version=*/99);
    hft::DecisionEngine engine;
    ASSERT_TRUE(!engine.load_model(path));
    std::remove(path.c_str());
}

static void test_load_feature_count_zero() {
    const std::string path = "test_fc_zero.bin";
    write_valid_model(path, 0x48465433, 1, 1, /*feature_count=*/0);
    hft::DecisionEngine engine;
    ASSERT_TRUE(!engine.load_model(path));
    std::remove(path.c_str());
}

static void test_load_feature_count_65_rejected() {
    const std::string path = "test_fc_65.bin";
    write_valid_model(path, 0x48465433, 1, 1, /*feature_count=*/65);
    hft::DecisionEngine engine;
    ASSERT_TRUE(!engine.load_model(path));
    std::remove(path.c_str());
}

static void test_load_feature_count_64_accepted() {
    const std::string path = "test_fc_64.bin";
    write_valid_model(path, 0x48465433, 1, 1, /*feature_count=*/64);
    hft::DecisionEngine engine;
    ASSERT_TRUE(engine.load_model(path));
    std::remove(path.c_str());
}

static void test_load_truncated_weights() {
    // Header OK but weights section missing.
    const std::string path = "test_truncated.bin";
    write_valid_model(path, 0x48465433, 1, 1, 4, /*truncate_weights=*/true);
    hft::DecisionEngine engine;
    ASSERT_TRUE(!engine.load_model(path));
    std::remove(path.c_str());
}

static void test_load_truncated_header() {
    // File shorter than header.
    const std::string path = "test_short.bin";
    {
        std::ofstream f(path, std::ios::binary);
        f.write("\x33\x54\x46\x48", 4);  // only 4 bytes (magic only, no version etc.)
    }
    hft::DecisionEngine engine;
    ASSERT_TRUE(!engine.load_model(path));
    std::remove(path.c_str());
}

static void test_load_valid_model() {
    const std::string path = "test_valid.bin";
    write_valid_model(path);
    hft::DecisionEngine engine;
    ASSERT_TRUE(engine.load_model(path));
    std::remove(path.c_str());
}

// After loading a valid model, evaluate_actions with a weighted dot product
// must still write all 10 slots and keep non-promoted at -inf.
static void test_loaded_model_still_sentinels_non_promoted() {
    const std::string path = "test_loaded_sentinel.bin";
    write_valid_model(path);
    hft::DecisionEngine engine;
    engine.load_model(path);
    std::remove(path.c_str());

    hft::ActionArray arr;
    poison_actions(arr);
    hft::MarketState s = make_state(engine);
    engine.evaluate_actions(s, arr);

    for (int i = 3; i < NUM_ACTIONS; ++i) {
        ASSERT_TRUE(std::isinf(arr[i].expected_value) && arr[i].expected_value < 0.0);
    }
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

int main() {
    std::cout << "[decision_runtime_hardening] Running tests...\n";

    // Group A: evaluate_actions writes all 10 slots
    test_all_slots_written_poisoned_memory();
    test_promoted_slots_have_correct_action_codes();
    test_non_promoted_slots_are_neg_inf();
    test_non_promoted_slots_have_correct_action_codes();
    test_no_trade_ev_is_zero();

    // Group B: get_optimal_action never returns non-promoted code
    test_argmax_never_returns_non_promoted_random_features();
    test_argmax_all_neg_inf_falls_back_to_no_trade();
    test_argmax_negative_ev_promoted_actions_returns_no_trade();

    // Group C: load_model header validation
    test_load_wrong_magic();
    test_load_wrong_version();
    test_load_feature_count_zero();
    test_load_feature_count_65_rejected();
    test_load_feature_count_64_accepted();
    test_load_truncated_weights();
    test_load_truncated_header();
    test_load_valid_model();
    test_loaded_model_still_sentinels_non_promoted();

    if (g_tests_failed == 0) {
        std::cout << "[decision_runtime_hardening] ALL " << g_tests_run << " tests PASSED\n";
        return 0;
    } else {
        std::cout << "[decision_runtime_hardening] FAILED " << g_tests_failed
                  << " / " << g_tests_run << " tests\n";
        return 1;
    }
}
