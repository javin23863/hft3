// safety_poller_syntax_check.cpp
//
// Standalone syntax check for safety_poller.hpp that does NOT require the
// Rithmic SDK (RApiPlus.h).  Compile with:
//
//   g++ -std=c++20 -fsyntax-only \
//       -I rithmic_gateway/include \
//       -I risk_engine/include \
//       rithmic_gateway/tools/safety_poller_syntax_check.cpp
//
// (run from the repository root)
//
// The file must produce zero errors and zero warnings from GCC.

#include "safety_poller.hpp"   // the header under test

#include <cstdint>

// ---------------------------------------------------------------------------
// MockAdapter — duck-type of hft::RithmicAdapter exposing only the surface
// that SafetyPoller<T> uses.  No RApiPlus.h needed.
// ---------------------------------------------------------------------------
struct MockAdapter {
    // Flags
    bool order_halt_flag         = false;
    bool auto_liquidate_flag     = false;
    bool position_desync_flag    = false;
    bool order_desync_flag       = false;
    bool md_data_gap_flag        = false;
    int  adm_severity            = 0;
    uint64_t md_drops_val        = 0;
    uint64_t order_drops_val     = 0;

    // Accessors matching RithmicAdapter's const interface
    bool     order_halt()          const noexcept { return order_halt_flag; }
    bool     auto_liquidate_halt() const noexcept { return auto_liquidate_flag; }
    bool     position_desync()     const noexcept { return position_desync_flag; }
    bool     order_desync()        const noexcept { return order_desync_flag; }
    bool     md_data_gap()         const noexcept { return md_data_gap_flag; }
    int      adm_alert_severity()  const noexcept { return adm_severity; }
    uint64_t md_drops()            const noexcept { return md_drops_val; }
    uint64_t order_event_drops()   const noexcept { return order_drops_val; }

    // Clear methods
    void clear_position_desync()    noexcept { position_desync_flag = false; }
    void clear_order_desync()       noexcept { order_desync_flag    = false; }
    void clear_md_data_gap()        noexcept { md_data_gap_flag     = false; }
    void clear_order_halt()         noexcept { order_halt_flag      = false; }
    void clear_auto_liquidate_halt() noexcept { auto_liquidate_flag = false; }
    void clear_adm_alert_severity() noexcept { adm_severity         = 0; }
};

// ---------------------------------------------------------------------------
// Verify the template instantiates and basic PollResult fields are accessible.
// This is a compile-time-only check — no main() needed for -fsyntax-only,
// but we provide one so the file also compiles (not just parses) cleanly.
// ---------------------------------------------------------------------------
int main() {
    hft::risk::RiskLimits limits{};
    hft::risk::RiskManager risk(limits);

    MockAdapter adapter{};
    hft::gateway::SafetyPoller<MockAdapter> poller(adapter, risk);

    // Verify poll() is callable and returns a properly-typed PollResult.
    hft::gateway::PollResult r = poller.poll();
    (void)r.halted;
    (void)r.reconcile_required;
    (void)r.book_resync_required;
    (void)r.alert_severity;
    (void)r.md_drops_delta;
    (void)r.order_drops_delta;

    // Verify ack methods compile.
    poller.ack_reconcile();
    poller.ack_book_resync();
    (void)poller.is_hard_halted();

    // Exercise the order_halt path so the branch is instantiated.
    adapter.order_halt_flag = true;
    r = poller.poll();

    // Exercise the desync path.
    MockAdapter adapter2{};
    hft::risk::RiskManager risk2(limits);
    hft::gateway::SafetyPoller<MockAdapter> poller2(adapter2, risk2);
    adapter2.position_desync_flag = true;
    r = poller2.poll();
    poller2.ack_reconcile();

    // Exercise the book-resync path.
    adapter2.md_data_gap_flag = true;
    r = poller2.poll();
    poller2.ack_book_resync();

    // Exercise adm_alert_severity paths.
    MockAdapter adapter3{};
    hft::risk::RiskManager risk3(limits);
    hft::gateway::SafetyPoller<MockAdapter> poller3(adapter3, risk3);
    adapter3.adm_severity = 2;
    r = poller3.poll();
    adapter3.adm_severity = 3;
    r = poller3.poll();

    return 0;
}
