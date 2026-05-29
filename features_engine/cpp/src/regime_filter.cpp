#include "regime_filter.hpp"

#include "feature_index.hpp"

#include <algorithm>
#include <cmath>
#include <string>

namespace hft {

namespace {

constexpr size_t kRegimeCount = 9;

double clamp_nonneg(double x) { return x > 0.0 ? x : 0.0; }

void apply_event_context_boosts(double* logits, const std::string& event_context) {
    static const char* kEventShock[] = {
        "CPI_TIGHT", "NFP_TIGHT", "FOMC_STATEMENT_TIGHT", "FOMC_PRESS_TIGHT", "CASH_EQUITY_OPEN"};
    static const char* kPropFlatten[] = {
        "PROP_FLATTEN_TOPSTEP", "TPT_FLATTEN", "APEX_FLATTEN", "FRIDAY_CLOSE"};

    for (const char* tag : kEventShock) {
        if (event_context == tag) {
            logits[1] += 2.0;  // event_shock
            logits[8] += 1.0;  // spread_stress
            break;
        }
    }
    for (const char* tag : kPropFlatten) {
        if (event_context == tag) {
            logits[4] += 3.0;  // prop_flatten
            logits[0] -= 1.0;  // normal
            break;
        }
    }
    if (event_context == "NEWS_RESTRICTION") {
        logits[4] += 1.5;
    }
}

}  // namespace

RegimeFilterCpp::RegimeFilterCpp(double temperature) : temperature_(temperature) { reset(); }

void RegimeFilterCpp::reset() {
    const double uniform = 1.0 / static_cast<double>(kRegimeCount);
    prev_posterior_.fill(uniform);
}

void RegimeFilterCpp::update(std::array<double, 64>& vec, const std::string& event_context) {
    const double spread_stress = vec[static_cast<size_t>(FeatureIndex::SPREAD_STRESS)];
    const double cancel_ratio = vec[static_cast<size_t>(FeatureIndex::CANCEL_TO_ADD_RATIO)];
    const double agg_imb = vec[static_cast<size_t>(FeatureIndex::AGGRESSOR_VOLUME_IMBALANCE)];
    const double slope_change = vec[static_cast<size_t>(FeatureIndex::BOOK_SLOPE_CHANGE)];
    const double vacuum = vec[static_cast<size_t>(FeatureIndex::LIQUIDITY_VACUUM_SCORE)];
    const double near_cancel = vec[static_cast<size_t>(FeatureIndex::NEAR_TOUCH_CANCEL_PRESSURE)];

    double logits[kRegimeCount] = {0.0};

    // Order matches REGIME_LABELS in regime_filter.py
    logits[0] = -std::abs(agg_imb) - clamp_nonneg(spread_stress - 1.0);           // normal
    logits[1] = clamp_nonneg(spread_stress - 1.5) + std::abs(slope_change) * 2.0; // event_shock
    logits[2] = vacuum * 3.0 + clamp_nonneg(spread_stress - 1.0);                 // liquidity_vacuum
    logits[3] = std::abs(agg_imb) * 2.0 + clamp_nonneg(cancel_ratio - 1.5);       // stop_cascade
    logits[4] = 0.0;                                                              // prop_flatten
    logits[5] = slope_change != 0.0 ? clamp_nonneg(-slope_change * agg_imb) : 0.0;  // book_rebuild
    logits[6] = -std::abs(agg_imb) + std::abs(slope_change) * 0.5;                // chop
    logits[7] = std::abs(agg_imb) * 1.5 - std::abs(slope_change);                 // trend_continuation
    logits[8] = clamp_nonneg(spread_stress - 1.2) * 2.0;                          // spread_stress

    apply_event_context_boosts(logits, event_context);

    if (near_cancel > 0.5) {
        logits[1] += 0.5;
        logits[2] += 0.5;
    }

    for (size_t i = 0; i < kRegimeCount; ++i) {
        logits[i] += 0.15 * std::log(prev_posterior_[i] + 1e-12);
        logits[i] /= temperature_;
    }

    double max_logit = logits[0];
    for (size_t i = 1; i < kRegimeCount; ++i) max_logit = std::max(max_logit, logits[i]);

    double sum_exp = 0.0;
    double posterior[kRegimeCount];
    for (size_t i = 0; i < kRegimeCount; ++i) {
        posterior[i] = std::exp(logits[i] - max_logit);
        sum_exp += posterior[i];
    }
    for (size_t i = 0; i < kRegimeCount; ++i) {
        posterior[i] /= sum_exp;
        prev_posterior_[i] = posterior[i];
    }

    vec[static_cast<size_t>(FeatureIndex::REGIME_NORMAL)] = posterior[0];
    vec[static_cast<size_t>(FeatureIndex::REGIME_EVENT_SHOCK)] = posterior[1];
    vec[static_cast<size_t>(FeatureIndex::REGIME_LIQUIDITY_VACUUM)] = posterior[2];
    vec[static_cast<size_t>(FeatureIndex::REGIME_STOP_CASCADE)] = posterior[3];
    vec[static_cast<size_t>(FeatureIndex::REGIME_PROP_FLATTEN)] = posterior[4];
    vec[static_cast<size_t>(FeatureIndex::REGIME_BOOK_REBUILD)] = posterior[5];
    vec[static_cast<size_t>(FeatureIndex::REGIME_CHOP)] = posterior[6];
    vec[static_cast<size_t>(FeatureIndex::REGIME_TREND)] = posterior[7];
    vec[static_cast<size_t>(FeatureIndex::REGIME_SPREAD_STRESS)] = posterior[8];
}

}  // namespace hft
