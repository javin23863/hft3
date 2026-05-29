#include "decision_runtime.hpp"
#include "feature_extractor.hpp"
#include "risk_manager.hpp"

#include <iostream>

int main() {
    hft::FeatureExtractorCpp extractor(0.25);
    hft::DecisionEngine engine;
    hft::RiskManager risk;

    std::cout << "[hft_research_sim] C++ stack linked (no R-API).\n";
    std::cout << "Load model: " << (engine.load_model("models/model.bin") ? "ok" : "missing/stub")
              << "\n";
    return 0;
}
