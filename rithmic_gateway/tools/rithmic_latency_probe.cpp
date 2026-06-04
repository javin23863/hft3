#include "rithmic_adapter.hpp"
#include "spsc_queue.hpp"

#include <chrono>
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <memory>
#include <new>
#include <numeric>
#include <sstream>
#include <string>
#include <vector>
#include <thread>

#if defined(_WIN32)
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#else
#include <cerrno>
#include <sched.h>
#include <sys/mman.h>
#include <unistd.h>
#endif

static const char* get_env_or(const char* key, const char* def) {
    const char* v = std::getenv(key);
    return v ? v : def;
}

static int get_env_int_or(const char* key, int def) {
    const char* v = std::getenv(key);
    if (!v || !v[0]) return def;
    char* endp = nullptr;
    long parsed = std::strtol(v, &endp, 10);
    return endp == v ? def : static_cast<int>(parsed);
}

static bool get_env_bool_or(const char* key, bool def) {
    const char* v = std::getenv(key);
    if (!v || !v[0]) return def;
    if (std::strcmp(v, "0") == 0 || std::strcmp(v, "false") == 0
        || std::strcmp(v, "FALSE") == 0 || std::strcmp(v, "no") == 0
        || std::strcmp(v, "NO") == 0) {
        return false;
    }
    return true;
}

static uint64_t steady_now_ns() {
    return static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch()
        ).count()
    );
}

static std::string run_id_utc() {
    std::time_t t = std::time(nullptr);
    std::tm tm{};
#if defined(_WIN32)
    gmtime_s(&tm, &t);
#else
    gmtime_r(&t, &tm);
#endif
    char buf[32];
    std::strftime(buf, sizeof(buf), "%Y%m%dT%H%M%SZ", &tm);
    return std::string(buf);
}

static std::string fixed_cstr(const char* data, size_t len) {
    size_t n = 0;
    while (n < len && data[n] != '\0') ++n;
    return std::string(data, data + n);
}

static bool order_event_matches(const hft::OrderEvent& ev, const std::string& user_msg,
                                uint64_t send_ns) {
    std::string ev_user_msg = fixed_cstr(ev.user_msg, sizeof(ev.user_msg));
    std::string ev_tag = fixed_cstr(ev.tag, sizeof(ev.tag));
    if (!ev_user_msg.empty() || !ev_tag.empty()) {
        return ev_user_msg == user_msg || ev_tag == user_msg;
    }
    uint64_t event_ns = ev.callback_monotonic_ns ? ev.callback_monotonic_ns : steady_now_ns();
    return event_ns >= send_ns;
}

static double pct_us(const std::vector<double>& sorted_us, double pct) {
    if (sorted_us.empty()) return 0.0;
    double rank = (pct / 100.0) * static_cast<double>(sorted_us.size() - 1);
    size_t idx = static_cast<size_t>(rank + 0.5);
    if (idx >= sorted_us.size()) idx = sorted_us.size() - 1;
    return sorted_us[idx];
}

static const char* repo_root() {
    const char* env = std::getenv("HFT3_REPO_DIR");
    if (env) return env;
    // fallback: probe runs as build/rithmic_gateway/rithmic_latency_probe
    return "/root/hft3/repo";
}

// Trim ASCII whitespace from both ends (in place).
static void trim(std::string& s) {
    size_t a = 0;
    while (a < s.size() && (s[a] == ' ' || s[a] == '\t' || s[a] == '\r' || s[a] == '\n')) ++a;
    size_t b = s.size();
    while (b > a && (s[b-1] == ' ' || s[b-1] == '\t' || s[b-1] == '\r' || s[b-1] == '\n')) --b;
    s = s.substr(a, b - a);
}

// Strip a matching pair of surrounding quotes (single or double) from a value.
static void unquote(std::string& s) {
    if (s.size() >= 2 && (s.front() == '"' || s.front() == '\'')
        && s.back() == s.front()) {
        s = s.substr(1, s.size() - 2);
    }
}

// Read a YAML file and pull out any `KEY: VALUE` pairs where KEY starts with MML_.
// Supports: indented or top-level keys, quoted or unquoted values, inline `#` comments.
// Returns true if at least one MML_* entry was extracted.
static bool load_mml_env_vars(const std::string& path, std::vector<std::string>& out) {
    std::ifstream f(path);
    if (!f.is_open()) return false;

    std::string line;
    int found = 0;
    while (std::getline(f, line)) {
        std::string raw = line;
        // Find first non-whitespace char.
        size_t start = 0;
        while (start < raw.size() && (raw[start] == ' ' || raw[start] == '\t')) ++start;
        if (start == raw.size()) continue;          // blank
        if (raw[start] == '#') continue;             // comment-only

        // Must start with MML_
        if (raw.compare(start, 4, "MML_") != 0) continue;

        // Find ':' separator (skip past the key).
        size_t colon = raw.find(':', start);
        if (colon == std::string::npos) continue;

        std::string key = raw.substr(start, colon - start);

        // Value starts after the colon, skipping whitespace.
        size_t v = colon + 1;
        while (v < raw.size() && (raw[v] == ' ' || raw[v] == '\t')) ++v;

        // Value ends at inline comment or end of line.
        size_t vend = raw.size();
        size_t hash = raw.find('#', v);
        if (hash != std::string::npos && hash >= v) vend = hash;
        std::string value = raw.substr(v, vend - v);
        trim(value);
        unquote(value);

        if (!key.empty() && !value.empty()) {
            out.push_back(key + "=" + value);
            ++found;
        }
    }
    return found > 0;
}

static size_t leading_spaces(const std::string& s) {
    size_t n = 0;
    while (n < s.size() && s[n] == ' ') ++n;
    return n;
}

static bool yaml_scalar_at(const std::string& line, const std::string& key,
                           std::string& value) {
    std::string s = line;
    trim(s);
    if (s.empty() || s[0] == '#') return false;
    if (s.compare(0, key.size(), key) != 0) return false;
    size_t pos = key.size();
    while (pos < s.size() && (s[pos] == ' ' || s[pos] == '\t')) ++pos;
    if (pos >= s.size() || s[pos] != ':') return false;
    ++pos;
    while (pos < s.size() && (s[pos] == ' ' || s[pos] == '\t')) ++pos;
    value = s.substr(pos);
    size_t hash = value.find('#');
    if (hash != std::string::npos) value = value.substr(0, hash);
    trim(value);
    unquote(value);
    return !value.empty();
}

static std::string load_yaml_scalar(const std::string& path,
                                    const std::string& section,
                                    const std::string& key,
                                    const std::string& def) {
    std::ifstream f(path);
    if (!f.is_open()) return def;
    std::string line;
    bool in_section = section.empty();
    size_t section_indent = 0;
    while (std::getline(f, line)) {
        if (section.empty()) {
            std::string value;
            if (yaml_scalar_at(line, key, value)) return value;
            continue;
        }

        std::string stripped = line;
        trim(stripped);
        if (stripped.empty() || stripped[0] == '#') continue;
        size_t indent = leading_spaces(line);
        if (!in_section) {
            if (stripped == section + ":") {
                in_section = true;
                section_indent = indent;
            }
            continue;
        }
        if (indent <= section_indent) break;
        std::string value;
        if (yaml_scalar_at(line, key, value)) return value;
    }
    return def;
}

static std::string get_env_or_string(const char* key, const std::string& def) {
    const char* v = std::getenv(key);
    return v && v[0] ? std::string(v) : def;
}

static std::string json_escape(const std::string& s) {
    std::string out;
    out.reserve(s.size() + 8);
    for (char c : s) {
        switch (c) {
            case '\\': out += "\\\\"; break;
            case '"': out += "\\\""; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default: out += c; break;
        }
    }
    return out;
}

