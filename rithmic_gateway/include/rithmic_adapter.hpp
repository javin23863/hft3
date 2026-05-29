#pragma once

#include <string>
#include <functional>
#include <cstdint>
#include "spsc_queue.hpp"

// Forward declaration for R-API components (mocked for this skeleton)
namespace omne {
    class RApi;
}

namespace hft {

// MBO Market Data Event Structure to pass over the lock-free queue
struct MarketDataEvent {
    uint64_t timestamp_ns;
    uint64_t order_id;
    char action; // 'A', 'C', 'M', 'T'
    char side;   // 'B', 'A'
    double price;
    int32_t size;
};

class RithmicAdapter {
public:
    struct Config {
        std::string environment; // e.g., "Rithmic Aurora"
        std::string username;
        std::string password;
        std::string app_name;
        std::string version;
    };

    RithmicAdapter(const Config& config, SPSCQueue<MarketDataEvent, 8192>* mbo_queue);
    ~RithmicAdapter();

    // Login and session initialization
    bool initialize();
    bool connect();
    void disconnect();

    // Subscription
    bool subscribe_mbo(const std::string& symbol, const std::string& exchange = "CME");
    
    // Order entry interface (called from the Risk/Decision thread)
    bool send_order(const std::string& symbol, char side, int32_t qty, double price);
    bool cancel_order(const std::string& order_id);

    // Callbacks from R-API (Executing on Rithmic thread)
    void on_market_data_update(const MarketDataEvent& event);
    void on_heartbeat();
    void on_execution_report(const std::string& order_id, int32_t fill_qty, double fill_price);

private:
    Config config_;
    omne::RApi* r_api_engine_;
    bool is_connected_;

    // SPSC Queue to pass data from R-API network thread to our internal hot-path thread
    SPSCQueue<MarketDataEvent, 8192>* mbo_queue_;
};

} // namespace hft