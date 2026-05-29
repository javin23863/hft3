#include "rithmic_adapter.hpp"
#include <iostream>
#include <chrono>

namespace hft {

// Stub for R-API initialization
RithmicAdapter::RithmicAdapter(const Config& config, SPSCQueue<MarketDataEvent, 8192>* mbo_queue)
    : config_(config), r_api_engine_(nullptr), is_connected_(false), mbo_queue_(mbo_queue) {}

RithmicAdapter::~RithmicAdapter() {
    disconnect();
}

bool RithmicAdapter::initialize() {
    // In reality, setup omne::RApi environment, specify certs, set up callbacks
    std::cout << "[RithmicAdapter] Initializing R-API for app: " << config_.app_name << std::endl;
    return true;
}

bool RithmicAdapter::connect() {
    std::cout << "[RithmicAdapter] Connecting to " << config_.environment 
              << " as " << config_.username << std::endl;
    // Perform R-API login sequence
    is_connected_ = true;
    return true;
}

void RithmicAdapter::disconnect() {
    if (is_connected_) {
        std::cout << "[RithmicAdapter] Disconnecting." << std::endl;
        is_connected_ = false;
    }
}

bool RithmicAdapter::subscribe_mbo(const std::string& symbol, const std::string& exchange) {
    if (!is_connected_) return false;
    std::cout << "[RithmicAdapter] Subscribing to MBO for " << symbol << " on " << exchange << std::endl;
    // R-API SubscribeByTicker request
    return true;
}

bool RithmicAdapter::send_order(const std::string& symbol, char side, int32_t qty, double price) {
    // Construct R-API Order intent. This must be lock-free and extremely fast.
    return true;
}

bool RithmicAdapter::cancel_order(const std::string& order_id) {
    return true;
}

// Simulated callback from R-API thread
void RithmicAdapter::on_market_data_update(const MarketDataEvent& event) {
    // Immediately push into the lock-free queue for the Decision thread to consume.
    // We do NOT process logic here to avoid stalling the Rithmic network thread.
    if (!mbo_queue_->push(event)) {
        // Queue full (hot path fell behind network path). This is a critical failure state.
        std::cerr << "[CRITICAL] MBO Queue overrun! Risk engine should HALT." << std::endl;
    }
}

void RithmicAdapter::on_heartbeat() {
    // Update internal health state
}

void RithmicAdapter::on_execution_report(const std::string& order_id, int32_t fill_qty, double fill_price) {
    // Parse fill and alert RiskEngine / DecisionEngine
}

} // namespace hft