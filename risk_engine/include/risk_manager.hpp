#pragma once

#include <atomic>
#include <array>
#include <chrono>
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

// Sliding-window rate limiter using a fixed-size ring buffer of timestamps.
// Window width is 1 second. Thread-safety: single writer expected (order/cancel
// submission path); the hot-path reader (check_order / check_cancel) scans the
// buffer under a relaxed load and accepts a small false-positive rate on the
// window boundary rather than taking a lock.
//
// Capacity must be >= max_rate to avoid silent drops.  We size it at 256 which
// covers the default 100/s limit with comfortable headroom and fits a single
// cache line per slot (int64 timestamps).
static constexpr uint32_t RATE_WINDOW_CAPACITY = 256;

struct alignas(64) SlidingWindowCounter {
    // Ring buffer of nanosecond-epoch timestamps for each accepted event.
    std::array<std::atomic<int64_t>, RATE_WINDOW_CAPACITY> timestamps{};
    // Write cursor: next slot to overwrite (monotonically increasing mod capacity).
    std::atomic<uint32_t> write_pos{0};

    SlidingWindowCounter() {
        for (auto& ts : timestamps) {
            ts.store(0, std::memory_order_relaxed);
        }
    }

    // Returns the count of events within the last 1 second ending at `now_ns`.
    uint32_t count_in_window(int64_t now_ns) const noexcept {
        const int64_t cutoff = now_ns - 1'000'000'000LL; // 1 s in nanoseconds
        uint32_t n = 0;
        for (const auto& slot : timestamps) {
            int64_t t = slot.load(std::memory_order_relaxed);
            // t == 0 marks an empty slot; guard against counting them when
            // cutoff is negative (steady_clock close to zero).
            if (t > cutoff && t > 0) {
                ++n;
            }
        }
        return n;
    }

    // Records an event at `now_ns`.  Must only be called after the caller has
    // confirmed the rate check passed, to avoid over-counting.
    void record(int64_t now_ns) noexcept {
        uint32_t pos = write_pos.fetch_add(1, std::memory_order_relaxed) % RATE_WINDOW_CAPACITY;
        timestamps[pos].store(now_ns, std::memory_order_relaxed);
    }
};

struct alignas(64) RiskState {
    std::atomic<int32_t> current_position{0};
    std::atomic<double> current_daily_pnl{0.0};
    std::atomic<bool> halted{false};

    // Set when the system is in "flatten only" mode (loss limit or position mismatch).
    // Under this flag, reduce-only orders (strictly decreasing |position|) are still
    // permitted so the engine can exit its book.
    std::atomic<bool> flatten_active{false};

    // Set for integrity-level halts where even reduce-only activity is unsafe
    // (e.g. fill reconciliation unknown).  When true, check_order returns HALT
    // regardless of intent.
    std::atomic<bool> hard_halt{false};

    SlidingWindowCounter order_rate;
    SlidingWindowCounter cancel_rate;
};

class RiskManager {
public:
    explicit RiskManager(const RiskLimits& limits);

    // Core hot-path check before order submission. MUST be wait-free.
    // intent_qty: positive = buy, negative = sell (matches position sign convention).
    RiskStatus check_order(int32_t intent_qty) const noexcept;

    // Hot-path check before cancel submission.  Returns BLOCK if the cancel rate
    // limit for this sliding 1-second window has been reached; otherwise PASS.
    // Callers must still call record_cancel_sent() after the cancel is dispatched.
    RiskStatus check_cancel() const noexcept;

    // Updates internal state based on fills/executions
    void update_position(int32_t fill_qty, double pnl_impact) noexcept;

    // Failure state processing (triggers halts/flattens)
    RiskStatus handle_failure_state(FailureState state) noexcept;

    // Rate event recording — call after the corresponding check passes and the
    // message is actually sent to the exchange.
    void record_order_sent() noexcept;
    void record_cancel_sent() noexcept;

    // Called by a background timer thread every 1000ms.
    // No-op for the sliding-window implementation but kept for API compatibility.
    void reset_rate_counters() noexcept;

    bool is_halted() const noexcept { return state_.halted.load(std::memory_order_acquire); }
    bool is_flatten_active() const noexcept { return state_.flatten_active.load(std::memory_order_acquire); }

private:
    RiskLimits limits_;
    RiskState state_;

    // Returns current time in nanoseconds since epoch.
    static int64_t now_ns() noexcept;
};

} // namespace risk
} // namespace hft
