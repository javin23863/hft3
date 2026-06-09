#include "risk_manager.hpp"
#include <cmath>
#include <chrono>

namespace hft {
namespace risk {

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

int64_t RiskManager::now_ns() noexcept {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
               std::chrono::steady_clock::now().time_since_epoch())
        .count();
}

// ---------------------------------------------------------------------------
// Construction
// ---------------------------------------------------------------------------

RiskManager::RiskManager(const RiskLimits& limits) : limits_(limits) {
    state_.halted.store(false, std::memory_order_release);
    state_.flatten_active.store(false, std::memory_order_release);
    state_.hard_halt.store(false, std::memory_order_release);
}

// ---------------------------------------------------------------------------
// check_order
//
// Reduce-only carve-out (finding 2.1 C++ half):
//   When flatten_active is set (loss-limit / position-mismatch flatten path),
//   orders that strictly reduce |position| are allowed so the engine can exit
//   its book.  "Strictly reduce" means:
//     - The sign of intent_qty is opposite to current_position, AND
//     - |intent_qty| <= |current_position|  (no flip through zero).
//   All other orders remain blocked under flatten.
//
//   Under hard_halt (integrity-level stop: fill reconciliation unknown, etc.)
//   everything is blocked, including reduce-only, because the true position
//   is unknown.
// ---------------------------------------------------------------------------

RiskStatus RiskManager::check_order(int32_t intent_qty) const noexcept {
    // 0. Hard-halt check — integrity stop, no carve-outs.
    if (state_.hard_halt.load(std::memory_order_acquire)) [[unlikely]] {
        return RiskStatus::HALT;
    }

    // 1. Soft-halt (flatten) check with reduce-only carve-out.
    if (state_.halted.load(std::memory_order_acquire)) [[unlikely]] {
        if (!state_.flatten_active.load(std::memory_order_acquire)) {
            // Generic halt with no flatten path — block everything.
            return RiskStatus::HALT;
        }

        // Flatten is active: allow only orders that strictly reduce |position|.
        int32_t pos = state_.current_position.load(std::memory_order_relaxed);
        bool opposite_side = (pos > 0 && intent_qty < 0) || (pos < 0 && intent_qty > 0);
        bool within_book   = std::abs(intent_qty) <= std::abs(pos);
        if (pos != 0 && opposite_side && within_book) {
            // Reduce-only order: allow through rate and position checks below,
            // but skip the position-limit check (we're unwinding, not growing).
            int64_t ts = now_ns();
            if (state_.order_rate.count_in_window(ts) >= limits_.max_order_rate_per_sec) [[unlikely]] {
                return RiskStatus::BLOCK;
            }
            return RiskStatus::PASS;
        }
        return RiskStatus::FLATTEN;
    }

    // 2. Daily loss limit check (triggers soft-halt + flatten on next cycle;
    //    return FLATTEN immediately so the caller knows the mode).
    if (state_.current_daily_pnl.load(std::memory_order_relaxed) <= limits_.daily_loss_limit) [[unlikely]] {
        // We do NOT print to std::cerr here. Hot path must be branch-optimized and non-blocking.
        // A separate telemetry thread will read this state lock-free.
        return RiskStatus::FLATTEN;
    }

    // 3. Max position check
    int32_t pos = state_.current_position.load(std::memory_order_relaxed);
    if (std::abs(pos + intent_qty) > limits_.max_position) [[unlikely]] {
        return RiskStatus::BLOCK;
    }

    // 4. Order rate limit check (sliding window)
    int64_t ts = now_ns();
    if (state_.order_rate.count_in_window(ts) >= limits_.max_order_rate_per_sec) [[unlikely]] {
        return RiskStatus::BLOCK;
    }

    return RiskStatus::PASS;
}

// ---------------------------------------------------------------------------
// check_cancel (finding 2.5 item 1)
//
// Symmetrical rate-limit gate for cancel messages.  Returns BLOCK if the
// sliding-window cancel count for the last second meets or exceeds the
// configured max_cancel_rate_per_sec.  Callers must call record_cancel_sent()
// after the cancel is dispatched.
//
// Cancels are not gated by halt/flatten state: the engine must be able to
// cancel existing orders even while unwinding.
// ---------------------------------------------------------------------------

RiskStatus RiskManager::check_cancel() const noexcept {
    int64_t ts = now_ns();
    if (state_.cancel_rate.count_in_window(ts) >= limits_.max_cancel_rate_per_sec) [[unlikely]] {
        return RiskStatus::BLOCK;
    }
    return RiskStatus::PASS;
}

// ---------------------------------------------------------------------------
// update_position
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// handle_failure_state (finding 2.5 item 2)
//
// Mappings:
//   STALE_MARKET_DATA, RITHMIC_DISCONNECT, CLOCK_DRIFT
//       -> hard HALT (no carve-out; connectivity / clock integrity unknown)
//   LATENCY_SPIKE
//       -> BLOCK (reject new orders; do not halt outright — latency may recover)
//          We set halted=true so check_order returns early, but flatten_active
//          and hard_halt remain false so the caller can re-enable via explicit
//          reset if latency recovers.  This is a conservative pause, not a full
//          integrity stop.
//   MISSING_FILL_RECONCILIATION
//       -> hard HALT (position state unknown; even reduce-only is unsafe)
//   ORDER_REJECT_ESCALATION
//       -> hard HALT (repeated rejects imply a connectivity or account issue)
//   POSITION_MISMATCH, DAILY_LOSS_LIMIT
//       -> soft HALT + flatten_active (reduce-only carve-out applies)
// ---------------------------------------------------------------------------

RiskStatus RiskManager::handle_failure_state(FailureState state) noexcept {
    switch (state) {
        case FailureState::STALE_MARKET_DATA:
        case FailureState::RITHMIC_DISCONNECT:
        case FailureState::CLOCK_DRIFT:
            state_.hard_halt.store(true, std::memory_order_release);
            state_.halted.store(true, std::memory_order_release);
            return RiskStatus::HALT;

        case FailureState::LATENCY_SPIKE:
            // Pause new orders but do not declare an integrity stop.  A latency
            // spike does not invalidate position state; the engine can resume
            // once the caller clears the halt after latency normalises.
            state_.halted.store(true, std::memory_order_release);
            // flatten_active and hard_halt intentionally left unchanged.
            return RiskStatus::BLOCK;

        case FailureState::MISSING_FILL_RECONCILIATION:
            // Position state is unknown — hard halt, reduce-only unsafe.
            state_.hard_halt.store(true, std::memory_order_release);
            state_.halted.store(true, std::memory_order_release);
            return RiskStatus::HALT;

        case FailureState::ORDER_REJECT_ESCALATION:
            // Escalating rejects indicate an account or connectivity fault — hard halt.
            state_.hard_halt.store(true, std::memory_order_release);
            state_.halted.store(true, std::memory_order_release);
            return RiskStatus::HALT;

        case FailureState::POSITION_MISMATCH:
        case FailureState::DAILY_LOSS_LIMIT:
            // Soft halt: position is known but we must flatten; reduce-only allowed.
            state_.halted.store(true, std::memory_order_release);
            state_.flatten_active.store(true, std::memory_order_release);
            return RiskStatus::FLATTEN;

        default:
            return RiskStatus::PASS;
    }
}

// ---------------------------------------------------------------------------
// Rate recording
// ---------------------------------------------------------------------------

void RiskManager::record_order_sent() noexcept {
    state_.order_rate.record(now_ns());
}

void RiskManager::record_cancel_sent() noexcept {
    state_.cancel_rate.record(now_ns());
}

// reset_rate_counters is a no-op for the sliding-window implementation.
// The background timer can still call it safely; it exists for API compatibility.
void RiskManager::reset_rate_counters() noexcept {
    // Sliding-window counters age out naturally; no explicit reset needed.
}

} // namespace risk
} // namespace hft
