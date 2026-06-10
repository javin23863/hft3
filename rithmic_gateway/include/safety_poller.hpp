#pragma once

// safety_poller.hpp
//
// Polls the RithmicAdapter safety flags and counter deltas on every consumer-loop
// iteration and maps them to the appropriate RiskManager failure states.
//
// Design constraints
//   - poll() must be allocation-free and I/O-free; it is called in the hot loop.
//   - No RApiPlus.h dependency.  SafetyPoller is templated on the adapter type so
//     the header parses cleanly without the vendor SDK on the include path.  In
//     production the template is instantiated with hft::RithmicAdapter.
//   - Clear methods on the adapter are called only after the caller has
//     acknowledged the condition via the ack_*() methods exposed here.

#include <cstdint>

#include "risk_manager.hpp"

namespace hft {
namespace gateway {

// ---------------------------------------------------------------------------
// PollResult — plain aggregate, no heap allocation, returned by value.
// ---------------------------------------------------------------------------
struct PollResult {
    // True when order_halt() or auto_liquidate_halt() fired and
    // handle_failure_state(MISSING_FILL_RECONCILIATION) was called.
    // The consumer must stop sending new orders immediately.
    bool halted              = false;

    // True when position_desync() or order_desync() is set.
    // The consumer must schedule a full reconciliation.  The flag is NOT
    // cleared on the adapter until ack_reconcile() is called.
    bool reconcile_required  = false;

    // True when md_data_gap() is set.  The consumer must discard its
    // in-memory order book and wait for a full snapshot before trusting
    // quotes again.  The flag is NOT cleared until ack_book_resync() is
    // called.
    bool book_resync_required = false;

    // Highest adm_alert_severity seen this iteration (0 = none).
    int  alert_severity      = 0;

    // Incremental drops since the previous poll() call.
    uint64_t md_drops_delta          = 0;
    uint64_t order_drops_delta       = 0;
};

// ---------------------------------------------------------------------------
// SafetyPoller
//
// Template parameter AdapterT must expose the same interface as
// hft::RithmicAdapter (see rithmic_adapter.hpp).  Templating avoids a
// transitive dependency on RApiPlus.h when this header is included in
// translation units that do not link against the Rithmic SDK.
// ---------------------------------------------------------------------------
template <typename AdapterT>
class SafetyPoller {
public:
    // Both references must outlive this object.
    SafetyPoller(AdapterT& adapter, hft::risk::RiskManager& risk)
        : adapter_(adapter)
        , risk_(risk)
        , prev_md_drops_(adapter.md_drops())
        , prev_order_drops_(adapter.order_event_drops())
        , hard_halted_(false)
    {}

    // -----------------------------------------------------------------------
    // poll()
    //
    // Called once per consumer-loop iteration.  Reads all safety flags and
    // counter values from the adapter, maps them to RiskManager failure states,
    // and returns a PollResult for the caller to act on.
    //
    // Ordering of checks (most severe first so early-exit is safe):
    //   1. order_halt / auto_liquidate_halt  → hard halt
    //   2. position_desync / order_desync    → soft halt / flatten  (skipped if already hard-halted)
    //   3. md_data_gap                       → book resync required
    //   4. adm_alert_severity >= 2           → ORDER_REJECT_ESCALATION (sev 3) or LATENCY_SPIKE (sev 2)
    //   5. drop counters                     → delta telemetry only
    // -----------------------------------------------------------------------
    PollResult poll() noexcept {
        PollResult result{};

        // --- 1. Order halt / auto-liquidate halt ----------------------------
        if (adapter_.order_halt() || adapter_.auto_liquidate_halt()) {
            if (!hard_halted_) {
                // Order state is unknown; hard halt, no reduce-only carve-out.
                risk_.handle_failure_state(
                    hft::risk::FailureState::MISSING_FILL_RECONCILIATION);
                hard_halted_ = true;
            }
            result.halted = true;
        }

        // --- 2. Position / order desync -------------------------------------
        // Only act if we are not already in a hard halt.  A hard halt already
        // prevents all order activity; triggering an additional POSITION_MISMATCH
        // flatten path on top of that is harmless but unnecessary.
        if (!hard_halted_) {
            if (adapter_.position_desync() || adapter_.order_desync()) {
                risk_.handle_failure_state(
                    hft::risk::FailureState::POSITION_MISMATCH);
                result.reconcile_required = true;
                // Do NOT clear the adapter flags here.  They are cleared only
                // when the caller calls ack_reconcile() after completing
                // reconciliation.
            }
        } else if (adapter_.position_desync() || adapter_.order_desync()) {
            // Already hard-halted; surface the flag so the caller can log it,
            // but do not call handle_failure_state again.
            result.reconcile_required = true;
        }

        // --- 3. Market-data gap ---------------------------------------------
        if (adapter_.md_data_gap()) {
            result.book_resync_required = true;
            // Adapter flag cleared only via ack_book_resync().
        }

        // --- 4. ADM alert severity ------------------------------------------
        const int sev = adapter_.adm_alert_severity();
        result.alert_severity = sev;
        if (sev >= 3) {
            // Severity 3: treat as a hard account/connectivity fault.
            if (!hard_halted_) {
                risk_.handle_failure_state(
                    hft::risk::FailureState::ORDER_REJECT_ESCALATION);
                hard_halted_ = true;
            }
            result.halted = true;
        } else if (sev == 2) {
            // Severity 2: block new orders (latency / throttle condition).
            // Does not override a hard halt already in effect.
            if (!hard_halted_) {
                risk_.handle_failure_state(
                    hft::risk::FailureState::LATENCY_SPIKE);
            }
        }

        // --- 5. Drop counter deltas (telemetry only) -------------------------
        const uint64_t cur_md    = adapter_.md_drops();
        const uint64_t cur_order = adapter_.order_event_drops();
        result.md_drops_delta    = cur_md    - prev_md_drops_;
        result.order_drops_delta = cur_order - prev_order_drops_;
        prev_md_drops_    = cur_md;
        prev_order_drops_ = cur_order;

        return result;
    }

    // -----------------------------------------------------------------------
    // ack_reconcile()
    //
    // Call after the caller has completed position / order state
    // reconciliation.  Clears position_desync and order_desync on the adapter
    // so they do not re-trigger on the next poll.
    // -----------------------------------------------------------------------
    void ack_reconcile() noexcept {
        adapter_.clear_position_desync();
        adapter_.clear_order_desync();
    }

    // -----------------------------------------------------------------------
    // ack_book_resync()
    //
    // Call after the caller has rebuilt its order book from a fresh snapshot.
    // Clears md_data_gap on the adapter.
    // -----------------------------------------------------------------------
    void ack_book_resync() noexcept {
        adapter_.clear_md_data_gap();
    }

    // Whether a hard halt has been declared by this poller instance.
    bool is_hard_halted() const noexcept { return hard_halted_; }

private:
    AdapterT&                  adapter_;
    hft::risk::RiskManager&    risk_;

    uint64_t prev_md_drops_;
    uint64_t prev_order_drops_;

    // Sticky: once true, avoid re-triggering handle_failure_state on every
    // subsequent iteration for the same underlying fault.
    bool hard_halted_;
};

} // namespace gateway
} // namespace hft
