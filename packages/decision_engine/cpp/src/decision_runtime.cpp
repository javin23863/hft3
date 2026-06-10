#include "decision_runtime.hpp"
#include <fstream>
#include <iostream>
#include <limits>

namespace hft {

// Sentinel value written to all non-promoted action slots so that
// get_optimal_action can never select them via argmax.
static constexpr double NEG_INFINITY_SENTINEL = -std::numeric_limits<double>::infinity();

DecisionEngine::DecisionEngine() = default;

bool DecisionEngine::load_model(const std::string& model_path) {
    std::ifstream file(model_path, std::ios::binary);
    if (!file) {
        std::cerr << "[DecisionEngine] Error: Could not open model file: " << model_path << std::endl;
        return false;
    }

    ModelHeader header;
    if (!file.read(reinterpret_cast<char*>(&header), sizeof(ModelHeader))) {
        std::cerr << "[DecisionEngine] Error: File too short to contain header (truncated): " << model_path << std::endl;
        return false;
    }

    if (header.magic != 0x48465433) {
        std::cerr << "[DecisionEngine] Error: Wrong magic number 0x"
                  << std::hex << header.magic << std::dec
                  << " (expected 0x48465433): " << model_path << std::endl;
        return false;
    }

    if (header.version != 1) {
        std::cerr << "[DecisionEngine] Error: Unsupported version " << header.version
                  << " (expected 1): " << model_path << std::endl;
        return false;
    }

    if (header.feature_count == 0) {
        std::cerr << "[DecisionEngine] Error: feature_count is 0 (invalid): " << model_path << std::endl;
        return false;
    }

    if (header.feature_count > 64) {
        std::cerr << "[DecisionEngine] Error: feature_count " << header.feature_count
                  << " exceeds maximum supported 64: " << model_path << std::endl;
        return false;
    }

    // Weights section must be fully present — reject truncated files.
    const std::streamsize weights_bytes = static_cast<std::streamsize>(weights_.size() * sizeof(double));
    if (!file.read(reinterpret_cast<char*>(weights_.data()), weights_bytes)) {
        std::cerr << "[DecisionEngine] Error: Weights section truncated (file too small): " << model_path << std::endl;
        return false;
    }

    active_model_id_ = header.model_id;
    active_feature_count_ = header.feature_count;
    initialized_ = true;

    std::cout << "[DecisionEngine] Loaded model id=" << active_model_id_
              << " feature_count=" << active_feature_count_ << std::endl;
    return true;
}

void DecisionEngine::evaluate_actions(const MarketState& state, ActionArray& out_actions) const noexcept {
    // Zero-allocation hot path evaluation.
    // EV_t(a) = P_fill(a) * E[PnL | fill, a] - (1 - P_fill(a)) * C_miss(a) - Costs
    //
    // CONTRACT (CHI404_RUNTIME.md §4.1): ALL 10 slots must be written every call.
    // Slots for non-promoted action codes receive NEG_INFINITY_SENTINEL so that
    // get_optimal_action's argmax scan can NEVER select them.
    // Promoted set (v1): {NO_TRADE=0, ENTER_LONG=1, ENTER_SHORT=2}.

    // --- Promoted actions ---

    // NO_TRADE: EV = 0.0 (neutral baseline; ties always broken in NO_TRADE's favour
    // because we seed best_action = slot[0] in get_optimal_action and use strict >).
    out_actions[0] = {Action::NO_TRADE, 0.0, 1.0, 0.0, 0.0};

    // ENTER_LONG / ENTER_SHORT — full dot product on exported weight vector.
    double ev_long  = -0.5;
    double ev_short = -0.5;
    if (state.ask_qty_1 > 0 || state.bid_qty_1 > 0) {
        ev_long  = 0.0;
        ev_short = 0.0;
        const size_t n = std::min(static_cast<size_t>(active_feature_count_),
                                  static_cast<size_t>(64));
        for (size_t i = 0; i < n; ++i) {
            const double contrib = state.model_features[i] * weights_[i];
            ev_long  += contrib;
            ev_short -= contrib;
        }
    }
    out_actions[1] = {Action::ENTER_LONG,  ev_long,  0.8, -10.0, 0.5};
    out_actions[2] = {Action::ENTER_SHORT, ev_short, 0.8, -10.0, 0.5};

    // --- Non-promoted actions (slots 3–9): NEG_INFINITY_SENTINEL ---
    // These codes (ADD, REDUCE, FLATTEN, CANCEL, REPLACE, PASSIVE_JOIN,
    // MARKETABLE_LIMIT) are not yet calibrated for EV-based selection in v1.
    // Writing -inf here ensures argmax can never return them, even if garbage
    // happened to be in the caller's ActionArray before this call.
    for (size_t i = 3; i < static_cast<size_t>(Action::_COUNT); ++i) {
        out_actions[i] = {static_cast<Action>(i), NEG_INFINITY_SENTINEL, 0.0, 0.0, 0.0};
    }
}

ActionValue DecisionEngine::get_optimal_action(const ActionArray& evaluated_actions) const noexcept {
    // Seed with slot 0 (NO_TRADE, EV=0.0).  This guarantees:
    //   • If all promoted EVs are negative, NO_TRADE wins (0.0 > negative).
    //   • If all slots are -inf (degenerate/uninitialized caller), we still
    //     return slot 0 (NO_TRADE) as a safe deterministic fallback — never
    //     a non-promoted code.
    // Ties are broken in favour of the lower-index (earlier) slot because we
    // use strict greater-than (>), matching the seed.
    ActionValue best_action = evaluated_actions[0];

    for (size_t i = 1; i < static_cast<size_t>(Action::_COUNT); ++i) {
        if (evaluated_actions[i].expected_value > best_action.expected_value) {
            best_action = evaluated_actions[i];
        }
    }

    return best_action;
}

} // namespace hft
