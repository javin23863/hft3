#include "risk_manager.hpp"
#include <cmath>

namespace hft {
namespace risk {

RiskManager::RiskManager(const RiskLimits& limits) : limits_(limits) {
    state_.halted.store(false, std::memory_order_release);
}

RiskStatus RiskManager::check_order(int32_t intent_qty) const noexcept {
    // 0. Global halt check
    if (state_.halted.load(std::memory_order_acquire)) [[unlikely]] {
        return RiskStatus::HALT;
    }
    
    // 1. Daily loss limit check
    if (state_.current_daily_pnl.load(std::memory_order_relaxed) <= limits_.daily_loss_limit) [[unlikely]] {
        // We do NOT print to std::cerr here. Hot path must be branch-optimized and non-blocking.
        // A separate telemetry thread will read this state lock-free.
        return RiskStatus::FLATTEN;
    }
    
    // 2. Max position check
    int32_t pos = state_.current_position.load(std::memory_order_relaxed);
    if (std::abs(pos + intent_qty) > limits_.max_position) [[unlikely]] {
        return RiskStatus::BLOCK;
    }
    
    // 3. Order rate limit check
    if (state_.orders_this_second.load(std::memory_order_relaxed) >= limits_.max_order_rate_per_sec) [[unlikely]] {
        return RiskStatus::BLOCK;
    }
    
    return RiskStatus::PASS;
}

void RiskManager::update_position(int32_t fill_qty, double pnl_impact) noexcept {
    state_.current_position.fetch_add(fill_qty, std::memory_order_relaxed);
    
    // Atomic float addition (CAS loop)
    double current = state_.current_daily_pnl.load(std::memory_order_relaxed);
    while (!state_.current_daily_pnl.compare_exchange_weak(current, current + pnl_impact,
                                                           std::memory_order_release,
                                                           std::memory_order_relaxed)) {
        // spin
    }
}

RiskStatus RiskManager::handle_failure_state(FailureState state) noexcept {
    switch (state) {
        case FailureState::STALE_MARKET_DATA:
        case FailureState::RITHMIC_DISCONNECT:
        case FailureState::CLOCK_DRIFT:
            state_.halted.store(true, std::memory_order_release);
            return RiskStatus::HALT;
            
        case FailureState::POSITION_MISMATCH:
        case FailureState::DAILY_LOSS_LIMIT:
            state_.halted.store(true, std::memory_order_release);
            return RiskStatus::FLATTEN;
            
        default:
            return RiskStatus::PASS;
    }
}

void RiskManager::record_order_sent() noexcept {
    state_.orders_this_second.fetch_add(1, std::memory_order_relaxed);
}

void RiskManager::record_cancel_sent() noexcept {
    state_.cancels_this_second.fetch_add(1, std::memory_order_relaxed);
}

void RiskManager::reset_rate_counters() noexcept {
    state_.orders_this_second.store(0, std::memory_order_relaxed);
    state_.cancels_this_second.store(0, std::memory_order_relaxed);
}

} // namespace risk
} // namespace hft
