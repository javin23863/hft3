#include "event_context.hpp"

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <fstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace hft {
namespace {

constexpr int64_t kNsPerSec = 1'000'000'000LL;

std::string trim(std::string s) {
    while (!s.empty() && (s.back() == '\r' || s.back() == ' ' || s.back() == '\t')) {
        s.pop_back();
    }
    size_t i = 0;
    while (i < s.size() && (s[i] == ' ' || s[i] == '\t')) {
        ++i;
    }
    return s.substr(i);
}

std::vector<std::string> split_csv_line(const std::string& line) {
    std::vector<std::string> fields;
    std::string cur;
    bool in_quotes = false;
    for (char c : line) {
        if (c == '"') {
            in_quotes = !in_quotes;
            continue;
        }
        if (c == ',' && !in_quotes) {
            fields.push_back(trim(cur));
            cur.clear();
            continue;
        }
        cur.push_back(c);
    }
    fields.push_back(trim(cur));
    return fields;
}

using days = std::chrono::sys_days;

days nth_weekday_of_month(int year, std::chrono::month month, int weekday, int n) {
    using std::chrono::year;
    using std::chrono::year_month_day;

    const year_month_day first{year(year), month, std::chrono::day{1}};
    const int first_wd = static_cast<int>(std::chrono::weekday{first}.c_encoding());
    const int delta = (weekday - first_wd + 7) % 7;
    const int day = 1 + delta + 7 * (n - 1);
    return days{year_month_day{year(year), month, std::chrono::day{static_cast<unsigned>(day)}}};
}

bool us_dst_active(days d) {
    const auto ymd = std::chrono::year_month_day{d};
    const int y = static_cast<int>(ymd.year());
    const days dst_start = nth_weekday_of_month(y, std::chrono::March, /*Sunday*/ 0, 2);
    const days dst_end = nth_weekday_of_month(y, std::chrono::November, /*Sunday*/ 0, 1);
    return d >= dst_start && d < dst_end;
}

int utc_offset_seconds(days d, const std::string& tz) {
    const bool dst = us_dst_active(d);
    if (tz == "America/New_York") {
        return dst ? 4 * 3600 : 5 * 3600;
    }
    if (tz == "America/Chicago") {
        return dst ? 5 * 3600 : 6 * 3600;
    }
    throw std::runtime_error("unsupported timezone: " + tz);
}

int64_t parse_anchor_utc_ns(const std::string& date, const std::string& time, const std::string& tz) {
    int y = 0;
    int mo = 0;
    int da = 0;
    if (std::sscanf(date.c_str(), "%d-%d-%d", &y, &mo, &da) != 3) {
        throw std::runtime_error("bad release_date: " + date);
    }
    int hh = 0;
    int mm = 0;
    int ss = 0;
    if (std::sscanf(time.c_str(), "%d:%d:%d", &hh, &mm, &ss) != 3) {
        throw std::runtime_error("bad release_time: " + time);
    }

    using std::chrono::hours;
    using std::chrono::minutes;
    using std::chrono::seconds;
    using std::chrono::sys_seconds;
    using std::chrono::year;
    using std::chrono::year_month_day;

    const days local_day{year_month_day{year(y), std::chrono::month(static_cast<unsigned>(mo)),
                                        std::chrono::day(static_cast<unsigned>(da))}};
    const sys_seconds local_sec =
        sys_seconds{local_day.time_since_epoch()} + hours{hh} + minutes{mm} + seconds{ss};
    const sys_seconds utc_sec = local_sec + seconds{utc_offset_seconds(local_day, tz)};
    return std::chrono::duration_cast<std::chrono::nanoseconds>(utc_sec.time_since_epoch()).count();
}

void require_columns(const std::vector<std::string>& header) {
    static const char* kRequired[] = {
        "event_id",         "event_type",       "release_date",     "release_time",
        "timezone",         "window_name",      "start_offset_seconds", "end_offset_seconds",
        "symbols",          "priority",         "source",           "source_url",
        "effective_date",   "notes"};
    for (const char* col : kRequired) {
        bool found = false;
        for (const auto& h : header) {
            if (h == col) {
                found = true;
                break;
            }
        }
        if (!found) {
            throw std::runtime_error(std::string("Missing required column: ") + col);
        }
    }
}

int col_index(const std::vector<std::string>& header, const char* name) {
    for (size_t i = 0; i < header.size(); ++i) {
        if (header[i] == name) {
            return static_cast<int>(i);
        }
    }
    return -1;
}

}  // namespace

std::string EventContextEngineCpp::map_label(const std::string& event_type,
                                             const std::string& window_name) {
    if (window_name == "TIGHT") {
        if (event_type == "CPI") {
            return "CPI_TIGHT";
        }
        if (event_type == "NFP") {
            return "NFP_TIGHT";
        }
        if (event_type.find("FOMC") != std::string::npos) {
            return "FOMC_STATEMENT_TIGHT";
        }
    }
    if (event_type == "PROP_REOPEN") {
        return "PROP_REOPEN";
    }
    if (event_type == "CASH_EQUITY_OPEN" || event_type.find("OPEN") != std::string::npos) {
        return "CASH_EQUITY_OPEN";
    }
    if (event_type == "PROP_FLATTEN_TOPSTEP") {
        return "PROP_FLATTEN_TOPSTEP";
    }
    if (event_type.find("FRIDAY") != std::string::npos) {
        return "FRIDAY_CLOSE";
    }
    if (event_type.find("APEX") != std::string::npos) {
        return "APEX_FLATTEN";
    }
    if (event_type.find("TPT") != std::string::npos || event_type.find("MyFunded") != std::string::npos) {
        return "TPT_FLATTEN";
    }
    if (event_type.find("NEWS") != std::string::npos) {
        return "NEWS_RESTRICTION";
    }
    return event_type;
}

EventContextEngineCpp::EventContextEngineCpp(const std::string& events_csv_path) {
    std::ifstream in(events_csv_path);
    if (!in) {
        throw std::runtime_error("cannot open events csv: " + events_csv_path);
    }

    std::string line;
    if (!std::getline(in, line)) {
        throw std::runtime_error("empty events csv: " + events_csv_path);
    }
    const auto header = split_csv_line(line);
    require_columns(header);

    const int i_type = col_index(header, "event_type");
    const int i_date = col_index(header, "release_date");
    const int i_time = col_index(header, "release_time");
    const int i_tz = col_index(header, "timezone");
    const int i_window = col_index(header, "window_name");
    const int i_start_off = col_index(header, "start_offset_seconds");
    const int i_end_off = col_index(header, "end_offset_seconds");
    const int i_priority = col_index(header, "priority");

    while (std::getline(in, line)) {
        line = trim(line);
        if (line.empty()) {
            continue;
        }
        const auto row = split_csv_line(line);
        const size_t need = static_cast<size_t>(
            std::max({i_type, i_date, i_time, i_tz, i_window, i_start_off, i_end_off, i_priority}) + 1);
        if (row.size() < need) {
            continue;
        }

        const int64_t anchor_ns =
            parse_anchor_utc_ns(row[static_cast<size_t>(i_date)], row[static_cast<size_t>(i_time)],
                                row[static_cast<size_t>(i_tz)]);
        const int64_t start_off = std::stoll(row[static_cast<size_t>(i_start_off)]);
        const int64_t end_off = std::stoll(row[static_cast<size_t>(i_end_off)]);

        ParsedEvent ev;
        ev.event_type = row[static_cast<size_t>(i_type)];
        ev.window_name = row[static_cast<size_t>(i_window)];
        ev.priority = std::stoi(row[static_cast<size_t>(i_priority)]);
        ev.start_utc_ns = anchor_ns + start_off * kNsPerSec;
        ev.end_utc_ns = anchor_ns + end_off * kNsPerSec;
        events_.push_back(std::move(ev));
    }
}

std::string EventContextEngineCpp::resolve_ns(int64_t timestamp_ns) const {
    struct Candidate {
        int priority;
        std::string event_type;
        std::string window_name;
    };
    std::vector<Candidate> candidates;
    candidates.reserve(events_.size());

    for (const ParsedEvent& ev : events_) {
        if (ev.start_utc_ns <= timestamp_ns && timestamp_ns <= ev.end_utc_ns) {
            candidates.push_back({ev.priority, ev.event_type, ev.window_name});
        }
    }
    if (candidates.empty()) {
        return "NORMAL";
    }

    std::stable_sort(candidates.begin(), candidates.end(),
                     [](const Candidate& a, const Candidate& b) { return a.priority < b.priority; });
    return map_label(candidates.front().event_type, candidates.front().window_name);
}

}  // namespace hft
