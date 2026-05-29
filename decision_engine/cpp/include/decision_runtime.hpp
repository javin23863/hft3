#pragma once

#include <array>
#include <cstdint>

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

class DecisionEngine {
public:
    DecisionEngine();
    
    // Loads weights. Call before hot-path.
    bool load_model(const char* model_path);
    
    // Evaluates all actions without dynamic memory allocation
    void evaluate_actions(const MarketState& state, ActionArray& out_actions) const noexcept;
    
    // Returns the optimal action based on the evaluated array
    ActionValue get_optimal_action(const ActionArray& evaluated_actions) const noexcept;

private:
    bool initialized_{false};
    // Model weights would reside here, block-aligned
    alignas(64) std::array<double, 1024> weights_{}; 
};

} // namespace hft
