#pragma once

#include <array>
#include <cstdint>
#include <string>

namespace hft {

enum class Action : uint8_t {
    NO_TRADE = 0,
    ENTER_LONG,
    ENTER_SHORT,
    ADD,
    REDUCE,
    FLATTEN,
    CANCEL,
    REPLACE,
    PASSIVE_JOIN,
    MARKETABLE_LIMIT,
    _COUNT // For array sizing
};

struct ActionValue {
    Action action;
    double expected_value;
    double fill_probability;
    double tail_risk;
    double adverse_selection_ticks;
};

// Fixed-size array for zero-allocation hot path evaluation
using ActionArray = std::array<ActionValue, static_cast<size_t>(Action::_COUNT)>;

struct alignas(64) MarketState {
    double bid_price_1;
    double ask_price_1;
    int32_t bid_qty_1;
    int32_t ask_qty_1;
    int32_t current_inventory;
    double latency_state_ms;
    
    // Feature array representing the processed MBO state (e.g. queue depletion, etc.)
    std::array<double, 64> model_features; 
};

// Header struct matching the Python binary export
struct ModelHeader {
    uint32_t magic;
    uint32_t version;
    uint32_t model_id;
    uint32_t feature_count;
};

class DecisionEngine {
public:
    DecisionEngine();
    
    // Loads weights and validates header. Call before hot-path.
    bool load_model(const std::string& model_path);
    
    // Evaluates all actions without dynamic memory allocation
    void evaluate_actions(const MarketState& state, ActionArray& out_actions) const noexcept;
    
    // Returns the optimal action based on the evaluated array
    ActionValue get_optimal_action(const ActionArray& evaluated_actions) const noexcept;

private:
    bool initialized_{false};
    uint32_t active_model_id_{0};
    uint32_t active_feature_count_{0};
    
    // Model weights reside here, strictly block-aligned for cache efficiency
    alignas(64) std::array<double, 1024> weights_{}; 
};

} // namespace hft
