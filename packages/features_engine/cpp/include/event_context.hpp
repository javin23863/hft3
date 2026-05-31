#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace hft {

// Mirrors features_engine/src/regime/event_context.py + data_system/src/events_parser.py.
class EventContextEngineCpp {
public:
    explicit EventContextEngineCpp(
        const std::string& events_csv_path = "packages/data_system/config/events.csv",
        const std::string& event_id = "");

    std::string resolve_ns(int64_t timestamp_ns) const;

    static std::string map_label(const std::string& event_type, const std::string& window_name);

private:
    struct ParsedEvent {
        std::string event_type;
        std::string window_name;
        int effective_ymd{0};
        int64_t start_utc_ns{0};
        int64_t end_utc_ns{0};
    };

    static int utc_date_ymd(int64_t timestamp_ns);
    static bool effective_date_active(int effective_ymd, int ts_ymd);

    std::vector<ParsedEvent> events_;
};

}  // namespace hft
