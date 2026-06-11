#pragma once

#include <string>
#include <vector>
#include <functional>
#include <cstdint>
#include <mutex>
#include <condition_variable>
#include <atomic>
#include <set>
#include "spsc_queue.hpp"

// Event-type codes used in OrderEvent::event_type.
// The gateway consumer must handle all of these:
//   'A' ack/status, 'F' fill, 'C' cancel, 'M' modify-confirmed,
//   'R' reject, 'X' failure/not-cancelled,
//   'B' bust (trade reversed — position changed retroactively),
//   'N' not-modified (modify rejected — local order state may be wrong),
//   'L' auto-liquidate (broker force-flattened position).
// The consumer must treat 'B', 'N', and 'L' as requiring immediate
// reconciliation or halt.

namespace hft {

struct PreparedLimitOrder;

struct MarketDataEvent {
    uint64_t timestamp_ns;
    uint64_t callback_monotonic_ns;
    uint64_t order_id;
    char action;          // 'A' add, 'C' cancel, 'M' modify, 'T' trade
    char side;            // 'B' bid, 'A' ask
    uint16_t symbol_id;   // registry id assigned by subscribe_mbo(); 0xFFFF = unknown
                          // Placed inside existing 6-byte padding after side; struct size unchanged.
    // 4 bytes implicit padding here (before double alignment)
    double price;
    int32_t size;
    // 4 bytes trailing padding
};

static_assert(sizeof(MarketDataEvent) == 48,
    "MarketDataEvent layout changed — update c_api.cpp pop path and any binary format readers");

struct OrderEvent {
    uint64_t timestamp_ns;
    uint64_t callback_monotonic_ns;
    uint64_t callback_wall_ns;
    uint64_t order_id;
    char event_type;   // 'S' submit, 'A' ack, 'F' fill, 'C' cancel, 'M' modify, 'R' reject, 'X' failure/not-cancelled,
                       // 'B' bust (fill reversed — position changed retroactively; reconcile immediately),
                       // 'N' not-modified (modify rejected — local order state wrong; reconcile immediately),
                       // 'L' auto-liquidate (broker force-flattened; halt and reconcile)
    char side;         // 'B' buy, 'A' sell, ' ' unknown
    char order_type;   // 'L' limit, 'M' market, ' ' unknown
    double price;
    int32_t size;
    int32_t filled_size;
    int32_t total_filled;
    int32_t total_unfilled;
    char user_msg[64];
    char tag[64];
};

struct ConnectionConfig {
    std::string environment;
    std::string username;
    std::string password;
    std::string app_name;
    std::string app_version;
    std::string ssl_cert_path;
    std::string log_file_path;

    std::string md_connect_point;
    std::string ts_connect_point;
    std::string rep_connect_point;
    std::string pnl_connect_point;
    std::string ih_connect_point;

    std::vector<std::string> env_vars;
};

class RithmicAdapter {
    friend class MyCallbacks;
    friend class MyAdmCallbacks;

public:
    explicit RithmicAdapter(const ConnectionConfig& config,
                           SPSCQueue<MarketDataEvent, 8192>* mbo_queue,
                           SPSCQueue<OrderEvent, 8192>* order_queue);
    ~RithmicAdapter();

    bool initialize();
    bool connect();
    void disconnect();

    // subscribe_mbo: subscribes and assigns a symbol_id (sequential, starting at 0).
    // Returns false on adapter failure.  symbol_id is exposed via lookup_symbol_id().
    bool subscribe_mbo(const std::string& symbol, const std::string& exchange = "CME");

    // Returns the symbol_id assigned to ticker, or 0xFFFF if not registered.
    uint16_t lookup_symbol_id(const char* ticker, int len) const noexcept;
    bool warm_price_increment(const std::string& symbol, const std::string& exchange = "CME");
    PreparedLimitOrder* prepare_limit_order(const std::string& symbol,
                                            const std::string& exchange = "CME");
    bool set_prepared_limit_order_tag(PreparedLimitOrder* prepared,
                                      const char* user_msg);
    bool send_prepared_limit_order(PreparedLimitOrder* prepared, char side,
                                   int32_t qty, double price,
                                   const char* user_msg = nullptr);
    void destroy_prepared_limit_order(PreparedLimitOrder* prepared);
    bool send_order(const std::string& symbol, char side, int32_t qty, double price,
                    const std::string& user_msg = "");
    bool cancel_order(const std::string& order_id);

    bool has_account() const { return account_ready_.load(); }
    bool has_trade_route() const { return trade_route_ready_.load(); }
    const char* cached_account_id();
    const char* cached_trade_route();
    const char* cached_env_key() const { return discovered_env_key_.c_str(); }
    const char* last_connect_error() const { return last_connect_error_.c_str(); }

    bool list_agreements();
    bool has_agreement_list() const { return agreement_list_ready_.load(); }
    const char* last_agreement_list_text() const;

