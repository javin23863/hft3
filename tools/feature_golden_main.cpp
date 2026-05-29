#include "feature_extractor.hpp"
#include "feature_index.hpp"

#include <cstdint>
#include <iomanip>
#include <iostream>
#include <vector>

namespace {

hft::MBOEventCpp make_event(int64_t ts, int64_t oid, char action, char side, double price,
                            int32_t size) {
    return hft::MBOEventCpp{ts, oid, action, side, price, size};
}

}  // namespace

int main() {
    hft::FeatureExtractorCpp extractor(0.25);
    const std::vector<hft::MBOEventCpp> events = {
        make_event(1'000, 1, 'A', 'B', 5500.0, 10),
        make_event(1'001, 2, 'A', 'A', 5501.0, 8),
        make_event(1'002, 3, 'A', 'B', 5500.5, 5),
        make_event(1'003, 4, 'C', 'B', 5500.5, 5),
        make_event(1'004, 5, 'T', 'A', 5501.0, 2),
    };

    for (const auto& ev : events) {
        extractor.process_event(ev);
    }

    const auto& vec = extractor.features();
    constexpr size_t kSlots[] = {26, 41, 42, 43, 44, 45, 46, 47, 48, 49};

    std::cout << std::setprecision(17);
    std::cout << "{";
    for (size_t i = 0; i < sizeof(kSlots) / sizeof(kSlots[0]); ++i) {
        if (i > 0) {
            std::cout << ",";
        }
        std::cout << "\"" << kSlots[i] << "\":" << vec[kSlots[i]];
    }
    std::cout << "}\n";
    return 0;
}
