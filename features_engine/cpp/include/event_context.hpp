#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace hft {

// Mirrors features_engine/src/regime/event_context.py + data_system/src/events_parser.py.
class EventContextEngineCpp {
public:
    explicit EventContextEngineCpp(
        const std::string& events_csv_path = "data_system/config/events.csv");

    std::string resolve_ns(int64_t timestamp_ns) const;

private:
    struct ParsedEvent {
        int priority{99};
        std::string event_type;
        std::string window_name;
        int64_t start_utc_ns{0};
        int64_t end_utc_ns{0};
    };

    static std::string map_label(const std::string& event_type, const std::string& window_name);

    std::vector<ParsedEvent> events_;
};

}  // namespace hft