    // ---------------------------------------------------------------------------
    // Drop counters and safety flags — all lock-free; safe to read from any thread.
    //
    // md_drops:           market-data queue overruns (TradePrint / BestBidAskQuote).
    // order_event_drops:  order-event queue overruns (any order callback).
    // md_data_gap:        set when a market-data event was dropped; stale book —
    //                     consumer must force a book resync or halt before trusting quotes.
    // order_halt:         set when an order event was dropped (order state unknown);
    //                     consumer must halt trading immediately.
    // position_desync:    set when a trade bust is received; position changed retroactively.
    // order_desync:       set when a not-modified report is received; local modify state wrong.
    // auto_liquidate_halt: set when the broker force-flattened a position outside our control.
    // adm_alert_severity: highest AdmCallbacks::Alert severity seen (0 = none); values ≥ 2
    //                     indicate a condition that warrants operator attention or halt.
    // ---------------------------------------------------------------------------
    uint64_t md_drops()           const noexcept { return md_drops_.load(std::memory_order_relaxed); }
    uint64_t order_event_drops()  const noexcept { return order_event_drops_.load(std::memory_order_relaxed); }
    bool     md_data_gap()        const noexcept { return md_data_gap_.load(std::memory_order_acquire); }
    bool     order_halt()         const noexcept { return order_halt_.load(std::memory_order_acquire); }
    bool     position_desync()    const noexcept { return position_desync_.load(std::memory_order_acquire); }
    bool     order_desync()       const noexcept { return order_desync_.load(std::memory_order_acquire); }
    bool     auto_liquidate_halt() const noexcept { return auto_liquidate_halt_.load(std::memory_order_acquire); }
    int      adm_alert_severity() const noexcept { return adm_alert_severity_.load(std::memory_order_relaxed); }

    // Reset flags after the consumer has acknowledged and acted on them.
    void clear_md_data_gap()        noexcept { md_data_gap_.store(false, std::memory_order_release); }
    void clear_order_halt()         noexcept { order_halt_.store(false, std::memory_order_release); }
    void clear_position_desync()    noexcept { position_desync_.store(false, std::memory_order_release); }
    void clear_order_desync()       noexcept { order_desync_.store(false, std::memory_order_release); }
    void clear_auto_liquidate_halt() noexcept { auto_liquidate_halt_.store(false, std::memory_order_release); }
    void clear_adm_alert_severity() noexcept { adm_alert_severity_.store(0, std::memory_order_relaxed); }

private:
    ConnectionConfig config_;
    SPSCQueue<MarketDataEvent, 8192>* mbo_queue_;
    SPSCQueue<OrderEvent, 8192>* order_queue_;

    void* engine_;
    void* callbacks_;
    void* adm_callbacks_;

    std::atomic<bool> connected_{false};
    std::atomic<bool> logged_in_{false};
    std::atomic<bool> account_ready_{false};
    std::atomic<bool> trade_route_ready_{false};
    std::atomic<int> md_login_status_{0};
    std::atomic<int> ts_login_status_{0};
    std::atomic<int> rep_login_status_{0};

    std::mutex login_mutex_;
    std::condition_variable login_cv_;
    std::mutex account_mutex_;
    std::condition_variable account_cv_;
    std::mutex trade_route_mutex_;
    std::condition_variable trade_route_cv_;
    std::mutex price_incr_mutex_;
    std::condition_variable price_incr_cv_;
    std::mutex env_mutex_;
    std::condition_variable env_cv_;
    std::condition_variable env_list_cv_;

    std::string account_id_;
    std::string fcm_id_;
    std::string ib_id_;
    std::string trade_route_;
    std::string discovered_env_key_;
    std::string last_connect_error_;
    std::string last_agreement_list_text_;
    mutable std::mutex agreement_mutex_;
    std::condition_variable agreement_cv_;
    std::atomic<bool> env_list_ready_{false};
    std::atomic<bool> env_ready_{false};
    std::atomic<bool> agreement_list_ready_{false};
    std::atomic<int> unaccepted_mandatory_agreements_{0};
    std::atomic<bool> price_incr_ready_{false};
    std::set<std::string> price_incr_ready_keys_;
    std::vector<std::string> env_storage_;
    std::vector<char*> env_strings_;

    // Drop counters and safety flags — written only from Rithmic callback threads,
    // read from the gateway consumer thread.
    std::atomic<uint64_t> md_drops_{0};
    std::atomic<uint64_t> order_event_drops_{0};
    std::atomic<bool>     md_data_gap_{false};
    std::atomic<bool>     order_halt_{false};
    std::atomic<bool>     position_desync_{false};
    std::atomic<bool>     order_desync_{false};
    std::atomic<bool>     auto_liquidate_halt_{false};
    std::atomic<int>      adm_alert_severity_{0};

    // Symbol registry — populated by subscribe_mbo() in subscription order.
    // Max 16 symbols; ids are indices 0..N-1.  Written only from the calling
    // thread before market data arrives; read lock-free from callback threads
    // (harmless if a subscription races a callback for the same symbol because
    // the worst outcome is id 0xFFFF on the very first event, which the daemon
    // treats as a re-stamp-able gap).
    static constexpr int kMaxSymbols = 16;
    struct SymbolEntry {
        char ticker[32];
        int  len;
        uint16_t id;
    };
    SymbolEntry symbol_registry_[kMaxSymbols] = {};
    int symbol_count_ = 0;

    void build_envp();
    void cleanup_envp();

    static const int LOGIN_NOT_LOGGED_IN = 0;
    static const int LOGIN_AWAITING      = 1;
    static const int LOGIN_FAILED        = 2;
    static const int LOGIN_COMPLETE      = 3;
};

} // namespace hft