static std::string utc_date() {
    std::time_t t = std::time(nullptr);
    std::tm tm{};
#if defined(_WIN32)
    gmtime_s(&tm, &t);
#else
    gmtime_r(&t, &tm);
#endif
    char buf[16];
    std::strftime(buf, sizeof(buf), "%Y-%m-%d", &tm);
    return std::string(buf);
}

static std::string utc_timestamp() {
    std::time_t t = std::time(nullptr);
    std::tm tm{};
#if defined(_WIN32)
    gmtime_s(&tm, &t);
#else
    gmtime_r(&t, &tm);
#endif
    char buf[32];
    std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &tm);
    return std::string(buf);
}

static std::string default_config_path(const std::string& repo) {
    const std::string profile = get_env_or_string("RITHMIC_ENDPOINT_PROFILE", "paper_chicago");
    if (profile == "paper_chicago" || profile == "paper") {
        return repo + "/packages/data_system/config/rithmic_api_paper.yaml";
    }
    return repo + "/packages/data_system/config/rithmic_api_test.yaml";
}

static double duration_us(uint64_t start_ns, uint64_t end_ns) {
    if (start_ns == 0 || end_ns == 0 || end_ns < start_ns) return 0.0;
    return static_cast<double>(end_ns - start_ns) / 1000.0;
}

struct MetricSummary {
    size_t count = 0;
    double min_us = 0.0;
    double mean_us = 0.0;
    double p50_us = 0.0;
    double p90_us = 0.0;
    double p95_us = 0.0;
    double p99_us = 0.0;
    double p99_9_us = 0.0;
    double max_us = 0.0;
};

struct BaselineSample {
    std::string timestamp_utc;
    std::string order_action;
    std::string side;
    std::string order_type;
    int quantity = 0;
    bool has_order_metrics = true;
    bool has_send_to_ack = true;
    bool has_cancel_metrics = false;
    bool has_cancel_to_ack = false;
    double tick_to_decision_us = 0.0;
    double decision_to_send_trigger_us = 0.0;
    double tick_to_send_trigger_us = 0.0;
    double decision_to_send_us = 0.0;
    double tick_to_send_us = 0.0;
    double send_to_ack_us = 0.0;
    double rithmic_send_call_us = 0.0;
    double cancel_to_send_us = 0.0;
    double cancel_to_ack_us = 0.0;
    bool success = false;
    std::string reject_reason;
    uint64_t market_event_received_ns = 0;
    uint64_t features_ready_ns = 0;
    uint64_t decision_ready_ns = 0;
    uint64_t risk_check_ready_ns = 0;
    uint64_t order_ready_ns = 0;
    uint64_t order_api_call_start_ns = 0;
    uint64_t order_api_call_end_ns = 0;
    uint64_t order_send_ns = 0;
    uint64_t ack_received_ns = 0;
    uint64_t cancel_decision_ns = 0;
    uint64_t cancel_send_ns = 0;
    uint64_t cancel_ack_received_ns = 0;
    uint64_t broker_order_id = 0;
};

struct RuntimeTuningStatus {
    std::string platform;
    int requested_cpu = -1;
    bool affinity_requested = false;
    bool affinity_applied = false;
    std::string affinity_error;
    int requested_rt_priority = 0;
    bool rt_priority_requested = false;
    bool rt_priority_applied = false;
    std::string rt_priority_error;
    bool memory_lock_requested = false;
    bool memory_lock_applied = false;
    std::string memory_lock_error;
    int prefault_bytes = 0;
    bool prefault_requested = false;
    bool prefault_applied = false;
    std::string prefault_error;
};

static std::string errno_message(int err) {
    if (err == 0) return "";
    return std::strerror(err);
}

static RuntimeTuningStatus apply_runtime_tuning() {
    RuntimeTuningStatus status;
#if defined(_WIN32)
    status.platform = "windows";
#else
    status.platform = "linux";
#endif

    status.requested_cpu = get_env_int_or(
        "RITHMIC_PROBE_CPU",
        get_env_int_or("HFT3_RITHMIC_CPU", -1)
    );
    status.affinity_requested = status.requested_cpu >= 0;
    if (status.affinity_requested) {
#if defined(_WIN32)
        const int bit_count = static_cast<int>(sizeof(DWORD_PTR) * 8);
        if (status.requested_cpu >= bit_count) {
            status.affinity_error = "requested CPU exceeds affinity mask width";
        } else {
            DWORD_PTR mask = static_cast<DWORD_PTR>(1) << status.requested_cpu;
            if (SetProcessAffinityMask(GetCurrentProcess(), mask)) {
                status.affinity_applied = true;
            } else {
                status.affinity_error = "SetProcessAffinityMask failed";
            }
        }
#else
        cpu_set_t set;
        CPU_ZERO(&set);
        CPU_SET(status.requested_cpu, &set);
        if (sched_setaffinity(0, sizeof(set), &set) == 0) {
            status.affinity_applied = true;
        } else {
            status.affinity_error = errno_message(errno);
        }
#endif
    }

    status.requested_rt_priority = get_env_int_or(
        "RITHMIC_PROBE_RT_PRIORITY",
        get_env_int_or("HFT3_RITHMIC_RT_PRIORITY", 0)
    );
    status.rt_priority_requested = status.requested_rt_priority > 0;
    if (status.rt_priority_requested) {
#if defined(_WIN32)
        const BOOL process_ok = SetPriorityClass(GetCurrentProcess(), REALTIME_PRIORITY_CLASS);
        const BOOL thread_ok = SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_TIME_CRITICAL);
        status.rt_priority_applied = process_ok && thread_ok;
        if (!status.rt_priority_applied) {
            status.rt_priority_error = "SetPriorityClass or SetThreadPriority failed";
        }
#else
        sched_param param{};
        param.sched_priority = status.requested_rt_priority;
        if (sched_setscheduler(0, SCHED_FIFO, &param) == 0) {
            status.rt_priority_applied = true;
        } else {
            status.rt_priority_error = errno_message(errno);
        }
#endif
    }

    status.memory_lock_requested = get_env_bool_or("RITHMIC_PROBE_MLOCK", true);
    if (status.memory_lock_requested) {
#if defined(_WIN32)
        status.memory_lock_error = "mlockall unavailable on Windows; use locked allocator path if needed";
#else
        if (mlockall(MCL_CURRENT | MCL_FUTURE) == 0) {
            status.memory_lock_applied = true;
        } else {
            status.memory_lock_error = errno_message(errno);
        }
#endif
    }

    status.prefault_bytes = get_env_int_or("RITHMIC_PROBE_PREFAULT_BYTES", 16 * 1024 * 1024);
    status.prefault_requested = status.prefault_bytes > 0;
    if (status.prefault_requested) {
        try {
            static std::vector<char> prefault_buffer;
            prefault_buffer.resize(static_cast<size_t>(status.prefault_bytes));
            volatile char sink = 0;
            const size_t page = 4096;
            for (size_t i = 0; i < prefault_buffer.size(); i += page) {
                prefault_buffer[i] = static_cast<char>(prefault_buffer[i] + 1);
                sink ^= prefault_buffer[i];
            }
            if (!prefault_buffer.empty()) {
                prefault_buffer[prefault_buffer.size() - 1] =
                    static_cast<char>(prefault_buffer.back() + sink);
            }
            status.prefault_applied = true;
        } catch (const std::bad_alloc&) {
            status.prefault_error = "prefault allocation failed";
        }
    }

    return status;
}

