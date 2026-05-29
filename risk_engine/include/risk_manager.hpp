#pragma once

#include <atomic>
#include <cstdint>

namespace hft {
namespace risk {

enum class RiskStatus : uint8_t {
    PASS = 0,
    BLOCK,
    HALT,
    FLATTEN
};

enum class FailureState : uint8_t {
    NONE = 0,
    STALE_MARKET_DATA,
    RITHMIC_DISCONNECT,
    CLOCK_DRIFT,
    LATENCY_SPIKE,
    MISSING_FILL_RECONCILIATION,
    ORDER_REJECT_ESCALATION,
    POSITION_MISMATCH,
    DAILY_LOSS_LIMIT
};

// Cache-line aligned struct to prevent false sharing between hot-path threads
struct alignas(64) RiskLimits {
    int32_t max_position = 10;
    double daily_loss_limit = -500.0;
    uint32_t max_order_rate_per_sec = 100;
    uint32_t max_cancel_rate_per_sec = 100;
};

struct alignas(64) RiskState {
    std::atomic<int32_t> current_position{0};
    std::atomic<double> current_daily_pnl{0.0};
    std::atomic<uint32_t> orders_this_second{0};
    std::atomic<uint32_t> cancels_this_second{0};
    std::atomic<bool> halted{false};
};

class RiskManager {
public:
    explicit RiskManager(const RiskLimits& limits);
    
    // Core hot-path check before order submission. MUST be wait-free.
    RiskStatus check_order(int32_t intent_qty) const noexcept;
    
    // Updates internal state based on fills/executions
    void update_position(int32_t fill_qty, double pnl_impact) noexcept;
    
    // Failure state processing (triggers halts/flattens)
    RiskStatus handle_failure_state(FailureState state) noexcept;
    
    // Order rate tracking
    void record_order_sent() noexcept;
    void record_cancel_sent() noexcept;
    
    // Called by a background timer thread every 1000ms
    void reset_rate_counters() noexcept;

    bool is_halted() const noexcept { return state_.halted.load(std::memory_order_acquire); }

private:
    RiskLimits limits_;
    RiskState state_;
};

} // namespace risk
} // namespace hft
