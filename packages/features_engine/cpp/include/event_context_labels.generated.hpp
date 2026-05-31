#pragma once
// AUTO-GENERATED — python tools/economic_event_universe/generate_event_context_labels.py

#include <string>
#include <unordered_map>

namespace hft {

struct EventContextLabelEntry {
    std::string label;
    std::string main_label;
    int context_priority{50};
};

inline const std::unordered_map<std::string, EventContextLabelEntry>& event_context_label_map() {
    static const std::unordered_map<std::string, EventContextLabelEntry> kMap = {
        {"ADP_EMPLOYMENT", {"ADP_TIGHT", "", 50},}
        {"BAKER_HUGHES_RIG", {"RIG_COUNT_TIGHT", "", 50},}
        {"BUILDING_PERMITS", {"BUILDING_PERMITS_TIGHT", "", 50},}
        {"CASH_EQUITY_OPEN", {"CASH_EQUITY_OPEN", "", 50},}
        {"CONSTRUCTION_SPENDING", {"CONSTRUCTION_SPENDING_TIGHT", "", 50},}
        {"CORE_CPI", {"CORE_CPI_TIGHT", "", 50},}
        {"CORE_PCE", {"CORE_PCE_TIGHT", "", 50},}
        {"CORE_PPI", {"CORE_PPI_TIGHT", "", 50},}
        {"CPI", {"CPI_TIGHT", "", 50},}
        {"DURABLE_GOODS_ADVANCE", {"DURABLE_GOODS_TIGHT", "", 50},}
        {"DURABLE_GOODS_FULL", {"DURABLE_GOODS_FULL_TIGHT", "", 50},}
        {"ECI", {"ECI_TIGHT", "", 50},}
        {"EIA_CRUDE", {"EIA_CRUDE_TIGHT", "", 50},}
        {"EIA_NATGAS", {"EIA_NATGAS_TIGHT", "", 50},}
        {"EXISTING_HOME_SALES", {"EXISTING_HOME_SALES_TIGHT", "", 50},}
        {"EXPORT_PRICES", {"EXPORT_PRICES_TIGHT", "", 50},}
        {"FACTORY_ORDERS", {"FACTORY_ORDERS_TIGHT", "", 50},}
        {"FED_BEIGE_BOOK", {"FED_BEIGE_BOOK_TIGHT", "", 50},}
        {"FED_H41", {"FED_H41_TIGHT", "", 50},}
        {"FED_SPEAKER", {"FED_SPEAKER_TIGHT", "", 50},}
        {"FOMC_MINUTES", {"FOMC_MINUTES_TIGHT", "", 30},}
        {"FOMC_PRESS", {"FOMC_PRESS_TIGHT", "", 5},}
        {"FOMC_STATEMENT", {"FOMC_STATEMENT_TIGHT", "", 10},}
        {"FRIDAY_CLOSE", {"FRIDAY_CLOSE", "", 50},}
        {"GDP_ADVANCE", {"GDP_ADVANCE_TIGHT", "", 50},}
        {"GDP_FINAL", {"GDP_FINAL_TIGHT", "", 50},}
        {"GDP_SECOND", {"GDP_SECOND_TIGHT", "", 50},}
        {"HOUSING_STARTS", {"HOUSING_STARTS_TIGHT", "", 50},}
        {"IMPORT_PRICES", {"IMPORT_PRICES_TIGHT", "", 50},}
        {"INDUSTRIAL_PRODUCTION", {"INDPRO_TIGHT", "", 50},}
        {"ISM_MANUFACTURING", {"ISM_MFG_TIGHT", "", 50},}
        {"ISM_SERVICES", {"ISM_SVC_TIGHT", "", 50},}
        {"JOLTS", {"JOLTS_TIGHT", "", 50},}
        {"NEW_HOME_SALES", {"NEW_HOME_SALES_TIGHT", "", 50},}
        {"NFP", {"NFP_TIGHT", "", 50},}
        {"PCE", {"PCE_TIGHT", "", 50},}
        {"PPI", {"PPI_TIGHT", "", 50},}
        {"PRODUCTIVITY", {"PRODUCTIVITY_TIGHT", "", 50},}
        {"PROP_FLATTEN_TOPSTEP", {"PROP_FLATTEN_TOPSTEP", "PROP_FLATTEN_TOPSTEP", 50},}
        {"PROP_REOPEN", {"PROP_REOPEN", "", 50},}
        {"RETAIL_SALES", {"RETAIL_SALES_TIGHT", "", 50},}
        {"TRADE_BALANCE", {"TRADE_BALANCE_TIGHT", "", 50},}
        {"TREASURY_AUCTION", {"TREASURY_AUCTION_TIGHT", "", 50},}
        {"TREASURY_REFUNDING", {"TREASURY_REFUNDING_TIGHT", "", 50},}
        {"UNEMPLOYMENT_CLAIMS", {"CLAIMS_TIGHT", "", 50},}
    };
    return kMap;
}

}  // namespace hft