static void write_runtime_tuning_json(std::ofstream& f,
                                      const RuntimeTuningStatus& status,
                                      int indent) {
    const std::string pad(static_cast<size_t>(indent), ' ');
    f << "{\n"
      << pad << "  \"platform\": \"" << json_escape(status.platform) << "\",\n"
      << pad << "  \"requested_cpu\": " << status.requested_cpu << ",\n"
      << pad << "  \"affinity_requested\": " << (status.affinity_requested ? "true" : "false") << ",\n"
      << pad << "  \"affinity_applied\": " << (status.affinity_applied ? "true" : "false") << ",\n"
      << pad << "  \"affinity_error\": \"" << json_escape(status.affinity_error) << "\",\n"
      << pad << "  \"requested_rt_priority\": " << status.requested_rt_priority << ",\n"
      << pad << "  \"rt_priority_requested\": " << (status.rt_priority_requested ? "true" : "false") << ",\n"
      << pad << "  \"rt_priority_applied\": " << (status.rt_priority_applied ? "true" : "false") << ",\n"
      << pad << "  \"rt_priority_error\": \"" << json_escape(status.rt_priority_error) << "\",\n"
      << pad << "  \"memory_lock_requested\": " << (status.memory_lock_requested ? "true" : "false") << ",\n"
      << pad << "  \"memory_lock_applied\": " << (status.memory_lock_applied ? "true" : "false") << ",\n"
      << pad << "  \"memory_lock_error\": \"" << json_escape(status.memory_lock_error) << "\",\n"
      << pad << "  \"prefault_bytes\": " << status.prefault_bytes << ",\n"
      << pad << "  \"prefault_requested\": " << (status.prefault_requested ? "true" : "false") << ",\n"
      << pad << "  \"prefault_applied\": " << (status.prefault_applied ? "true" : "false") << ",\n"
      << pad << "  \"prefault_error\": \"" << json_escape(status.prefault_error) << "\"\n"
      << pad << "}";
}

static MetricSummary summarize_metric(std::vector<double> values) {
    MetricSummary summary;
    if (values.empty()) return summary;
    std::sort(values.begin(), values.end());
    summary.count = values.size();
    summary.min_us = values.front();
    summary.max_us = values.back();
    summary.mean_us = std::accumulate(values.begin(), values.end(), 0.0)
        / static_cast<double>(values.size());
    summary.p50_us = pct_us(values, 50.0);
    summary.p90_us = pct_us(values, 90.0);
    summary.p95_us = pct_us(values, 95.0);
    summary.p99_us = pct_us(values, 99.0);
    summary.p99_9_us = pct_us(values, 99.9);
    return summary;
}

static void write_metric_json(std::ofstream& f, const MetricSummary& s, int indent) {
    const std::string pad(static_cast<size_t>(indent), ' ');
    f << "{\n"
      << pad << "  \"count\": " << s.count << ",\n"
      << pad << "  \"min_us\": " << s.min_us << ",\n"
      << pad << "  \"mean_us\": " << s.mean_us << ",\n"
      << pad << "  \"p50_us\": " << s.p50_us << ",\n"
      << pad << "  \"p90_us\": " << s.p90_us << ",\n"
      << pad << "  \"p95_us\": " << s.p95_us << ",\n"
      << pad << "  \"p99_us\": " << s.p99_us << ",\n"
      << pad << "  \"p99_9_us\": " << s.p99_9_us << ",\n"
      << pad << "  \"max_us\": " << s.max_us << "\n"
      << pad << "}";
}

static void write_null_metric_json(std::ofstream& f, int indent) {
    const std::string pad(static_cast<size_t>(indent), ' ');
    f << "{\n"
      << pad << "  \"count\": 0,\n"
      << pad << "  \"min_us\": null,\n"
      << pad << "  \"mean_us\": null,\n"
      << pad << "  \"p50_us\": null,\n"
      << pad << "  \"p90_us\": null,\n"
      << pad << "  \"p95_us\": null,\n"
      << pad << "  \"p99_us\": null,\n"
      << pad << "  \"p99_9_us\": null,\n"
      << pad << "  \"max_us\": null\n"
      << pad << "}";
}

static void write_metric_or_null_json(std::ofstream& f, const MetricSummary& s, int indent) {
    if (s.count > 0) {
        write_metric_json(f, s, indent);
    } else {
        write_null_metric_json(f, indent);
    }
}

static void append_sample_jsonl(const std::string& path,
                                const std::string& run_id,
                                const std::string& environment,
                                const std::string& broker,
                                const std::string& venue,
                                const std::string& symbol,
                                const std::string& strategy_id,
                                const std::string& model_id,
                                const std::string& trade_manager_id,
                                const BaselineSample& s) {
    std::ofstream f(path, std::ios::app);
    if (!f.is_open()) return;
    f << std::fixed << std::setprecision(3);
    auto metric = [&](bool present, double value) {
        if (present) f << value;
        else f << "null";
    };
    f << "{\"run_id\":\"" << json_escape(run_id) << "\","
      << "\"timestamp_utc\":\"" << json_escape(s.timestamp_utc) << "\","
      << "\"environment\":\"" << json_escape(environment) << "\","
      << "\"broker\":\"" << json_escape(broker) << "\","
      << "\"venue\":\"" << json_escape(venue) << "\","
      << "\"symbol\":\"" << json_escape(symbol) << "\","
      << "\"strategy_id\":\"" << json_escape(strategy_id) << "\","
      << "\"model_id\":\"" << json_escape(model_id) << "\","
      << "\"trade_manager_id\":\"" << json_escape(trade_manager_id) << "\","
      << "\"order_action\":\"" << json_escape(s.order_action) << "\","
      << "\"side\":\"" << json_escape(s.side) << "\","
      << "\"order_type\":\"" << json_escape(s.order_type) << "\","
      << "\"quantity\":" << s.quantity << ","
      << "\"tick_to_decision_us\":";
    metric(s.has_order_metrics, s.tick_to_decision_us);
    f << ",\"decision_to_send_trigger_us\":";
    metric(s.has_order_metrics, s.decision_to_send_trigger_us);
    f << ",\"tick_to_send_trigger_us\":";
    metric(s.has_order_metrics, s.tick_to_send_trigger_us);
    f << ",\"decision_to_send_us\":";
    metric(s.has_order_metrics, s.decision_to_send_us);
    f << ",\"tick_to_send_us\":";
    metric(s.has_order_metrics, s.tick_to_send_us);
    f << ",\"rithmic_send_call_us\":";
    metric(s.has_order_metrics, s.rithmic_send_call_us);
    f << ",\"send_to_ack_us\":";
    metric(s.has_send_to_ack, s.send_to_ack_us);
    f << ",\"cancel_to_send_us\":";
    metric(s.has_cancel_metrics, s.cancel_to_send_us);
    f << ",\"cancel_to_ack_us\":";
    metric(s.has_cancel_to_ack, s.cancel_to_ack_us);
    f << ","
      << "\"replace_to_send_us\":null,"
      << "\"replace_to_ack_us\":null,"
      << "\"success\":" << (s.success ? "true" : "false") << ","
      << "\"reject_reason\":\"" << json_escape(s.reject_reason) << "\","
      << "\"raw_timestamps\":{"
      << "\"market_event_received_ts\":" << s.market_event_received_ns << ","
      << "\"features_ready_ts\":" << s.features_ready_ns << ","
      << "\"decision_ready_ts\":" << s.decision_ready_ns << ","
      << "\"risk_check_ready_ts\":" << s.risk_check_ready_ns << ","
      << "\"order_ready_ts\":" << s.order_ready_ns << ","
      << "\"order_api_call_start_ts\":" << s.order_api_call_start_ns << ","
      << "\"order_api_call_end_ts\":" << s.order_api_call_end_ns << ","
      << "\"order_send_ts\":" << s.order_send_ns << ","
      << "\"order_send_return_ts\":" << s.order_send_ns << ","
      << "\"ack_received_ts\":" << s.ack_received_ns << ","
      << "\"cancel_decision_ts\":" << s.cancel_decision_ns << ","
      << "\"cancel_send_ts\":" << s.cancel_send_ns << ","
      << "\"cancel_ack_received_ts\":" << s.cancel_ack_received_ns << ","
      << "\"replace_send_ts\":null,"
      << "\"replace_ack_received_ts\":null"
      << "},"
      << "\"broker_order_id\":" << s.broker_order_id
      << "}\n";
}

