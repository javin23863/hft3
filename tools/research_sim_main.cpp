#include "decision_runtime.hpp"
#include "feature_extractor.hpp"
#include "risk_manager.hpp"
#include "rithmic_adapter.hpp"

#include <algorithm>
#include <iostream>
#include <string>

namespace {

struct StackChecks {
    bool gateway_init{false};
    bool spsc_queue_roundtrip{false};
    bool feature_extract{false};
    bool decision_evaluate{false};
    bool risk_precheck{false};
};

bool run_stack_checks(
    hft::FeatureExtractorCpp& extractor,
    hft::DecisionEngine& engine,
    hft::risk::RiskManager& risk,
    hft::RithmicAdapter& gateway,
    hft::SPSCQueue<hft::MarketDataEvent, 8192>& queue,
    StackChecks& checks) {
    checks = StackChecks{};

    checks.gateway_init = gateway.initialize();
    if (!checks.gateway_init) {
        return false;
    }

    const hft::MarketDataEvent ev{1'000'000'000ULL, 42, 'A', 'B', 5500.0, 10};
    queue.push(ev);

    hft::MarketDataEvent popped{};
    checks.spsc_queue_roundtrip = queue.pop(popped) && popped.order_id == 42 && popped.price == 5500.0;
    if (!checks.spsc_queue_roundtrip) {
        return false;
    }

    const hft::MBOEventCpp bid{1'000'000'000LL, 42, 'A', 'B', 5500.0, 10};
    const hft::MBOEventCpp ask{1'000'000'100LL, 43, 'A', 'A', 5500.25, 8};
    extractor.process_event(bid);
    extractor.process_event(ask);
    const auto& feats = extractor.features();
    checks.feature_extract = std::any_of(
        feats.begin(), feats.end(), [](double v) { return v != 0.0; });
    if (!checks.feature_extract) {
        return false;
    }

    hft::MarketState state{};
    state.bid_price_1 = 5500.0;
    state.bid_qty_1 = 10;
    state.ask_price_1 = 5500.25;
    state.ask_qty_1 = 8;
    for (size_t i = 0; i < state.model_features.size(); ++i) {
        state.model_features[i] = feats[i];
    }

    hft::ActionArray actions{};
    engine.evaluate_actions(state, actions);
    const hft::ActionValue best = engine.get_optimal_action(actions);
    checks.decision_evaluate =
        actions[static_cast<size_t>(hft::Action::NO_TRADE)].action == hft::Action::NO_TRADE
        && actions[static_cast<size_t>(hft::Action::ENTER_LONG)].action == hft::Action::ENTER_LONG
        && actions[static_cast<size_t>(hft::Action::ENTER_SHORT)].action == hft::Action::ENTER_SHORT
        && best.action != hft::Action::_COUNT;
    if (!checks.decision_evaluate) {
        return false;
    }

    checks.risk_precheck =
        risk.check_order(1) == hft::risk::RiskStatus::PASS
        && risk.check_order(-1) == hft::risk::RiskStatus::PASS;

    return checks.gateway_init && checks.spsc_queue_roundtrip && checks.feature_extract
        && checks.decision_evaluate && checks.risk_precheck;
}

void print_json(bool stack_ok, const StackChecks& checks) {
    std::cout << "HFT_RESEARCH_SIM_JSON:"
              << "{\"stack_verified\":" << (stack_ok ? "true" : "false")
              << ",\"checks\":{"
              << "\"gateway_init\":" << (checks.gateway_init ? "true" : "false") << ","
              << "\"spsc_queue_roundtrip\":" << (checks.spsc_queue_roundtrip ? "true" : "false") << ","
              << "\"feature_extract\":" << (checks.feature_extract ? "true" : "false") << ","
              << "\"decision_evaluate\":" << (checks.decision_evaluate ? "true" : "false") << ","
              << "\"risk_precheck\":" << (checks.risk_precheck ? "true" : "false")
              << "}"
              << ",\"orders\":[],\"fills\":[],\"latency_logs\":[]}"
              << "\n";
}

}  // namespace

int main(int argc, char** argv) {
    bool verify_only = false;
    for (int i = 1; i < argc; ++i) {
        if (std::string(argv[i]) == "--verify-stack") {
            verify_only = true;
        }
    }

    hft::FeatureExtractorCpp extractor(0.25);
    hft::DecisionEngine engine;
    hft::risk::RiskLimits limits;
    hft::risk::RiskManager risk(limits);
    hft::SPSCQueue<hft::MarketDataEvent, 8192> mbo_queue;
    hft::SPSCQueue<hft::OrderEvent, 8192> order_queue;
    hft::ConnectionConfig gw_cfg;
    gw_cfg.environment = "Rithmic Aurora";
    gw_cfg.username = "paper";
    gw_cfg.password = "paper";
    gw_cfg.app_name = "hft3_research_sim";
    gw_cfg.app_version = "1.0";
    hft::RithmicAdapter gateway(gw_cfg, &mbo_queue, &order_queue);

    StackChecks checks{};
    const bool stack_ok = run_stack_checks(extractor, engine, risk, gateway, mbo_queue, checks);

    if (verify_only) {
        print_json(stack_ok, checks);
        return stack_ok ? 0 : 1;
    }

    std::cout << "[hft_research_sim] C++ stack self-test (no R-API, no NPZ replay).\n";
    print_json(stack_ok, checks);
    return stack_ok ? 0 : 1;
}
