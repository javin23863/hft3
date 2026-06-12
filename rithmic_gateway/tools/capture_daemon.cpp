// capture_daemon.cpp — CC2 binary market-data capture daemon.
//
// Design overview:
//   - Parses a YAML config (same loader style as rithmic_latency_probe).
//   - Logs in once via RithmicAdapter, subscribes all configured symbols.
//   - A single consumer thread drains the SPSC mbo queue and appends
//     fixed-width CaptureRecord structs to per-symbol binary files.
//   - Files rotate at the CME trade-date boundary (17:00 CT).
//   - Per-symbol manifests (JSON) are rewritten every manifest_interval_sec.
//   - A supervisor check runs at each loop iteration:  if no MD event has
//     arrived for stale_threshold_sec during active trading hours, or if
//     md_data_gap() fires, we reconnect.
//   - SIGTERM/SIGINT trigger a clean shutdown: drain queue, flush, fsync,
//     final manifests, exit 0.
//   - Credentials are never logged.  Only env variable names appear in output.
//
// Roll logic overview:
//   - The `roots:` YAML block declares root/exchange/cycle tuples.  At each
//     17:00 CT daily rotation, compute_desired() re-derives the front-month
//     contracts from the contract calendar.  Contracts that fall off the
//     eligible set are closed (ROLL_DROP); new ones are subscribed live
//     (ROLL_ADD), subject to the 16-slot adapter registry ceiling.
//   - If adding new contracts would exceed the 16-slot limit, the daemon
//     logs ROLL_RESTART and exits 0 so systemd relaunches it cleanly.
//   - Explicit `symbols:` entries (if present in the YAML) are never
//     auto-rolled; they persist for the process lifetime.
//   - SymbolState objects live in a std::deque so that push_back never
//     invalidates existing pointers.  The id_to_state[16] lookup array is
//     rebuilt after every subscription round.
//
// Trade-date convention: see capture_format.hpp for full documentation.
// TZ handling: we use gmtime_r + explicit CT UTC-offset arithmetic (see
// ct_utc_offset() in capture_format.hpp).  We deliberately do NOT call
// setenv("TZ",...) because that function is not thread-safe.

#include "rithmic_adapter.hpp"
#include "spsc_queue.hpp"
#include "capture_format.hpp"
#include "contract_calendar.hpp"

#include <algorithm>
#include <atomic>
#include <cassert>
#include <cerrno>
#include <chrono>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <deque>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <memory>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#if defined(_WIN32)
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#else
#include <csignal>
#include <fcntl.h>
#include <unistd.h>
#endif

// ---------------------------------------------------------------------------
// Signal handling
// ---------------------------------------------------------------------------

static std::atomic<bool> g_shutdown{false};

#if !defined(_WIN32)
static void handle_signal(int) {
    g_shutdown.store(true, std::memory_order_relaxed);
}
#endif

static void install_signal_handlers() {
#if !defined(_WIN32)
    struct sigaction sa{};
    sa.sa_handler = handle_signal;
    sigemptyset(&sa.sa_mask);
    sa.sa_flags = SA_RESTART;
    sigaction(SIGTERM, &sa, nullptr);
    sigaction(SIGINT,  &sa, nullptr);
#endif
}

// ---------------------------------------------------------------------------
// Utilities (shared with rithmic_latency_probe style)
// ---------------------------------------------------------------------------

static const char* get_env_or(const char* key, const char* def) {
    const char* v = std::getenv(key);
    return v ? v : def;
}

static std::string get_env_or_string(const char* key, const std::string& def) {
    const char* v = std::getenv(key);
    return v && v[0] ? std::string(v) : def;
}

static int get_env_int_or(const char* key, int def) {
    const char* v = std::getenv(key);
    if (!v || !v[0]) return def;
    char* endp = nullptr;
    long parsed = std::strtol(v, &endp, 10);
    return endp == v ? def : static_cast<int>(parsed);
}

// Return monotonic nanoseconds.
static uint64_t mono_now_ns() {
    return static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch()
        ).count()
    );
}

// Return wall-clock nanoseconds (CLOCK_REALTIME on Linux).
static uint64_t wall_now_ns() {
#if !defined(_WIN32)
    struct timespec ts{};
    clock_gettime(CLOCK_REALTIME, &ts);
    return static_cast<uint64_t>(ts.tv_sec) * 1000000000ULL
         + static_cast<uint64_t>(ts.tv_nsec);
#else
    return static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::system_clock::now().time_since_epoch()
        ).count()
    );
#endif
}

// Wall-clock seconds (whole seconds).
static time_t wall_now_sec() {
    return static_cast<time_t>(wall_now_ns() / 1000000000ULL);
}

// Formatted wall timestamp for log lines: "YYYY-MM-DDTHH:MM:SSZ".
static std::string wall_ts() {
    time_t t = wall_now_sec();
    struct tm tm{};
#if defined(_WIN32)
    gmtime_s(&tm, &t);
#else
    gmtime_r(&t, &tm);
#endif
    char buf[24];
    std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &tm);
    return std::string(buf);
}

// Log a single timestamped line to stderr.
static void log_event(const char* msg) {
    std::fprintf(stderr, "%s %s\n", wall_ts().c_str(), msg);
}

#if defined(__GNUC__) || defined(__clang__)
__attribute__((format(printf, 1, 2)))
#endif
static void log_eventf(const char* fmt, ...);
static void log_eventf(const char* fmt, ...) {
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    std::vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    log_event(buf);
}

// JSON string escaping (minimal — same helper as latency probe).
static std::string json_escape(const std::string& s) {
    std::string out;
    out.reserve(s.size() + 8);
    for (char c : s) {
        switch (c) {
            case '\\': out += "\\\\"; break;
            case '"':  out += "\\\""; break;
            case '\n': out += "\\n";  break;
            case '\r': out += "\\r";  break;
            case '\t': out += "\\t";  break;
            default:   out += c;      break;
        }
    }
    return out;
}

// ---------------------------------------------------------------------------
// Minimal YAML parser (same approach as rithmic_latency_probe)
// ---------------------------------------------------------------------------