static bool write_summary_artifacts(const std::string& report_json_path,
                                    const std::string& report_md_path,
                                    const std::string& sample_path,
                                    const std::string& run_id,
                                    const std::string& environment,
                                    const std::string& broker,
                                    const std::string& venue,
                                    const std::string& symbol,
                                    const std::string& strategy_id,
                                    int target_samples,
                                    int ack_count,
                                    int reject_count,
                                    int failure_count,
                                    int timeout_count,
                                    int cancel_submit_count,
                                    const std::string& stop_reason,
                                    const RuntimeTuningStatus& tuning_status,
                                    const std::vector<BaselineSample>& samples) {
    std::vector<double> tick_to_decision;
    std::vector<double> decision_to_send_trigger;
    std::vector<double> tick_to_send_trigger;
    std::vector<double> decision_to_send;
    std::vector<double> tick_to_send;
    std::vector<double> rithmic_send_call;
    std::vector<double> send_to_ack;
    std::vector<double> cancel_to_send;
    std::vector<double> cancel_to_ack;
    tick_to_decision.reserve(samples.size());
    decision_to_send_trigger.reserve(samples.size());
    tick_to_send_trigger.reserve(samples.size());
    decision_to_send.reserve(samples.size());
    tick_to_send.reserve(samples.size());
    rithmic_send_call.reserve(samples.size());
    send_to_ack.reserve(samples.size());
    cancel_to_send.reserve(samples.size());
    cancel_to_ack.reserve(samples.size());
    for (const auto& sample : samples) {
        if (sample.has_order_metrics) {
            tick_to_decision.push_back(sample.tick_to_decision_us);
            decision_to_send_trigger.push_back(sample.decision_to_send_trigger_us);
            tick_to_send_trigger.push_back(sample.tick_to_send_trigger_us);
            decision_to_send.push_back(sample.decision_to_send_us);
            tick_to_send.push_back(sample.tick_to_send_us);
            rithmic_send_call.push_back(sample.rithmic_send_call_us);
        }
        if (sample.success && sample.has_send_to_ack) {
            send_to_ack.push_back(sample.send_to_ack_us);
        }
        if (sample.has_cancel_metrics) {
            cancel_to_send.push_back(sample.cancel_to_send_us);
        }
        if (sample.has_cancel_to_ack) {
            cancel_to_ack.push_back(sample.cancel_to_ack_us);
        }
    }

    const MetricSummary tick_decision = summarize_metric(tick_to_decision);
    const MetricSummary decision_send_trigger = summarize_metric(decision_to_send_trigger);
    const MetricSummary tick_send_trigger = summarize_metric(tick_to_send_trigger);
    const MetricSummary decision_send = summarize_metric(decision_to_send);
    const MetricSummary tick_send = summarize_metric(tick_to_send);
    const MetricSummary send_call = summarize_metric(rithmic_send_call);
    const MetricSummary send_ack = summarize_metric(send_to_ack);
    const MetricSummary cancel_send = summarize_metric(cancel_to_send);
    const MetricSummary cancel_ack = summarize_metric(cancel_to_ack);

    std::ofstream jf(report_json_path);
    if (!jf.is_open()) return false;
    jf << std::fixed << std::setprecision(3);
    jf << "{\n"
       << "  \"schema_version\": \"latency_baseline_summary_v1\",\n"
       << "  \"run_id\": \"" << json_escape(run_id) << "\",\n"
       << "  \"generated_at_utc\": \"" << utc_timestamp() << "\",\n"
       << "  \"sample_count\": " << samples.size() << ",\n"
       << "  \"sample_path\": \"" << json_escape(sample_path) << "\",\n"
       << "  \"primary_kpi\": \"tick_to_send_us\",\n"
       << "  \"placement_trigger_kpi\": \"tick_to_send_trigger_us\",\n"
       << "  \"principle\": \"placement speed is separate from broker acknowledgment latency\",\n"
       << "  \"metrics\": {\n"
       << "    \"tick_to_decision_us\": ";
    write_metric_or_null_json(jf, tick_decision, 4);
    jf << ",\n    \"decision_to_send_trigger_us\": ";
    write_metric_or_null_json(jf, decision_send_trigger, 4);
    jf << ",\n    \"tick_to_send_trigger_us\": ";
    write_metric_or_null_json(jf, tick_send_trigger, 4);
    jf << ",\n    \"decision_to_send_us\": ";
    write_metric_or_null_json(jf, decision_send, 4);
    jf << ",\n    \"tick_to_send_us\": ";
    write_metric_or_null_json(jf, tick_send, 4);
    jf << ",\n    \"rithmic_send_call_us\": ";
    write_metric_or_null_json(jf, send_call, 4);
    jf << ",\n    \"send_to_ack_us\": ";
    write_metric_or_null_json(jf, send_ack, 4);
    jf << ",\n    \"cancel_to_send_us\": ";
    if (cancel_send.count > 0) write_metric_json(jf, cancel_send, 4);
    else write_null_metric_json(jf, 4);
    jf << ",\n    \"cancel_to_ack_us\": ";
    if (cancel_ack.count > 0) write_metric_json(jf, cancel_ack, 4);
    else write_null_metric_json(jf, 4);
    jf << ",\n    \"replace_to_send_us\": ";
    write_null_metric_json(jf, 4);
    jf << ",\n    \"replace_to_ack_us\": ";
    write_null_metric_json(jf, 4);
    jf << "\n  },\n"
       << "  \"views\": {\n"
       << "    \"offensive\": [\"tick_to_decision_us\", \"decision_to_send_trigger_us\", \"tick_to_send_trigger_us\", \"decision_to_send_us\", \"tick_to_send_us\", \"rithmic_send_call_us\"],\n"
       << "    \"defensive\": [\"cancel_to_send_us\", \"cancel_to_ack_us\", \"replace_to_send_us\", \"replace_to_ack_us\"],\n"
       << "    \"round_trip\": [\"send_to_ack_us\"]\n"
       << "  },\n"
       << "  \"broker_mode\": {\n"
       << "    \"status\": \"" << (tick_send.count > 0 ? "observed" : "blocked") << "\",\n"
       << "    \"broker\": \"" << json_escape(broker) << "\",\n"
       << "    \"environment\": \"" << json_escape(environment) << "\",\n"
       << "    \"requested_symbol\": \"" << json_escape(symbol) << "\",\n"
       << "    \"resolved_symbol\": \"" << json_escape(symbol) << "\",\n"
       << "    \"venue\": \"" << json_escape(venue) << "\",\n"
       << "    \"order_action\": \"new\",\n"
       << "    \"note\": \"tick_to_send_trigger_us is market callback to Rithmic SDK call entry; tick_to_send_us is market callback to SDK call return\"\n"
       << "  },\n"
       << "  \"broker_artifacts\": {\n"
       << "    \"hot_path_language\": \"c++\",\n"
       << "    \"wrapper\": \"none\",\n"
       << "    \"probe\": \"rithmic_latency_probe\",\n"
       << "    \"evidence_status\": \"" << (tick_send.count > 0 ? "observed" : "blocked") << "\",\n"
       << "    \"target_samples\": " << target_samples << ",\n"
       << "    \"records_written\": " << samples.size() << ",\n"
       << "    \"ack_count\": " << ack_count << ",\n"
       << "    \"reject_count\": " << reject_count << ",\n"
       << "    \"failure_count\": " << failure_count << ",\n"
       << "    \"timeout_count\": " << timeout_count << ",\n"
       << "    \"cancel_submit_count\": " << cancel_submit_count << ",\n"
       << "    \"stop_reason\": \"" << json_escape(stop_reason) << "\"\n"
       << "  },\n"
       << "  \"runtime_tuning\": ";
    write_runtime_tuning_json(jf, tuning_status, 2);
    jf << "\n}\n";
    jf.close();

    std::ofstream mf(report_md_path);
    if (!mf.is_open()) return false;
    mf << "# Latency Baseline " << run_id << "\n\n"
       << "Hot path: C++ direct Rithmic adapter, no Python broker wrapper.\n\n"
       << "| Metric | Count | p50 us | p90 us | p95 us | p99 us | p99.9 us | max us |\n"
       << "|---|---:|---:|---:|---:|---:|---:|---:|\n";
    auto md_row = [&](const char* name, const MetricSummary& s) {
        mf << "| " << name << " | " << s.count << " | "
           << s.p50_us << " | " << s.p90_us << " | " << s.p95_us << " | "
           << s.p99_us << " | " << s.p99_9_us << " | " << s.max_us << " |\n";
    };
    md_row("tick_to_decision_us", tick_decision);
    md_row("decision_to_send_trigger_us", decision_send_trigger);
    md_row("tick_to_send_trigger_us", tick_send_trigger);
    md_row("decision_to_send_us", decision_send);
    md_row("tick_to_send_us", tick_send);
    md_row("rithmic_send_call_us", send_call);
    md_row("send_to_ack_us", send_ack);
    md_row("cancel_to_send_us", cancel_send);
    md_row("cancel_to_ack_us", cancel_ack);
    mf << "\n## Runtime Tuning\n\n"
       << "| Setting | Requested | Applied | Detail |\n"
       << "|---|---:|---:|---|\n"
       << "| CPU affinity | " << tuning_status.requested_cpu << " | "
       << (tuning_status.affinity_applied ? "yes" : "no") << " | "
       << json_escape(tuning_status.affinity_error) << " |\n"
       << "| RT priority | " << tuning_status.requested_rt_priority << " | "
       << (tuning_status.rt_priority_applied ? "yes" : "no") << " | "
       << json_escape(tuning_status.rt_priority_error) << " |\n"
       << "| Memory lock | " << (tuning_status.memory_lock_requested ? "yes" : "no") << " | "
       << (tuning_status.memory_lock_applied ? "yes" : "no") << " | "
       << json_escape(tuning_status.memory_lock_error) << " |\n"
       << "| Prefault bytes | " << tuning_status.prefault_bytes << " | "
       << (tuning_status.prefault_applied ? "yes" : "no") << " | "
       << json_escape(tuning_status.prefault_error) << " |\n"
       << "\nStop reason: `" << stop_reason << "`\n";
    return true;
}

