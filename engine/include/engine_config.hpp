#pragma once
// engine/include/engine_config.hpp
//
// EngineConfig — validated configuration for all modes (REPLAY / PAPER / LIVE).
//
// LIVE_* contract (CHI404_RUNTIME.md §8 step 2):
//   LIVE_MAX_ORDER_SIZE, LIVE_DAILY_LOSS_LIMIT, LIVE_KILL_SWITCH, LIVE_RISK_ENABLED
//   are required in LIVE mode; absent or invalid → fail-closed (abort).
//   In REPLAY mode these fields come from the config file / CLI with identical
//   enforce semantics; LIVE_* check is skipped only for PAPER (per §10 table).
//
// Startup step mapping (CHI404_RUNTIME.md §8):
//   Step 1  — parse config file / environment → populated by load_config()
//   Step 2  — validate LIVE_* contract       → validated by assert_live_config()
//   Step 5  — init RiskManager limits        → caller calls to_risk_limits()
//   Step 6  — pre-allocate                   → caller uses queue/ring capacities
//   Step 7  — thread-pin (LIVE-only)         → skipped in REPLAY; marked below
//   Step 8  — connect Rithmic (LIVE-only)    → skipped in REPLAY; marked below
//   Step 9  — book snapshot (LIVE-only)      → skipped in REPLAY; marked below
//   Step 10 — feature warm-up               → warmup_events field

#include <cstdint>
#include <string>
#include "risk_manager.hpp"

namespace hft {
namespace engine {

enum class EngineMode : uint8_t {
    REPLAY = 0,
    PAPER  = 1,
    LIVE   = 2,
};

struct EngineConfig {
    // --- mode ---
    EngineMode mode = EngineMode::REPLAY;

    // --- LIVE_* contract (§8 step 2; required in LIVE mode) ---
    int32_t  live_max_order_size   = 0;      // LIVE_MAX_ORDER_SIZE
    double   live_daily_loss_limit = 0.0;    // LIVE_DAILY_LOSS_LIMIT (negative = loss)
    bool     live_kill_switch      = false;  // LIVE_KILL_SWITCH
    bool     live_risk_enabled     = false;  // LIVE_RISK_ENABLED

    // --- risk limits (also used in REPLAY with identical semantics) ---
    int32_t  max_position          = 10;
    double   daily_loss_limit      = -500.0;
    uint32_t max_order_rate_per_sec = 100;
    uint32_t max_cancel_rate_per_sec = 100;

    // --- weights path (§8 step 3) ---
    std::string weights_path;

    // --- replay-specific ---
    std::string feed_path;        // flat binary feed file path
    int64_t     latency_ns = 500; // synthetic ack latency for REPLAY

    // --- warm-up (§8 step 10) ---
    uint32_t warmup_events = 200;

    // --- MBO batch size (§3 step 3) ---
    uint32_t mbo_batch_size = 64;

    // --- log ring capacity (§7) ---
    uint32_t log_ring_capacity = 65536;

    // --- log-drain watchdog (§7.6) ---
    uint64_t watchdog_check_interval_iters = 10000;
    uint64_t watchdog_stale_ns             = 5'000'000'000ULL; // 5 s

    // Convert validated config to RiskLimits for RiskManager initialisation.
    hft::risk::RiskLimits to_risk_limits() const noexcept {
        hft::risk::RiskLimits lim;
        lim.max_position            = max_position;
        lim.daily_loss_limit        = daily_loss_limit;
        lim.max_order_rate_per_sec  = max_order_rate_per_sec;
        lim.max_cancel_rate_per_sec = max_cancel_rate_per_sec;
        return lim;
    }
};

// ---------------------------------------------------------------------------
// assert_live_config (§8 step 2 C++ equivalent)
//
// Validates the LIVE_* contract.  Returns false with a human-readable
// error written to err_out if any required field is missing or invalid.
// Callers must abort on false; fail-closed is the only safe behaviour.
// In REPLAY mode this is a no-op (returns true immediately per §10).
// ---------------------------------------------------------------------------
inline bool assert_live_config(const EngineConfig& cfg, std::string& err_out) noexcept {
    if (cfg.mode != EngineMode::LIVE) {
        // REPLAY / PAPER — LIVE_* check skipped per CHI404_RUNTIME.md §10.
        return true;
    }
    if (cfg.live_max_order_size <= 0) {
        err_out = "LIVE_MAX_ORDER_SIZE missing or <= 0";
        return false;
    }
    if (cfg.live_daily_loss_limit >= 0.0) {
        err_out = "LIVE_DAILY_LOSS_LIMIT must be negative (a loss limit)";
        return false;
    }
    if (!cfg.live_kill_switch) {
        err_out = "LIVE_KILL_SWITCH not set";
        return false;
    }
    if (!cfg.live_risk_enabled) {
        err_out = "LIVE_RISK_ENABLED not set";
        return false;
    }
    return true;
}

// Load an EngineConfig from a simple key=value text file.
// Returns false on parse failure; caller must abort on false (§8 step 1).
bool load_config(const std::string& path, EngineConfig& out, std::string& err_out);

} // namespace engine
} // namespace hft
