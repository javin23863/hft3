#include "decision_runtime.hpp"

namespace hft {

DecisionEngine::DecisionEngine() = default;

bool DecisionEngine::load_model(const char* model_path) {
    // In a real implementation, this loads weights from disk to the `weights_` array.
    // Must be done before joining the hot path.
    initialized_ = true;
    return true;
}

void DecisionEngine::evaluate_actions(const MarketState& state, ActionArray& out_actions) const noexcept {
    // Zero-allocation hot path evaluation.
    // EV_t(a) = P_fill(a) * E[PnL | fill, a] - (1 - P_fill(a)) * C_miss(a) - Costs
    
    // NO_TRADE
    out_actions[0] = {Action::NO_TRADE, 0.0, 1.0, 0.0, 0.0};
    
    // ENTER_LONG - Vectorized math / BLAS dot product would go here using `state.model_features` and `weights_`
    double ev_long = (state.ask_qty_1 > 0) ? (state.model_features[0] * weights_[0]) : -0.5;
    out_actions[1] = {Action::ENTER_LONG, ev_long, 0.8, -10.0, 0.5};
    
    // ENTER_SHORT
    double ev_short = (state.bid_qty_1 > 0) ? (state.model_features[1] * weights_[1]) : -0.5;
    out_actions[2] = {Action::ENTER_SHORT, ev_short, 0.8, -10.0, 0.5};
    
    // In reality, populate all supported actions based on the state.
}

ActionValue DecisionEngine::get_optimal_action(const ActionArray& evaluated_actions) const noexcept {
    ActionValue best_action = evaluated_actions[0];
    
    // Loop over fixed array to find max Expected Value.
    // Branches are highly predictable if NO_TRADE is usually the answer.
    for (size_t i = 1; i < static_cast<size_t>(Action::_COUNT); ++i) {
        if (evaluated_actions[i].expected_value > best_action.expected_value) {
            best_action = evaluated_actions[i];
        }
    }
    
    return best_action;
}

} // namespace hft