int main(int argc, char** argv) {
    (void)argc; (void)argv;
    std::setvbuf(stdout, nullptr, _IONBF, 0);
    std::setvbuf(stderr, nullptr, _IONBF, 0);

    const RuntimeTuningStatus tuning_status = apply_runtime_tuning();
    std::printf("RUNTIME_TUNING platform=%s cpu_requested=%d cpu_applied=%d"
                " rt_requested=%d rt_applied=%d mlock_requested=%d mlock_applied=%d"
                " prefault_bytes=%d prefault_applied=%d\n",
                tuning_status.platform.c_str(),
                tuning_status.requested_cpu,
                tuning_status.affinity_applied ? 1 : 0,
                tuning_status.requested_rt_priority,
                tuning_status.rt_priority_applied ? 1 : 0,
                tuning_status.memory_lock_requested ? 1 : 0,
                tuning_status.memory_lock_applied ? 1 : 0,
                tuning_status.prefault_bytes,
                tuning_status.prefault_applied ? 1 : 0);
    if (!tuning_status.affinity_error.empty()) {
        std::printf("RUNTIME_TUNING_AFFINITY_WARN %s\n", tuning_status.affinity_error.c_str());
    }
    if (!tuning_status.rt_priority_error.empty()) {
        std::printf("RUNTIME_TUNING_RT_WARN %s\n", tuning_status.rt_priority_error.c_str());
    }
    if (!tuning_status.memory_lock_error.empty()) {
        std::printf("RUNTIME_TUNING_MLOCK_WARN %s\n", tuning_status.memory_lock_error.c_str());
    }
    if (!tuning_status.prefault_error.empty()) {
        std::printf("RUNTIME_TUNING_PREFAULT_WARN %s\n", tuning_status.prefault_error.c_str());
    }

    const char* user = get_env_or("RITHMIC_USERNAME", "");
    const char* pass = get_env_or("RITHMIC_PASSWORD", "");
    if (!user[0] || !pass[0]) {
        std::fprintf(stderr, "FAIL: Set RITHMIC_USERNAME and RITHMIC_PASSWORD\n");
        return 1;
    }

    std::string repo = repo_root();
    std::vector<std::string> env_vars;
    std::string yaml_path = get_env_or_string("RITHMIC_CONFIG_PATH", default_config_path(repo));
    if (!load_mml_env_vars(yaml_path, env_vars)) {
        std::fprintf(stderr, "FAIL: could not load MML_* env from %s\n",
                     yaml_path.c_str());
        return 1;
    }

    std::string ssl_path = repo + "/rithmic_gateway/RApiPlus/13.7.0.0/etc/rithmic_ssl_cert_auth_params";
    env_vars.push_back("MML_SSL_CLNT_AUTH_FILE=" + ssl_path);

    hft::ConnectionConfig cfg;
    cfg.environment = get_env_or_string(
        "RITHMIC_ENVIRONMENT",
        load_yaml_scalar(yaml_path, "", "system", "Rithmic Paper Trading")
    );
    cfg.username = user;
    cfg.password = pass;
    cfg.app_name = load_yaml_scalar(yaml_path, "engine_params", "app_name", "HFT3-LatencyProbe");
    cfg.app_version = load_yaml_scalar(yaml_path, "engine_params", "app_version", "1.0");
    cfg.log_file_path = repo + "/runtime/rithmic_latency_probe.log";
    cfg.rep_connect_point = get_env_or_string(
        "RITHMIC_REP_CONNECT_POINT",
        load_yaml_scalar(yaml_path, "repository_login", "sCnnctPt", "login_agent_repositoryc")
    );
    cfg.md_connect_point = get_env_or_string(
        "RITHMIC_MD_CONNECT_POINT",
        load_yaml_scalar(yaml_path, "login_params", "sMdCnnctPt", "login_agent_tp_agg_paperc")
    );
    cfg.ts_connect_point = get_env_or_string(
        "RITHMIC_TS_CONNECT_POINT",
        load_yaml_scalar(yaml_path, "login_params", "sTsCnnctPt", "login_agent_op_paperc")
    );
    cfg.pnl_connect_point = get_env_or_string(
        "RITHMIC_PNL_CONNECT_POINT",
        load_yaml_scalar(yaml_path, "login_params", "sPnlCnnctPt", "login_agent_pnl_paperc")
    );
    cfg.ih_connect_point = get_env_or_string(
        "RITHMIC_IH_CONNECT_POINT",
        load_yaml_scalar(yaml_path, "login_params", "sIhCnnctPt", "login_agent_history_paperc")
    );
    cfg.env_vars = env_vars;

    auto mbo_queue = std::make_unique<hft::SPSCQueue<hft::MarketDataEvent, 8192>>();
    auto order_queue = std::make_unique<hft::SPSCQueue<hft::OrderEvent, 8192>>();

    hft::RithmicAdapter adapter(cfg, mbo_queue.get(), order_queue.get());
    const std::string env_name_str = get_env_or_string("RITHMIC_PROBE_ENV_LABEL", "paper");
    const char* env_name = env_name_str.c_str();

    // --- Phase 1: Initialize ---
    if (!adapter.initialize()) {
        std::fprintf(stderr, "FAIL [%s] initialize\n", env_name);
        return 2;
    }

    // --- Phase 2: Connect ---
    auto t1 = std::chrono::steady_clock::now();
    if (!adapter.connect()) {
        auto dur = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - t1).count();
        std::fprintf(stderr,
            "FAIL [%s] connect (%.3f ms)\n"
            "  error: %s\n"
            "  env_key='%s' account='%s' route='%s'\n"
            "  --- agreements ---\n%s\n",
            env_name, dur / 1000.0,
            adapter.last_connect_error(),
            adapter.cached_env_key(),
            adapter.cached_account_id(),
            adapter.cached_trade_route(),
            adapter.last_agreement_list_text());
        return 3;
    }
    auto t_conn = std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - t1).count();

    std::printf("OK [%s] connect %.3f ms\n"
                "  env_key='%s' account='%s' route='%s'\n"
                "  --- agreements ---\n%s\n",
                env_name, t_conn / 1000.0,
                adapter.cached_env_key(),
                adapter.cached_account_id(),
                adapter.cached_trade_route(),
                adapter.last_agreement_list_text());

    // --- Phase 3: Market-data parameters ---
    const char* symbol = get_env_or("RITHMIC_PROBE_SYMBOL", "MESM6");
    const char* exchange = get_env_or("RITHMIC_PROBE_EXCHANGE", "CME");
    bool subscribed_mbo = false;

    // --- Phase 4: Native C++ order/cancel latency baseline ---
    const int count = get_env_int_or("RITHMIC_PROBE_ORDER_COUNT", 1);
    const int qty = get_env_int_or("RITHMIC_PROBE_ORDER_QTY", 1);
    const int timeout_ms = get_env_int_or("RITHMIC_PROBE_ORDER_TIMEOUT_MS", 10000);
    const int md_timeout_ms = get_env_int_or("RITHMIC_PROBE_MD_TIMEOUT_MS", 15000);
    const int interval_us = get_env_int_or("RITHMIC_PROBE_ORDER_INTERVAL_US", 0);
    const bool cancel_after_ack = get_env_bool_or("RITHMIC_PROBE_CANCEL_AFTER_ACK", true);
    const bool require_md = get_env_bool_or("RITHMIC_PROBE_REQUIRE_MD", true);
    const bool md_smoke_enabled = !get_env_bool_or("RITHMIC_PROBE_SKIP_MD", false);
    const int md_smoke_timeout_ms = get_env_int_or("RITHMIC_PROBE_MD_SMOKE_TIMEOUT_MS", 5000);
    const bool subscribe_for_order_events =
        get_env_bool_or("RITHMIC_PROBE_SUBSCRIBE_FOR_ORDER_EVENTS", false);
    const int post_subscribe_warm_ms =
        get_env_int_or("RITHMIC_PROBE_POST_SUBSCRIBE_WARM_MS", 0);
    const bool debug_events = get_env_bool_or("RITHMIC_PROBE_DEBUG_EVENTS", false);
    const char side = get_env_or("RITHMIC_PROBE_ORDER_SIDE", "B")[0] == 'S' ? 'S' : 'B';
    const char* order_price_raw = get_env_or("RITHMIC_PROBE_ORDER_PRICE", "");
    const double explicit_order_price = order_price_raw[0] ? std::atof(order_price_raw) : 0.0;
    const double passive_offset = std::atof(get_env_or("RITHMIC_PROBE_PASSIVE_PRICE_OFFSET", "1000.0"));
    const std::string run_id = get_env_or_string(
        "RITHMIC_PROBE_RUN_ID",
        std::string("rithmic-cpp-paper-baseline-") + run_id_utc()
    );
    const std::string strategy_id = get_env_or_string("RITHMIC_PROBE_STRATEGY_ID", "latency_probe");
    const std::string model_id = get_env_or_string("RITHMIC_PROBE_MODEL_ID", "latency_probe");
    const std::string trade_manager_id = get_env_or_string("RITHMIC_PROBE_TRADE_MANAGER_ID", "native_cpp_probe");

    if (count <= 0 || qty <= 0) {
        std::fprintf(stderr, "FAIL [%s] invalid order count/qty\n", env_name);
        adapter.disconnect();
        return 4;
    }

    std::filesystem::path data_dir = std::filesystem::path(repo)
        / "data" / "latency_baselines" / utc_date();
    std::filesystem::path report_dir = std::filesystem::path(repo)
        / "reports" / "latency_baselines";
    std::filesystem::create_directories(data_dir);
    std::filesystem::create_directories(report_dir);
    const std::string sample_path = (data_dir / (run_id + ".jsonl")).string();
    const std::string report_json_path = (report_dir / (run_id + "_summary.json")).string();
    const std::string report_md_path = (report_dir / (run_id + "_summary.md")).string();
    {
        std::ofstream clear(sample_path, std::ios::trunc);
    }

    hft::OrderEvent stale_order;
    while (order_queue->pop(stale_order)) {}

    auto t_warm = std::chrono::steady_clock::now();
    if (!adapter.warm_price_increment(symbol, exchange)) {
        std::fprintf(stderr, "FAIL [%s] warm_price_increment(%s/%s)\n",
                     env_name, exchange, symbol);
        adapter.disconnect();
        return 6;
    }
    auto warm_us = std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - t_warm).count();

    hft::PreparedLimitOrder* prepared_order = adapter.prepare_limit_order(symbol, exchange);
    if (!prepared_order) {
        std::fprintf(stderr, "FAIL [%s] prepare_limit_order(%s/%s)\n",
                     env_name, exchange, symbol);
        adapter.disconnect();
        return 6;
    }

    std::vector<std::string> user_msgs;
    user_msgs.reserve(static_cast<size_t>(count));
    for (int i = 0; i < count; ++i) {
        std::ostringstream tag;
        tag << "hft3cpp-" << run_id << "-" << (i + 1);
        user_msgs.push_back(tag.str());
    }

    std::vector<BaselineSample> samples;
    samples.reserve(static_cast<size_t>(count * (cancel_after_ack ? 2 : 1)));
    int ack_count = 0;
    int reject_count = 0;
    int failure_count = 0;
    int timeout_count = 0;
    int cancel_submit_count = 0;
    int debug_event_count = 0;
    std::string stop_reason = "completed";

    std::printf("OK [%s] native_baseline_start run_id=%s symbol=%s exchange=%s"
                " side=%c qty=%d count=%d require_md=%d warm_price_incr=%.3f ms"
                " sample_path=%s\n",
                env_name, run_id.c_str(), symbol, exchange, side, qty, count,
                require_md ? 1 : 0, warm_us / 1000.0, sample_path.c_str());

    if ((require_md || subscribe_for_order_events) && !subscribed_mbo) {
        auto t_sub = std::chrono::steady_clock::now();
        if (!adapter.subscribe_mbo(symbol, exchange)) {
            std::fprintf(stderr, "FAIL [%s] subscribe_mbo(%s/%s) required for %s\n",
                         env_name, exchange, symbol,
                         require_md ? "tick-to-send baseline" : "order-event warming");
            adapter.destroy_prepared_limit_order(prepared_order);
            adapter.disconnect();
            return 5;
        }
        subscribed_mbo = true;
        auto sub_us = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - t_sub).count();
        std::printf("OK [%s] measurement_subscribe symbol=%s exchange=%s subscribe=%.3f ms\n",
                    env_name, symbol, exchange, sub_us / 1000.0);
        if (post_subscribe_warm_ms > 0) {
            const uint64_t warm_deadline_ns = steady_now_ns()
                + static_cast<uint64_t>(post_subscribe_warm_ms) * 1000000ULL;
            while (steady_now_ns() < warm_deadline_ns) {}
            std::printf("OK [%s] post_subscribe_warm_ms=%d\n",
                        env_name, post_subscribe_warm_ms);
        }
    }

    hft::MarketDataEvent primed_md_event{};
    uint64_t primed_market_event_received_ns = 0;
    bool primed_md_available = false;
    if (require_md && md_smoke_enabled) {
        const uint64_t smoke_deadline_ns = steady_now_ns()
            + static_cast<uint64_t>(md_smoke_timeout_ms) * 1000000ULL;
        while (steady_now_ns() < smoke_deadline_ns) {
            hft::MarketDataEvent ev;
            if (!mbo_queue->pop(ev)) {
                continue;
            }
            if (ev.price <= 0.0) {
                continue;
            }
            primed_md_event = ev;
            primed_market_event_received_ns = ev.callback_monotonic_ns
                ? ev.callback_monotonic_ns
                : steady_now_ns();
            primed_md_available = true;
            std::printf("OK [%s] md_event action=%c side=%c price=%.2f size=%d"
                        "  (primed_for_sample=1)\n",
                        env_name, ev.action, ev.side, ev.price, ev.size);
            break;
        }
        if (!primed_md_available) {
            std::printf("WARN [%s] no priming md event within %d ms\n",
                        env_name, md_smoke_timeout_ms);
        }
    }

    for (int i = 0; i < count; ++i) {
        if (!adapter.set_prepared_limit_order_tag(
                prepared_order,
                user_msgs[static_cast<size_t>(i)].c_str())) {
            stop_reason = "prepare_tag_failed";
            ++failure_count;
            std::printf("ORDER_RESULT index=%d status=prepare_tag_failed\n", i + 1);
            break;
        }

        hft::MarketDataEvent md_event{};
        uint64_t market_event_received_ns = 0;
        double md_price = 0.0;
        if (require_md) {
            bool got_md = false;
            if (primed_md_available) {
                md_event = primed_md_event;
                market_event_received_ns = primed_market_event_received_ns;
                md_price = md_event.price;
                primed_md_available = false;
                got_md = true;
            } else {
                const uint64_t md_deadline_ns = steady_now_ns()
                    + static_cast<uint64_t>(md_timeout_ms) * 1000000ULL;
                while (steady_now_ns() < md_deadline_ns) {
                    hft::MarketDataEvent ev;
                    if (!mbo_queue->pop(ev)) {
                        continue;
                    }
                    if (ev.price <= 0.0) {
                        continue;
                    }
                    md_event = ev;
                    market_event_received_ns = ev.callback_monotonic_ns
                        ? ev.callback_monotonic_ns
                        : steady_now_ns();
                    md_price = ev.price;
                    got_md = true;
                    break;
                }
            }
            if (!got_md) {
                stop_reason = "missing_market_data";
                std::printf("ORDER_RESULT index=%d status=missing_market_data\n", i + 1);
                break;
            }
        } else {
            market_event_received_ns = steady_now_ns();
            md_price = explicit_order_price;
        }

        BaselineSample order_sample{};
        order_sample.timestamp_utc = utc_timestamp();
        order_sample.order_action = "new";
        order_sample.side = side == 'S' ? "SELL" : "BUY";
        order_sample.order_type = "limit";
        order_sample.quantity = qty;
        order_sample.market_event_received_ns = market_event_received_ns;
        order_sample.features_ready_ns = steady_now_ns();

        double order_price = explicit_order_price;
        if (order_price <= 0.0) {
            order_price = side == 'S' ? md_price + passive_offset : md_price - passive_offset;
        }
        order_sample.decision_ready_ns = steady_now_ns();
        order_sample.risk_check_ready_ns = steady_now_ns();
        if (order_price <= 0.0) {
            stop_reason = "invalid_passive_price";
            ++failure_count;
            order_sample.success = false;
            order_sample.reject_reason = stop_reason;
            order_sample.order_ready_ns = steady_now_ns();
            order_sample.order_send_ns = order_sample.order_ready_ns;
            order_sample.ack_received_ns = 0;
            samples.push_back(order_sample);
            append_sample_jsonl(sample_path, run_id, env_name_str, "rithmic", exchange,
                                symbol, strategy_id, model_id, trade_manager_id, order_sample);
            break;
        }
        order_sample.order_ready_ns = steady_now_ns();
        order_sample.order_api_call_start_ns = steady_now_ns();
        order_sample.decision_to_send_trigger_us = duration_us(
            order_sample.decision_ready_ns,
            order_sample.order_api_call_start_ns
        );
        order_sample.tick_to_send_trigger_us = duration_us(
            order_sample.market_event_received_ns,
            order_sample.order_api_call_start_ns
        );
        bool order_sent = adapter.send_prepared_limit_order(
            prepared_order,
            side,
            qty,
            order_price
        );
        order_sample.order_api_call_end_ns = steady_now_ns();
        order_sample.order_send_ns = order_sample.order_api_call_end_ns;
        order_sample.tick_to_decision_us = duration_us(order_sample.market_event_received_ns,
                                                       order_sample.decision_ready_ns);
        order_sample.decision_to_send_us = duration_us(order_sample.decision_ready_ns,
                                                       order_sample.order_send_ns);
        order_sample.tick_to_send_us = duration_us(order_sample.market_event_received_ns,
                                                   order_sample.order_send_ns);
        order_sample.rithmic_send_call_us = duration_us(order_sample.order_api_call_start_ns,
                                                        order_sample.order_api_call_end_ns);
        if (!order_sent) {
            ++failure_count;
            stop_reason = "send_failed";
            order_sample.success = false;
            order_sample.reject_reason = stop_reason;
            samples.push_back(order_sample);
            append_sample_jsonl(sample_path, run_id, env_name_str, "rithmic", exchange,
                                symbol, strategy_id, model_id, trade_manager_id, order_sample);
            std::printf("ORDER_RESULT index=%d status=send_failed tick_to_send_us=%.3f\n",
                        i + 1, order_sample.tick_to_send_us);
            break;
        }

        if (debug_events) {
            std::printf("ORDER_SENT index=%d user_msg=%s order_send_ns=%llu price=%.2f md_price=%.2f\n",
                        i + 1, user_msgs[static_cast<size_t>(i)].c_str(),
                        static_cast<unsigned long long>(order_sample.order_send_ns),
                        order_price, md_price);
        }

        const uint64_t deadline_ns = order_sample.order_send_ns
            + static_cast<uint64_t>(timeout_ms) * 1000000ULL;
        bool finished = false;
        hft::OrderEvent terminal_event{};
        while (steady_now_ns() < deadline_ns) {
            hft::OrderEvent ev;
            if (!order_queue->pop(ev)) {
                continue;
            }
            if (debug_events && debug_event_count < 40) {
                ++debug_event_count;
                std::printf("ORDER_DEBUG index=%d event_type=%c broker_order_id=%llu"
                            " cb_ns=%llu user_msg='%s' tag='%s'\n",
                            i + 1, ev.event_type,
                            static_cast<unsigned long long>(ev.order_id),
                            static_cast<unsigned long long>(ev.callback_monotonic_ns),
                            fixed_cstr(ev.user_msg, sizeof(ev.user_msg)).c_str(),
                            fixed_cstr(ev.tag, sizeof(ev.tag)).c_str());
            }
            if (!order_event_matches(ev, user_msgs[static_cast<size_t>(i)],
                                     order_sample.order_send_ns)) {
                continue;
            }
            terminal_event = ev;
            finished = true;
            break;
        }

        if (!finished) {
            ++timeout_count;
            stop_reason = "ack_timeout";
            order_sample.success = false;
            order_sample.reject_reason = stop_reason;
            order_sample.has_send_to_ack = false;
            samples.push_back(order_sample);
            append_sample_jsonl(sample_path, run_id, env_name_str, "rithmic", exchange,
                                symbol, strategy_id, model_id, trade_manager_id, order_sample);
            std::printf("ORDER_RESULT index=%d status=timeout tick_to_send_us=%.3f\n",
                        i + 1, order_sample.tick_to_send_us);
            break;
        }

        order_sample.ack_received_ns = terminal_event.callback_monotonic_ns
            ? terminal_event.callback_monotonic_ns
            : steady_now_ns();
        order_sample.send_to_ack_us = duration_us(order_sample.order_send_ns,
                                                  order_sample.ack_received_ns);
        order_sample.broker_order_id = terminal_event.order_id;
        if (terminal_event.event_type == 'A') {
            ++ack_count;
            order_sample.success = true;
            order_sample.reject_reason = "";
        } else if (terminal_event.event_type == 'R') {
            ++reject_count;
            order_sample.success = false;
            order_sample.reject_reason = "order_reject";
            stop_reason = order_sample.reject_reason;
        } else if (terminal_event.event_type == 'X') {
            ++failure_count;
            order_sample.success = false;
            order_sample.reject_reason = "order_failure";
            stop_reason = order_sample.reject_reason;
        } else {
            ++failure_count;
            order_sample.success = false;
            order_sample.reject_reason = "unexpected_order_event";
            stop_reason = order_sample.reject_reason;
        }

        samples.push_back(order_sample);
        append_sample_jsonl(sample_path, run_id, env_name_str, "rithmic", exchange,
                            symbol, strategy_id, model_id, trade_manager_id, order_sample);
        std::printf("ORDER_RESULT index=%d status=%s tick_to_send_us=%.3f send_to_ack_us=%.3f broker_order_id=%llu\n",
                    i + 1, order_sample.success ? "ack" : order_sample.reject_reason.c_str(),
                    order_sample.tick_to_send_us, order_sample.send_to_ack_us,
                    static_cast<unsigned long long>(order_sample.broker_order_id));
        if (!order_sample.success) {
            break;
        }

        if (cancel_after_ack && terminal_event.order_id != 0) {
            BaselineSample cancel_sample{};
            cancel_sample.timestamp_utc = utc_timestamp();
            cancel_sample.order_action = "cancel";
            cancel_sample.side = order_sample.side;
            cancel_sample.order_type = "limit";
            cancel_sample.quantity = qty;
            cancel_sample.has_order_metrics = false;
            cancel_sample.has_send_to_ack = false;
            cancel_sample.has_cancel_metrics = true;
            cancel_sample.broker_order_id = terminal_event.order_id;
            cancel_sample.cancel_decision_ns = steady_now_ns();
            const bool cancel_sent = adapter.cancel_order(std::to_string(terminal_event.order_id));
            cancel_sample.cancel_send_ns = steady_now_ns();
            cancel_sample.cancel_to_send_us = duration_us(cancel_sample.cancel_decision_ns,
                                                          cancel_sample.cancel_send_ns);
            if (cancel_sent) {
                ++cancel_submit_count;
            } else {
                stop_reason = "cancel_send_failed";
                cancel_sample.success = false;
                cancel_sample.reject_reason = stop_reason;
                samples.push_back(cancel_sample);
                append_sample_jsonl(sample_path, run_id, env_name_str, "rithmic", exchange,
                                    symbol, strategy_id, model_id, trade_manager_id, cancel_sample);
                break;
            }

            const uint64_t cancel_deadline_ns = cancel_sample.cancel_send_ns
                + static_cast<uint64_t>(timeout_ms) * 1000000ULL;
            bool cancel_finished = false;
            while (steady_now_ns() < cancel_deadline_ns) {
                hft::OrderEvent ev;
                if (!order_queue->pop(ev)) {
                    continue;
                }
                if (ev.order_id != terminal_event.order_id) {
                    continue;
                }
                if (ev.event_type != 'C' && ev.event_type != 'X' && ev.event_type != 'R') {
                    continue;
                }
                cancel_sample.cancel_ack_received_ns = ev.callback_monotonic_ns
                    ? ev.callback_monotonic_ns
                    : steady_now_ns();
                cancel_sample.cancel_to_ack_us = duration_us(cancel_sample.cancel_send_ns,
                                                            cancel_sample.cancel_ack_received_ns);
                cancel_sample.has_cancel_to_ack = true;
                cancel_sample.success = ev.event_type == 'C';
                cancel_sample.reject_reason = cancel_sample.success ? "" : "cancel_reject_or_failure";
                cancel_finished = true;
                break;
            }
            if (!cancel_finished) {
                stop_reason = "cancel_ack_timeout";
                cancel_sample.success = false;
                cancel_sample.reject_reason = stop_reason;
            }
            samples.push_back(cancel_sample);
            append_sample_jsonl(sample_path, run_id, env_name_str, "rithmic", exchange,
                                symbol, strategy_id, model_id, trade_manager_id, cancel_sample);
            std::printf("CANCEL_RESULT index=%d status=%s cancel_to_send_us=%.3f cancel_to_ack_us=%.3f\n",
                        i + 1, cancel_sample.success ? "ack" : cancel_sample.reject_reason.c_str(),
                        cancel_sample.cancel_to_send_us, cancel_sample.cancel_to_ack_us);
            if (!cancel_sample.success) {
                break;
            }
        }

        if (i + 1 < count && interval_us > 0) {
            const uint64_t pause_until = steady_now_ns() + static_cast<uint64_t>(interval_us) * 1000ULL;
            while (steady_now_ns() < pause_until) {}
        }
    }

    if (!write_summary_artifacts(report_json_path, report_md_path, sample_path, run_id,
                                 env_name_str, "rithmic", exchange, symbol, strategy_id,
                                 count, ack_count, reject_count, failure_count, timeout_count,
                                 cancel_submit_count, stop_reason, tuning_status, samples)) {
        std::fprintf(stderr, "FAIL [%s] could not write latency summary artifacts\n", env_name);
        adapter.destroy_prepared_limit_order(prepared_order);
        adapter.disconnect();
        return 7;
    }
    std::printf("CPP_NATIVE_LATENCY_SUMMARY run_id=%s records=%zu ack=%d reject=%d"
                " failure=%d timeout=%d cancel_submit=%d stop_reason=%s report=%s\n",
                run_id.c_str(), samples.size(), ack_count, reject_count, failure_count,
                timeout_count, cancel_submit_count, stop_reason.c_str(), report_json_path.c_str());

    adapter.destroy_prepared_limit_order(prepared_order);
    adapter.disconnect();
    std::printf("OK [%s] probe complete\n", env_name);
    return 0;
}
