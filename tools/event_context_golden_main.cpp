#include "event_context.hpp"

#include <iostream>
#include <string>
#include <vector>

int main() {
    const std::vector<std::pair<std::string, std::string>> cases = {
        {"CPI", "TIGHT"},
        {"FOMC_STATEMENT", "TIGHT"},
        {"FOMC_PRESS", "TIGHT"},
        {"FOMC_MINUTES", "TIGHT"},
        {"PROP_FLATTEN_TOPSTEP", "MAIN"},
        {"UNEMPLOYMENT_CLAIMS", "TIGHT"},
    };
    std::cout << "{";
    bool first = true;
    for (const auto& [et, wn] : cases) {
        if (!first) {
            std::cout << ",";
        }
        first = false;
        const std::string label = hft::EventContextEngineCpp::map_label(et, wn);
        std::cout << "\"" << et << "|" << wn << "\":\"" << label << "\"";
    }
    std::cout << "}" << std::endl;
    return 0;
}