static void trim(std::string& s) {
    size_t a = 0;
    while (a < s.size() && (s[a] == ' ' || s[a] == '\t' || s[a] == '\r' || s[a] == '\n')) ++a;
    size_t b = s.size();
    while (b > a && (s[b-1] == ' ' || s[b-1] == '\t' || s[b-1] == '\r' || s[b-1] == '\n')) --b;
    s = s.substr(a, b - a);
}

static void unquote(std::string& s) {
    if (s.size() >= 2 && (s.front() == '"' || s.front() == '\'') && s.back() == s.front()) {
        s = s.substr(1, s.size() - 2);
    }
}

static bool load_mml_env_vars(const std::string& path, std::vector<std::string>& out) {
    std::ifstream f(path);
    if (!f.is_open()) return false;
    std::string line;
    int found = 0;
    while (std::getline(f, line)) {
        std::string raw = line;
        size_t start = 0;
        while (start < raw.size() && (raw[start] == ' ' || raw[start] == '\t')) ++start;
        if (start == raw.size()) continue;
        if (raw[start] == '#') continue;
        if (raw.compare(start, 4, "MML_") != 0) continue;
        size_t colon = raw.find(':', start);
        if (colon == std::string::npos) continue;
        std::string key = raw.substr(start, colon - start);
        size_t v = colon + 1;
        while (v < raw.size() && (raw[v] == ' ' || raw[v] == '\t')) ++v;
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

static bool yaml_scalar_at(const std::string& line, const std::string& key, std::string& value) {
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

static size_t leading_spaces(const std::string& s) {
    size_t n = 0;
    while (n < s.size() && s[n] == ' ') ++n;
    return n;
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

static int load_yaml_int(const std::string& path, const std::string& section,
                         const std::string& key, int def) {
    std::string v = load_yaml_scalar(path, section, key, "");
    if (v.empty()) return def;
    char* endp = nullptr;
    long parsed = std::strtol(v.c_str(), &endp, 10);
    return endp == v.c_str() ? def : static_cast<int>(parsed);
}

// Parse the symbols list from a YAML file.
// Expects the structure:
//   symbols:
//     - symbol: FOO
//       exchange: BAR
struct SymbolConfig {
    std::string symbol;
    std::string exchange;
};

static std::vector<SymbolConfig> load_symbols(const std::string& path) {
    std::vector<SymbolConfig> result;
    std::ifstream f(path);
    if (!f.is_open()) return result;

    std::string line;
    bool in_symbols = false;
    SymbolConfig current;
    bool has_symbol = false;
    bool has_exchange = false;

    while (std::getline(f, line)) {
        std::string stripped = line;
        trim(stripped);
        if (stripped.empty() || stripped[0] == '#') continue;

        size_t indent = leading_spaces(line);

        if (!in_symbols) {
            if (stripped == "symbols:") {
                in_symbols = true;
            }
            continue;
        }

        // A top-level key at indent 0 ends the symbols block.
        if (indent == 0 && !stripped.empty() && stripped[0] != '-' && stripped.back() == ':') {
            if (has_symbol) {
                if (!has_exchange) current.exchange = "CME";
                result.push_back(current);
            }
            break;
        }

        // New list entry.
        if (stripped[0] == '-') {
            if (has_symbol) {
                if (!has_exchange) current.exchange = "CME";
                result.push_back(current);
            }
            current = {};
            has_symbol   = false;
            has_exchange = false;
            // The rest of the line after '-' might have "symbol: FOO"
            std::string rest = stripped.substr(1);
            trim(rest);
            if (!rest.empty()) {
                std::string val;
                if (yaml_scalar_at(rest, "symbol", val)) {
                    current.symbol = val;
                    has_symbol = true;
                } else if (yaml_scalar_at(rest, "exchange", val)) {
                    current.exchange = val;
                    has_exchange = true;
                }
            }
            continue;
        }

        // Key inside a list entry.
        std::string val;
        if (yaml_scalar_at(stripped, "symbol", val)) {
            current.symbol = val;
            has_symbol = true;
        } else if (yaml_scalar_at(stripped, "exchange", val)) {
            current.exchange = val;
            has_exchange = true;
        }
    }

    if (has_symbol) {
        if (!has_exchange) current.exchange = "CME";
        result.push_back(current);
    }
    return result;
}

// ---------------------------------------------------------------------------
// Root config (auto-roll) — parsed from the `roots:` YAML list.
// ---------------------------------------------------------------------------

struct RootConfig {
    std::string root;
    std::string exchange;
    RollCycle   cycle = RollCycle::QuarterlyEquity;
};

// Parse the roots list from a YAML file.
// Expects the structure:
//   roots:
//     - root: MES
//       exchange: CME
//       cycle: quarterly_equity
static std::vector<RootConfig> load_roots(const std::string& path) {
    std::vector<RootConfig> result;
    std::ifstream f(path);
    if (!f.is_open()) return result;

    std::string line;
    bool in_roots = false;
    RootConfig current;
    bool has_root     = false;
    bool has_exchange = false;
    bool has_cycle    = false;

    while (std::getline(f, line)) {
        std::string stripped = line;
        trim(stripped);
        if (stripped.empty() || stripped[0] == '#') continue;

        size_t indent = leading_spaces(line);

        if (!in_roots) {
            if (stripped == "roots:") {
                in_roots = true;
            }
            continue;
        }

        // A top-level key at indent 0 ends the roots block.
        if (indent == 0 && !stripped.empty() && stripped[0] != '-' && stripped.back() == ':') {
            if (has_root) {
                if (!has_exchange) current.exchange = "CME";
                result.push_back(current);
            }
            break;
        }

        // New list entry.
        if (stripped[0] == '-') {
            if (has_root) {
                if (!has_exchange) current.exchange = "CME";
                result.push_back(current);
            }
            current  = {};
            has_root     = false;
            has_exchange = false;
            has_cycle    = false;
            // The rest of the line after '-' might have an inline key.
            std::string rest = stripped.substr(1);
            trim(rest);
            if (!rest.empty()) {
                std::string val;
                if (yaml_scalar_at(rest, "root", val)) {
                    current.root = val;
                    has_root = true;
                } else if (yaml_scalar_at(rest, "exchange", val)) {
                    current.exchange = val;
                    has_exchange = true;
                } else if (yaml_scalar_at(rest, "cycle", val)) {
                    if (parse_roll_cycle(val, current.cycle)) has_cycle = true;
                }
            }
            continue;
        }

        // Key inside a list entry.
        std::string val;
        if (yaml_scalar_at(stripped, "root", val)) {
            current.root = val;
            has_root = true;
        } else if (yaml_scalar_at(stripped, "exchange", val)) {
            current.exchange = val;
            has_exchange = true;
        } else if (yaml_scalar_at(stripped, "cycle", val)) {
            if (parse_roll_cycle(val, current.cycle)) has_cycle = true;
        }
    }

    if (has_root) {
        if (!has_exchange) current.exchange = "CME";
        result.push_back(current);
    }

    (void)has_cycle;  // silences unused-variable warning; default already set in RootConfig
    return result;
}

// ---------------------------------------------------------------------------
// Per-symbol capture state
// ---------------------------------------------------------------------------

struct SymbolState {
    std::string symbol;
    std::string exchange;
    uint16_t    symbol_id    = 0xFFFF;

    // Roll metadata.
    bool        active      = true;   // false once rolled off; file is closed
    bool        is_explicit = false;  // explicit-symbols entry; never auto-rolled
    std::string root;                 // empty for explicit-symbol entries

    // Current capture file.
    FILE*       fp           = nullptr;
    std::string file_path;
    char        trade_date[12] = {};   // current trade date "YYYY-MM-DD"

    // Statistics for the current session.
    uint64_t    records      = 0;
    uint64_t    trades       = 0;
    uint64_t    quotes       = 0;
    uint64_t    first_ts_exch_ns = 0;
    uint64_t    last_ts_exch_ns  = 0;
    uint64_t    file_bytes   = 0;

    // Reconnect counter (persists across rotations within process lifetime).
    uint64_t    reconnects   = 0;
    uint64_t    md_gap_flags = 0;   // incremented each time adapter's md_data_gap fires
    uint64_t    md_drops_snapshot = 0;  // snapshot at start of session

    // Manifest rewrite timing.
    uint64_t    last_manifest_mono_ns = 0;
};

// Ensure the output directory for a symbol exists.
static bool ensure_dir(const std::string& dir) {
    std::error_code ec;
    std::filesystem::create_directories(dir, ec);
    return !ec;
}

// Build the file path for a symbol+date.
static std::string make_file_path(const std::string& output_root,
                                  const std::string& symbol,
                                  const char trade_date[12]) {
    return output_root + "/" + symbol + "/" + symbol + "_" + std::string(trade_date) + ".cap";
}

// Build the manifest path.
static std::string make_manifest_path(const std::string& output_root,
                                      const std::string& symbol,
                                      const char trade_date[12]) {
    return output_root + "/" + symbol + "/" + symbol + "_" + std::string(trade_date)
           + ".manifest.json";
}

// Open a capture file for writing (append if it exists from a prior run today;
// write a fresh header if the file is new).
static bool open_capture_file(SymbolState& st,
                              const std::string& output_root,
                              const char new_trade_date[12],
                              uint64_t wall_ns,
                              uint64_t mono_ns) {
    std::string dir = output_root + "/" + st.symbol;
    if (!ensure_dir(dir)) {
        log_eventf("ERROR mkdir %s", dir.c_str());
        return false;
    }

    std::memcpy(st.trade_date, new_trade_date, 12);
    st.file_path = make_file_path(output_root, st.symbol, st.trade_date);

    // Open in append+binary mode.  If the file is new (size == 0), write header.
    st.fp = std::fopen(st.file_path.c_str(), "ab");
    if (!st.fp) {
        log_eventf("ERROR fopen %s errno=%d", st.file_path.c_str(), errno);
        return false;
    }

    // Check whether the file is new (empty).
    long sz = 0;
    if (std::fseek(st.fp, 0, SEEK_END) == 0) {
        sz = std::ftell(st.fp);
    }

    if (sz == 0) {
        // Write file header.
        CaptureFileHeader hdr{};
        std::memcpy(hdr.magic, "HFT3CAP1", 8);
        hdr.version        = 1;
        hdr.record_size    = sizeof(CaptureRecord);
        hdr.wall_ns_at_open = wall_ns;
        hdr.mono_ns_at_open = mono_ns;

        size_t sym_len = st.symbol.size() < sizeof(hdr.symbol) ? st.symbol.size() : sizeof(hdr.symbol) - 1;
        std::memcpy(hdr.symbol, st.symbol.c_str(), sym_len);

        size_t ex_len = st.exchange.size() < sizeof(hdr.exchange) ? st.exchange.size() : sizeof(hdr.exchange) - 1;
        std::memcpy(hdr.exchange, st.exchange.c_str(), ex_len);

        std::memcpy(hdr.trade_date, st.trade_date, 12);

        if (std::fwrite(&hdr, sizeof(hdr), 1, st.fp) != 1) {
            log_eventf("ERROR write header %s", st.file_path.c_str());
            std::fclose(st.fp);
            st.fp = nullptr;
            return false;
        }
        sz = static_cast<long>(sizeof(hdr));
    }

    st.file_bytes = static_cast<uint64_t>(sz);

    // Reset per-session statistics.
    st.records = 0;
    st.trades  = 0;
    st.quotes  = 0;
    st.first_ts_exch_ns = 0;
    st.last_ts_exch_ns  = 0;

    log_eventf("OPEN symbol=%s exchange=%s date=%s file=%s",
               st.symbol.c_str(), st.exchange.c_str(),
               st.trade_date, st.file_path.c_str());
    return true;
}

// Close the current file: flush, fdatasync, then fclose.
static void close_capture_file(SymbolState& st) {
    if (!st.fp) return;
    std::fflush(st.fp);
#if !defined(_WIN32)
    fdatasync(fileno(st.fp));
#endif
    std::fclose(st.fp);
    st.fp = nullptr;
    log_eventf("CLOSE symbol=%s date=%s records=%llu",
               st.symbol.c_str(), st.trade_date,
               static_cast<unsigned long long>(st.records));
}

// Write the manifest JSON for a symbol.
// unknown_symbol_drops is the process-lifetime count of events dropped because
// their symbol_id was not in the id_to_state map or their state was inactive.
static void write_manifest(const SymbolState& st,
                           const std::string& output_root,
                           uint64_t md_drops_total,
                           uint64_t wall_ns_now,
                           uint64_t unknown_symbol_drops) {
    std::string path = make_manifest_path(output_root, st.symbol, st.trade_date);
    // Write to a temp file then rename for atomic update.
    std::string tmp = path + ".tmp";
    FILE* f = std::fopen(tmp.c_str(), "w");
    if (!f) return;

    std::fprintf(f,
        "{\n"
        "  \"symbol\": \"%s\",\n"
        "  \"exchange\": \"%s\",\n"
        "  \"trade_date\": \"%s\",\n"
        "  \"records\": %llu,\n"
        "  \"trades\": %llu,\n"
        "  \"quotes\": %llu,\n"
        "  \"first_ts_exch_ns\": %llu,\n"
        "  \"last_ts_exch_ns\": %llu,\n"
        "  \"max_queue_gap_flag_count\": %llu,\n"
        "  \"md_drops_total\": %llu,\n"
        "  \"reconnects\": %llu,\n"
        "  \"file_bytes\": %llu,\n"
        "  \"unknown_symbol_drops\": %llu,\n"
        "  \"updated_wall_ns\": %llu\n"
        "}\n",
        json_escape(st.symbol).c_str(),
        json_escape(st.exchange).c_str(),
        json_escape(std::string(st.trade_date)).c_str(),
        static_cast<unsigned long long>(st.records),
        static_cast<unsigned long long>(st.trades),
        static_cast<unsigned long long>(st.quotes),
        static_cast<unsigned long long>(st.first_ts_exch_ns),
        static_cast<unsigned long long>(st.last_ts_exch_ns),
        static_cast<unsigned long long>(st.md_gap_flags),
        static_cast<unsigned long long>(md_drops_total),
        static_cast<unsigned long long>(st.reconnects),
        static_cast<unsigned long long>(st.file_bytes),
        static_cast<unsigned long long>(unknown_symbol_drops),
        static_cast<unsigned long long>(wall_ns_now)
    );
    std::fclose(f);
#if !defined(_WIN32)
    ::rename(tmp.c_str(), path.c_str());
#else
    std::rename(tmp.c_str(), path.c_str());
#endif
}

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

struct CaptureConfig {
    std::string            output_root;
    int                    flush_interval_sec    = 5;
    int                    fsync_interval_sec    = 30;
    int                    manifest_interval_sec = 60;
    int                    stale_threshold_sec   = 180;

    // Explicit per-contract overrides (optional; never auto-rolled).
    std::vector<SymbolConfig> symbols;

    // Auto-roll root specifications.
    std::vector<RootConfig> roots;
    int                    contracts_per_root    = 2;

    // Rithmic connection.
    hft::ConnectionConfig  conn;
};

static const char* repo_root_path() {
    const char* env = std::getenv("HFT3_REPO_DIR");
    return env ? env : "/root/hft3/repo";
}

static std::string default_rithmic_config(const std::string& repo) {
    const std::string profile = get_env_or_string("RITHMIC_ENDPOINT_PROFILE", "production");
    if (profile == "paper" || profile == "paper_chicago") {
        return repo + "/packages/data_system/config/rithmic_api_paper.yaml";
    }
    return repo + "/packages/data_system/config/rithmic_api_paper.yaml";
}

// Load CaptureConfig from the YAML path given on the command line.
// Rithmic credentials come from env vars; the YAML provides MML_* params
// and the connection-point names (same pattern as rithmic_latency_probe).
static bool load_config(const std::string& yaml_path, CaptureConfig& out) {
    const char* user = get_env_or("RITHMIC_USERNAME", "");
    const char* pass = get_env_or("RITHMIC_PASSWORD", "");
    if (!user[0] || !pass[0]) {
        std::fprintf(stderr, "FAIL: RITHMIC_USERNAME and RITHMIC_PASSWORD must be set\n");
        return false;
    }

    std::string repo = repo_root_path();

    // Load MML_* vars from the Rithmic endpoint YAML.
    std::string rithmic_yaml = get_env_or_string(
        "RITHMIC_CONFIG_PATH",
        default_rithmic_config(repo)
    );

    std::vector<std::string> env_vars;
    if (!load_mml_env_vars(rithmic_yaml, env_vars)) {
        // Also try loading from the capture yaml itself (allows self-contained configs).
        if (!load_mml_env_vars(yaml_path, env_vars)) {
            std::fprintf(stderr,
                "FAIL: no MML_* env vars found in %s or %s\n",
                rithmic_yaml.c_str(), yaml_path.c_str());
            return false;
        }
    }

    std::string ssl_path = repo + "/rithmic_gateway/RApiPlus/13.7.0.0/etc/rithmic_ssl_cert_auth_params";
    env_vars.push_back("MML_SSL_CLNT_AUTH_FILE=" + ssl_path);

    hft::ConnectionConfig& cfg = out.conn;
    // The capture YAML's explicit `system` wins over the generic RITHMIC_ENVIRONMENT
    // env var: the systemd unit loads /root/hft3/.env (EnvironmentFile), whose
    // RITHMIC_ENVIRONMENT points at the Test system, while capture runs against the
    // Paper/Chicago cluster.  Env var is only a fallback for configs without `system`.
    cfg.environment = load_yaml_scalar(
        yaml_path, "", "system",
        get_env_or_string("RITHMIC_ENVIRONMENT", "Rithmic Paper Trading")
    );
    cfg.username    = user;
    cfg.password    = pass;
    cfg.app_name    = load_yaml_scalar(yaml_path, "engine_params", "app_name", "HFT3-CaptureD");
    cfg.app_version = load_yaml_scalar(yaml_path, "engine_params", "app_version", "1.0");
    cfg.log_file_path = repo + "/runtime/capture_daemon.log";
    cfg.rep_connect_point = get_env_or_string(
        "RITHMIC_REP_CONNECT_POINT",
        load_yaml_scalar(yaml_path, "repository_login", "sCnnctPt", "login_agent_repositoryc")
    );
    cfg.md_connect_point = get_env_or_string(
        "RITHMIC_MD_CONNECT_POINT",
        load_yaml_scalar(yaml_path, "login_params", "sMdCnnctPt", "login_agent_tp_agg_cme_eqc")
    );
    cfg.ts_connect_point = get_env_or_string(
        "RITHMIC_TS_CONNECT_POINT",
        load_yaml_scalar(yaml_path, "login_params", "sTsCnnctPt", "login_agent_op_cme_eqc")
    );
    cfg.pnl_connect_point = get_env_or_string(
        "RITHMIC_PNL_CONNECT_POINT",
        load_yaml_scalar(yaml_path, "login_params", "sPnlCnnctPt", "login_agent_pnl_cme_eqc")
    );
    cfg.ih_connect_point = get_env_or_string(
        "RITHMIC_IH_CONNECT_POINT",
        load_yaml_scalar(yaml_path, "login_params", "sIhCnnctPt", "login_agent_history_cme_eqc")
    );
    cfg.env_vars = env_vars;

    // Capture-specific config.
    out.output_root = get_env_or_string(
        "CAPTURE_OUTPUT_ROOT",
        load_yaml_scalar(yaml_path, "", "output_root", "/root/hft3/data/capture")
    );
    out.flush_interval_sec    = load_yaml_int(yaml_path, "", "flush_interval_sec",    5);
    out.fsync_interval_sec    = load_yaml_int(yaml_path, "", "fsync_interval_sec",    30);
    out.manifest_interval_sec = load_yaml_int(yaml_path, "", "manifest_interval_sec", 60);
    out.stale_threshold_sec   = get_env_int_or(
        "CAPTURE_STALE_THRESHOLD_SEC",
        load_yaml_int(yaml_path, "", "stale_threshold_sec", 180)
    );

    // Explicit symbol overrides (optional).
    out.symbols = load_symbols(yaml_path);

    // Auto-roll root specifications.
    out.roots             = load_roots(yaml_path);
    out.contracts_per_root = load_yaml_int(yaml_path, "", "contracts_per_root", 2);

    if (out.symbols.empty() && out.roots.empty()) {
        std::fprintf(stderr,
            "FAIL: config %s has neither `symbols:` nor `roots:` entries\n",
            yaml_path.c_str());
        return false;
    }

    return true;
}

// ---------------------------------------------------------------------------
// compute_desired — derive the full desired symbol set for a given CT date.
//
// Returns one SymbolConfig per desired contract.  The caller uses this to
// reconcile against the current active SymbolState set at each daily rotation.
// ---------------------------------------------------------------------------

// Extended SymbolConfig carrying roll-origin information for state construction.
struct DesiredSymbol {
    std::string symbol;
    std::string exchange;
    bool        is_explicit = false;  // true for entries from the explicit symbols: list
    std::string root;                 // empty for explicit entries
};

static std::vector<DesiredSymbol> compute_desired(const CaptureConfig& cfg,
                                                  const CtDateTime& ct) {
    std::vector<DesiredSymbol> result;

    // Explicit symbols always appear in the desired set.
    for (const auto& sc : cfg.symbols) {
        DesiredSymbol ds;
        ds.symbol      = sc.symbol;
        ds.exchange    = sc.exchange;
        ds.is_explicit = true;
        result.push_back(std::move(ds));
    }

    // Derive contracts from each root using the calendar.
    for (const auto& rc : cfg.roots) {
        std::vector<std::string> codes = eligible_contracts(
            rc.root, rc.cycle,
            ct.year, ct.month, ct.mday,
            cfg.contracts_per_root
        );
        for (auto& code : codes) {
            // Deduplicate: an explicit entry with the same symbol wins.
            bool already = false;
            for (const auto& ex : result) {
                if (ex.symbol == code) { already = true; break; }
            }
            if (!already) {
                DesiredSymbol ds;
                ds.symbol      = std::move(code);
                ds.exchange    = rc.exchange;
                ds.is_explicit = false;
                ds.root        = rc.root;
                result.push_back(std::move(ds));
            }
        }
    }

    return result;
}

// ---------------------------------------------------------------------------
// Adapter lifecycle helpers
// ---------------------------------------------------------------------------

struct AdapterBundle {
    std::unique_ptr<hft::SPSCQueue<hft::MarketDataEvent, 8192>> mbo_queue;
    std::unique_ptr<hft::SPSCQueue<hft::OrderEvent, 8192>>      order_queue;
    std::unique_ptr<hft::RithmicAdapter>                        adapter;
};

// Maximum number of simultaneously-subscribed symbols.  The Rithmic adapter
// registry assigns contiguous ids starting at 0, so this is both the slot
// count and the upper bound on symbol_id values we ever expect to see.
static constexpr int kMaxSymbols = 16;

// Rebuild the id → SymbolState* dispatch table.
// Entries outside [0, kMaxSymbols) or for inactive states are set to nullptr.
static void rebuild_id_map(std::deque<SymbolState>& states,
                           SymbolState* id_to_state[kMaxSymbols]) {
    for (int i = 0; i < kMaxSymbols; ++i) id_to_state[i] = nullptr;
    for (auto& st : states) {
        if (!st.active) continue;
        if (st.symbol_id < kMaxSymbols) {
            id_to_state[st.symbol_id] = &st;
        }
    }
}

// Build and connect a fresh adapter, subscribe all active states.
// Returns nullptr on unrecoverable login failure (caller should exit nonzero).
// Returns nullptr with recoverable=true if connect/subscribe failed but retry
// is warranted.
//
// adapter_subscribe_count is set to the number of successful subscribe_mbo
// calls so callers can track registry utilization.
static std::unique_ptr<AdapterBundle> make_adapter(
    const CaptureConfig& config,
    std::deque<SymbolState>& states,
    SymbolState* id_to_state[kMaxSymbols],
    int& adapter_subscribe_count,
    bool& unrecoverable)
{
    unrecoverable = false;
    auto bundle = std::make_unique<AdapterBundle>();
    bundle->mbo_queue   = std::make_unique<hft::SPSCQueue<hft::MarketDataEvent, 8192>>();
    bundle->order_queue = std::make_unique<hft::SPSCQueue<hft::OrderEvent, 8192>>();
    bundle->adapter     = std::make_unique<hft::RithmicAdapter>(
        config.conn,
        bundle->mbo_queue.get(),
        bundle->order_queue.get()
    );

    hft::RithmicAdapter& adapter = *bundle->adapter;

    if (!adapter.initialize()) {
        log_event("ERROR initialize() failed — unrecoverable");
        unrecoverable = true;
        return nullptr;
    }

    log_event("CONNECT attempting");
    if (!adapter.connect()) {
        log_eventf("ERROR connect() failed: %s", adapter.last_connect_error());
        // Login failures with bad credentials are unrecoverable.
        const char* err = adapter.last_connect_error();
        if (err && (std::strstr(err, "auth") || std::strstr(err, "credential")
                 || std::strstr(err, "login"))) {
            unrecoverable = true;
        }
        return nullptr;
    }
    log_eventf("CONNECT ok env_key=%s", adapter.cached_env_key());

    adapter_subscribe_count = 0;
    for (auto& st : states) {
        if (!st.active) continue;
        if (!adapter.subscribe_mbo(st.symbol, st.exchange)) {
            log_eventf("WARN subscribe_mbo failed symbol=%s exchange=%s",
                       st.symbol.c_str(), st.exchange.c_str());
            // Non-fatal: continue with remaining symbols.
        } else {
            ++adapter_subscribe_count;
            st.symbol_id = adapter.lookup_symbol_id(
                st.symbol.c_str(), static_cast<int>(st.symbol.size()));
            log_eventf("SUBSCRIBE symbol=%s exchange=%s symbol_id=%u",
                       st.symbol.c_str(), st.exchange.c_str(),
                       static_cast<unsigned>(st.symbol_id));
        }
    }

    rebuild_id_map(states, id_to_state);
    return bundle;
}

// ---------------------------------------------------------------------------
// Main consumer loop
// ---------------------------------------------------------------------------

static int run_capture(const CaptureConfig& config) {
    // Derive the initial desired symbol set from the current CT date.
    CtDateTime initial_ct = utc_to_ct(wall_now_sec());
    std::vector<DesiredSymbol> desired = compute_desired(config, initial_ct);

    // SymbolState lives in a std::deque so that push_back never invalidates
    // existing pointers (used by id_to_state).  We still use indices for
    // iteration but all pointer-taking code goes through the deque reference.
    std::deque<SymbolState> states;
    for (const auto& ds : desired) {
        SymbolState st;
        st.symbol      = ds.symbol;
        st.exchange    = ds.exchange;
        st.is_explicit = ds.is_explicit;
        st.root        = ds.root;
        st.active      = true;
        states.push_back(std::move(st));
    }

    // id → SymbolState* dispatch table (rebuilt after every subscription round).
    SymbolState* id_to_state[kMaxSymbols] = {};

    // Process-lifetime counter for events whose symbol_id is not in the map
    // or whose mapped state is inactive.
    uint64_t unknown_symbol_drops = 0;

    // Tracks how many subscribe_mbo calls have been made in the current adapter
    // lifetime.  Reset to the subscription count each time make_adapter runs.
    int adapter_subscribe_count = 0;

    // Initial connect.
    bool unrecoverable = false;
    auto bundle = make_adapter(config, states, id_to_state,
                               adapter_subscribe_count, unrecoverable);
    if (!bundle) {
        if (unrecoverable) {
            log_event("FATAL unrecoverable login failure");
            return 1;
        }
        log_event("WARN initial connect failed; will retry");
        // Fall through — reconnect loop will handle it.
    }

    // Timing state.
    uint64_t last_flush_mono_ns   = mono_now_ns();
    uint64_t last_fsync_mono_ns   = mono_now_ns();
    uint64_t last_event_mono_ns   = mono_now_ns();
    uint64_t flush_interval_ns    = static_cast<uint64_t>(config.flush_interval_sec)    * 1000000000ULL;
    uint64_t fsync_interval_ns    = static_cast<uint64_t>(config.fsync_interval_sec)    * 1000000000ULL;
    uint64_t manifest_interval_ns = static_cast<uint64_t>(config.manifest_interval_sec) * 1000000000ULL;
    uint64_t stale_threshold_ns   = static_cast<uint64_t>(config.stale_threshold_sec)   * 1000000000ULL;

    // Open initial files for today's trade date.
    char current_td[12] = {};
    cme_trade_date(wall_now_sec(), current_td);
    uint64_t last_rotation_check_mono_ns = mono_now_ns();
    {
        uint64_t w = wall_now_ns();
        uint64_t m = mono_now_ns();
        for (auto& st : states) {
            if (st.active) {
                open_capture_file(st, config.output_root, current_td, w, m);
            }
        }
    }

    // ---------------------------------------------------------------------------
    // Main loop
    // ---------------------------------------------------------------------------
    while (!g_shutdown.load(std::memory_order_relaxed)) {
        // Reconnect if we have no adapter.
        if (!bundle) {
            log_event("RECONNECT sleeping 5s before retry");
            for (int i = 0; i < 50 && !g_shutdown.load(std::memory_order_relaxed); ++i) {
                std::this_thread::sleep_for(std::chrono::milliseconds(100));
            }
            if (g_shutdown.load(std::memory_order_relaxed)) break;

            bundle = make_adapter(config, states, id_to_state,
                                  adapter_subscribe_count, unrecoverable);
            if (!bundle) {
                if (unrecoverable) {
                    log_event("FATAL unrecoverable login failure during reconnect");
                    goto shutdown;
                }
                for (auto& st : states) { if (st.active) ++st.reconnects; }
                continue;
            }
            last_event_mono_ns = mono_now_ns();
            for (auto& st : states) { if (st.active) ++st.reconnects; }
        }

        hft::RithmicAdapter& adapter = *bundle->adapter;

        // Drain the SPSC queue in a tight loop (up to a batch limit to allow
        // periodic maintenance to run even under heavy load).
        static constexpr int kBatchLimit = 4096;
        int drained = 0;
        hft::MarketDataEvent ev;
        while (drained < kBatchLimit && bundle->mbo_queue->pop(ev)) {
            ++drained;
            last_event_mono_ns = mono_now_ns();

            // Dispatch via the id_to_state lookup table.
            SymbolState* st = nullptr;
            if (ev.symbol_id < kMaxSymbols) {
                st = id_to_state[ev.symbol_id];
            }

            // Drop events from unknown, unmapped, or inactive states.
            if (!st || !st->active || !st->fp) {
                ++unknown_symbol_drops;
                continue;
            }

            // Build and write the record.
            CaptureRecord rec{};
            rec.ts_exch_ns      = ev.timestamp_ns;
            rec.ts_recv_mono_ns = ev.callback_monotonic_ns;
            rec.order_id        = ev.order_id;
            rec.price           = ev.price;
            rec.size            = ev.size;
            rec.symbol_id       = ev.symbol_id;
            rec.action          = ev.action;
            rec.side            = ev.side;

            if (std::fwrite(&rec, sizeof(rec), 1, st->fp) == 1) {
                ++st->records;
                st->file_bytes += sizeof(rec);
                if (ev.action == 'T') {
                    ++st->trades;
                } else {
                    ++st->quotes;
                }
                if (st->first_ts_exch_ns == 0) st->first_ts_exch_ns = ev.timestamp_ns;
                st->last_ts_exch_ns = ev.timestamp_ns;
            } else {
                log_eventf("ERROR fwrite symbol=%s errno=%d", st->symbol.c_str(), errno);
            }
        }

        uint64_t now_mono    = mono_now_ns();
        uint64_t now_wall_ns = wall_now_ns();

        // Trade-date rotation check: at most once per second.
        if (now_mono - last_rotation_check_mono_ns >= 1000000000ULL) {
            last_rotation_check_mono_ns = now_mono;
            char new_td[12] = {};
            cme_trade_date(static_cast<time_t>(now_wall_ns / 1000000000ULL), new_td);
            if (std::memcmp(new_td, current_td, 10) != 0) {
                // --- Roll logic begins here ---
                //
                // 1. Write final manifests and close files for all currently
                //    active states (we will reopen them with the new trade date
                //    below, unless a state is being rolled off).
                uint64_t md_drops_now = bundle ? adapter.md_drops() : 0;
                for (auto& st : states) {
                    if (!st.active || !st.fp) continue;
                    write_manifest(st, config.output_root, md_drops_now,
                                   now_wall_ns, unknown_symbol_drops);
                    close_capture_file(st);
                }

                // 2. Recompute the desired symbol set for the new trade date.
                time_t new_wall_sec = static_cast<time_t>(now_wall_ns / 1000000000ULL);
                CtDateTime new_ct   = utc_to_ct(new_wall_sec);
                std::vector<DesiredSymbol> new_desired = compute_desired(config, new_ct);

                // 3. Mark active non-explicit states that are no longer in the
                //    desired set as inactive (ROLL_DROP).
                for (auto& st : states) {
                    if (!st.active || st.is_explicit) continue;
                    bool still_wanted = false;
                    for (const auto& ds : new_desired) {
                        if (ds.symbol == st.symbol) { still_wanted = true; break; }
                    }
                    if (!still_wanted) {
                        st.active = false;
                        log_eventf("ROLL_DROP symbol=%s", st.symbol.c_str());
                    }
                }

                // 4. Identify newly desired symbols that have no active state.
                //    Count how many we need to add.
                std::vector<const DesiredSymbol*> to_add;
                for (const auto& ds : new_desired) {
                    bool have = false;
                    for (const auto& st : states) {
                        if (st.active && st.symbol == ds.symbol) { have = true; break; }
                    }
                    if (!have) to_add.push_back(&ds);
                }

                // 5. Check registry capacity.  If adding all new symbols would
                //    exceed kMaxSymbols, the safe path is a clean restart.
                if (adapter_subscribe_count + static_cast<int>(to_add.size()) > kMaxSymbols) {
                    log_eventf(
                        "ROLL_RESTART registry exhausted (subscribed=%d, want_add=%zu, limit=%d)"
                        " — clean exit for systemd relaunch",
                        adapter_subscribe_count,
                        to_add.size(),
                        kMaxSymbols
                    );
                    // Flush and fsync everything open, write manifests, then exit cleanly
                    // so systemd restarts us with a fresh adapter registry.
                    for (auto& st : states) {
                        if (!st.fp) continue;
                        std::fflush(st.fp);
#if !defined(_WIN32)
                        fdatasync(fileno(st.fp));
#endif
                        write_manifest(st, config.output_root, md_drops_now,
                                       now_wall_ns, unknown_symbol_drops);
                        close_capture_file(st);
                    }
                    if (bundle) bundle->adapter->disconnect();
                    log_event("SHUTDOWN complete");
                    return 0;
                }

                // 6. Subscribe and open files for new symbols.  push_back into
                //    the deque before rebuilding the id map (std::deque never
                //    invalidates existing references on push_back).
                for (const DesiredSymbol* ds : to_add) {
                    SymbolState new_st;
                    new_st.symbol      = ds->symbol;
                    new_st.exchange    = ds->exchange;
                    new_st.is_explicit = ds->is_explicit;
                    new_st.root        = ds->root;
                    new_st.active      = true;
                    states.push_back(std::move(new_st));

                    SymbolState& st = states.back();

                    if (!adapter.subscribe_mbo(st.symbol, st.exchange)) {
                        log_eventf("WARN ROLL_ADD subscribe_mbo failed symbol=%s",
                                   st.symbol.c_str());
                    } else {
                        ++adapter_subscribe_count;
                        st.symbol_id = adapter.lookup_symbol_id(
                            st.symbol.c_str(), static_cast<int>(st.symbol.size()));
                        open_capture_file(st, config.output_root, new_td, now_wall_ns, now_mono);
                        log_eventf("ROLL_ADD symbol=%s symbol_id=%u",
                                   st.symbol.c_str(),
                                   static_cast<unsigned>(st.symbol_id));
                    }
                }

                // 7. Reopen files for states that survived the roll.
                for (auto& st : states) {
                    if (!st.active || st.fp) continue;  // skip inactive or already opened
                    open_capture_file(st, config.output_root, new_td, now_wall_ns, now_mono);
                    log_eventf("ROTATE symbol=%s new_date=%s", st.symbol.c_str(), new_td);
                }

                // 8. Rebuild the dispatch table after all push_backs are done.
                rebuild_id_map(states, id_to_state);
                std::memcpy(current_td, new_td, 12);
            }
        }

        // Periodic flush.
        if (now_mono - last_flush_mono_ns >= flush_interval_ns) {
            for (auto& st : states) {
                if (st.active && st.fp) std::fflush(st.fp);
            }
            last_flush_mono_ns = now_mono;
        }

        // Periodic fdatasync.
        if (now_mono - last_fsync_mono_ns >= fsync_interval_ns) {
            for (auto& st : states) {
                if (st.active && st.fp) {
#if !defined(_WIN32)
                    fdatasync(fileno(st.fp));
#endif
                }
            }
            last_fsync_mono_ns = now_mono;
        }

        // Periodic manifest rewrite.
        for (auto& st : states) {
            if (!st.active || !st.fp) continue;
            if (now_mono - st.last_manifest_mono_ns >= manifest_interval_ns) {
                write_manifest(st, config.output_root,
                               bundle ? adapter.md_drops() : 0,
                               now_wall_ns,
                               unknown_symbol_drops);
                st.last_manifest_mono_ns = now_mono;
            }
        }

        // Staleness / data-gap check.
        if (bundle) {
            // md_data_gap means the SPSC queue dropped events during a burst.
            // Reconnecting here would lose more data; instead, record the gap
            // and continue capturing — the staleness check below remains the
            // only reconnect trigger.
            if (adapter.md_data_gap()) {
                for (auto& st : states) { if (st.active) ++st.md_gap_flags; }
                adapter.clear_md_data_gap();
                log_eventf("WARN md_data_gap fired; gap_count=%llu; continuing capture",
                    static_cast<unsigned long long>(states.empty() ? 0 : states[0].md_gap_flags));
            }

            // Staleness check during expected-active hours.
            time_t now_sec = static_cast<time_t>(now_wall_ns / 1000000000ULL);
            CtDateTime ct  = utc_to_ct(now_sec);
            if (in_active_trading_window(ct)) {
                if (now_mono - last_event_mono_ns > stale_threshold_ns) {
                    log_eventf("WARN stale: no MD for >%ds during active hours",
                               config.stale_threshold_sec);
                    log_event("RECONNECT initiating");
                    adapter.disconnect();
                    bundle.reset();
                    // Loop continues; reconnect logic at top will re-enter.
                    continue;
                }
            }
        }

        // If queue was empty, yield briefly to avoid busy-spinning.
        if (drained == 0) {
            std::this_thread::sleep_for(std::chrono::microseconds(100));
        }
    }

shutdown:
    log_event("SHUTDOWN draining queue");

    // Drain remaining events.
    if (bundle) {
        hft::MarketDataEvent ev;
        while (bundle->mbo_queue->pop(ev)) {
            SymbolState* st = nullptr;
            if (ev.symbol_id < kMaxSymbols) {
                st = id_to_state[ev.symbol_id];
            }
            if (!st || !st->active || !st->fp) {
                ++unknown_symbol_drops;
                continue;
            }
            CaptureRecord rec{};
            rec.ts_exch_ns      = ev.timestamp_ns;
            rec.ts_recv_mono_ns = ev.callback_monotonic_ns;
            rec.order_id        = ev.order_id;
            rec.price           = ev.price;
            rec.size            = ev.size;
            rec.symbol_id       = ev.symbol_id;
            rec.action          = ev.action;
            rec.side            = ev.side;
            if (std::fwrite(&rec, sizeof(rec), 1, st->fp) == 1) {
                ++st->records;
                st->file_bytes += sizeof(rec);
                if (ev.action == 'T') ++st->trades; else ++st->quotes;
                if (st->first_ts_exch_ns == 0) st->first_ts_exch_ns = ev.timestamp_ns;
                st->last_ts_exch_ns = ev.timestamp_ns;
            }
        }
        bundle->adapter->disconnect();
    }

    // Final flush, fsync, manifests.
    uint64_t final_wall_ns = wall_now_ns();
    for (auto& st : states) {
        if (!st.active) continue;
        if (st.fp) {
            std::fflush(st.fp);
#if !defined(_WIN32)
            fdatasync(fileno(st.fp));
#endif
        }
        write_manifest(st, config.output_root,
                       bundle ? bundle->adapter->md_drops() : 0,
                       final_wall_ns,
                       unknown_symbol_drops);
        close_capture_file(st);
    }

    log_event("SHUTDOWN complete");
    return 0;
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

int main(int argc, char** argv) {
    std::setvbuf(stderr, nullptr, _IOLBF, 0);

    if (argc < 2) {
        std::fprintf(stderr,
            "Usage: capture_daemon <config.yaml>\n"
            "  Reads Rithmic credentials from RITHMIC_USERNAME / RITHMIC_PASSWORD env vars.\n"
            "  Never logs credential values.\n");
        return 2;
    }

    install_signal_handlers();

    const std::string yaml_path = argv[1];
    log_eventf("STARTUP config=%s", yaml_path.c_str());

    CaptureConfig config;
    if (!load_config(yaml_path, config)) {
        return 1;
    }

    log_eventf("CONFIG output_root=%s explicit_symbols=%zu roots=%zu"
               " contracts_per_root=%d flush=%ds fsync=%ds manifest=%ds stale=%ds",
               config.output_root.c_str(),
               config.symbols.size(),
               config.roots.size(),
               config.contracts_per_root,
               config.flush_interval_sec,
               config.fsync_interval_sec,
               config.manifest_interval_sec,
               config.stale_threshold_sec);

    // Log explicit symbols (not credentials).
    for (const auto& sc : config.symbols) {
        log_eventf("CONFIG explicit_symbol=%s exchange=%s",
                   sc.symbol.c_str(), sc.exchange.c_str());
    }
    // Log roots (not credentials).
    for (const auto& rc : config.roots) {
        log_eventf("CONFIG root=%s exchange=%s cycle=%s",
                   rc.root.c_str(), rc.exchange.c_str(), roll_cycle_name(rc.cycle));
    }
    log_eventf("CONFIG rithmic_env=%s app=%s (credentials: names only, never values)",
               config.conn.environment.c_str(),
               config.conn.app_name.c_str());

    return run_capture(config);
}
