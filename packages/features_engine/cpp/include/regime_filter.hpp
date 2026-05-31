#pragma once

#include <array>
#include <string>

namespace hft {

// Mirrors features_engine/src/regime/regime_filter.py (v0 log-linear + softmax).
class RegimeFilterCpp {
public:
    explicit RegimeFilterCpp(double temperature = 1.0);

    void reset();
    void update(std::array<double, 64>& vec, const std::string& event_context = "NORMAL");

private:
    double temperature_;
    std::array<double, 9> prev_posterior_{};
};

}  // namespace hft
