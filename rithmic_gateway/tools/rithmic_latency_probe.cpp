#include "rithmic_adapter.hpp"
#include "spsc_queue.hpp"

#include <chrono>
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>
#include <thread>

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

int main(int argc, char** argv) {
    (void)argc; (void)argv;
    std::setvbuf(stdout, nullptr, _IONBF, 0);
    std::setvbuf(stderr, nullptr, _IONBF, 0);

    const char* user = get_env_or("RITHMIC_USERNAME", "");
    const char* pass = get_env_or("RITHMIC_PASSWORD", "");
    if (!user[0] || !pass[0]) {
        std::fprintf(stderr, "FAIL: Set RITHMIC_USERNAME and RITHMIC_PASSWORD\n");
        return 1;
    }

    std::vector<std::string> env_vars;
    std::string yaml_path = std::string(repo_root())
        + "/packages/data_system/config/rithmic_api_test.yaml";
    if (!load_mml_env_vars(yaml_path, env_vars)) {
        std::fprintf(stderr, "WARN: could not load MML_* env from %s; using hardcoded Test defaults\n",
                     yaml_path.c_str());
        env_vars = {
            "MML_DMN_SRVR_ADDR=rituz00100.00.rithmic.com:65000~rituz00100.00.rithmic.net:65000~rituz00100.00.theomne.net:65000~rituz00100.00.theomne.com:65000",
            "MML_DOMAIN_NAME=rithmic_uat_dmz_domain",
            "MML_LIC_SRVR_ADDR=rituz00100.00.rithmic.com:56000~rituz00100.00.rithmic.net:56000~rituz00100.00.theomne.net:56000~rituz00100.00.theomne.com:56000",
            "MML_LOC_BROK_ADDR=rituz00100.00.rithmic.com:64100",
            "MML_LOGGER_ADDR=rituz00100.00.rithmic.com:45454~rituz00100.00.rithmic.net:45454~rituz00100.00.theomne.net:45454~rituz00100.00.theomne.com:45454",
            "MML_LOG_TYPE=log_net",
        };
    }

    std::string repo = repo_root();
    std::string ssl_path = repo + "/rithmic_gateway/RApiPlus/13.7.0.0/etc/rithmic_ssl_cert_auth_params";
    env_vars.push_back("MML_SSL_CLNT_AUTH_FILE=" + ssl_path);

    hft::ConnectionConfig cfg;
    cfg.environment = "Rithmic Test";
    cfg.username = user;
    cfg.password = pass;
    cfg.app_name = "HFT3-LatencyProbe";
    cfg.app_version = "1.0";
    cfg.log_file_path = repo + "/runtime/rithmic_latency_probe.log";
    cfg.rep_connect_point = "login_agent_repositoryc";
    cfg.md_connect_point = "login_agent_tpc";
    cfg.ts_connect_point = "login_agent_opc";
    cfg.pnl_connect_point = "login_agent_pnlc";
    cfg.ih_connect_point = "login_agent_historyc";
    cfg.env_vars = env_vars;

    hft::SPSCQueue<hft::MarketDataEvent, 8192> mbo_queue;
    hft::SPSCQueue<hft::OrderEvent, 8192> order_queue;

    hft::RithmicAdapter adapter(cfg, &mbo_queue, &order_queue);
    const char* env_name = "test";

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

    // --- Phase 3: Optional market-data smoke check ---
    const char* symbol = get_env_or("RITHMIC_PROBE_SYMBOL", "MESM6");
    const char* exchange = get_env_or("RITHMIC_PROBE_EXCHANGE", "CME");

    if (!get_env_bool_or("RITHMIC_PROBE_SKIP_MD", true)) {
        auto t_sub = std::chrono::steady_clock::now();
        if (!adapter.subscribe_mbo(symbol, exchange)) {
            std::fprintf(stderr, "WARN [%s] subscribe_mbo(%s) failed\n",
                         env_name, symbol);
        } else {
            auto t_poll_start = std::chrono::steady_clock::now();
            bool got_md = false;
            while (std::chrono::duration_cast<std::chrono::milliseconds>(
                       std::chrono::steady_clock::now() - t_poll_start).count() < 5000) {
                hft::MarketDataEvent ev;
                if (mbo_queue.pop(ev)) {
                    long long md_us = std::chrono::duration_cast<std::chrono::microseconds>(
                        std::chrono::steady_clock::now() - t_sub).count();
                    std::printf("OK [%s] md_event action=%c side=%c price=%.2f size=%d"
                                "  (subscribe=%.3f ms)\n",
                                env_name, ev.action, ev.side, ev.price, ev.size,
                                md_us / 1000.0);
                    got_md = true;
                    break;
                }
                std::this_thread::sleep_for(std::chrono::milliseconds(1));
            }
            if (!got_md) {
                std::printf("WARN [%s] no md event within 5s\n", env_name);
            }
        }
    }

    // --- Phase 4: Native C++ order latency burst ---
    const char* order_price_raw = get_env_or("RITHMIC_PROBE_ORDER_PRICE", "");
    double order_price = order_price_raw[0] ? std::atof(order_price_raw) : 0.0;
    if (order_price <= 0.0) {
        std::printf("WARN [%s] skipping send_order; set RITHMIC_PROBE_ORDER_PRICE"
                    " to a positive test limit price to exercise order ack\n",
                    env_name);
    } else {
        const int count = get_env_int_or("RITHMIC_PROBE_ORDER_COUNT", 1);
        const int qty = get_env_int_or("RITHMIC_PROBE_ORDER_QTY", 1);
        const int timeout_ms = get_env_int_or("RITHMIC_PROBE_ORDER_TIMEOUT_MS", 10000);
        const int interval_us = get_env_int_or("RITHMIC_PROBE_ORDER_INTERVAL_US", 0);
        const bool cancel_after_ack = get_env_bool_or("RITHMIC_PROBE_CANCEL_AFTER_ACK", true);
        const bool debug_events = get_env_bool_or("RITHMIC_PROBE_DEBUG_EVENTS", false);
        const char side = get_env_or("RITHMIC_PROBE_ORDER_SIDE", "B")[0] == 'S' ? 'S' : 'B';
        const std::string run_id = run_id_utc();
        std::vector<double> latencies_us;
        latencies_us.reserve(static_cast<size_t>(count > 0 ? count : 1));
        int ack_count = 0;
        int reject_count = 0;
        int failure_count = 0;
        int timeout_count = 0;
        int cancel_submit_count = 0;
        int debug_event_count = 0;

        hft::OrderEvent stale;
        while (order_queue.pop(stale)) {}

        auto t_warm = std::chrono::steady_clock::now();
        if (!adapter.warm_price_increment(symbol, exchange)) {
            std::fprintf(stderr, "FAIL [%s] warm_price_increment(%s/%s)\n",
                         env_name, exchange, symbol);
            adapter.disconnect();
            return 4;
        }
        auto warm_us = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - t_warm).count();

        std::printf("OK [%s] order_burst_start run_id=%s symbol=%s exchange=%s"
                    " side=%c qty=%d price=%.2f count=%d warm_price_incr=%.3f ms\n",
                    env_name, run_id.c_str(), symbol, exchange, side, qty,
                    order_price, count, warm_us / 1000.0);

        for (int i = 0; i < count; ++i) {
            std::ostringstream tag;
            tag << "hft3cpp-" << run_id << "-" << (i + 1);
            std::string user_msg = tag.str();

            const uint64_t send_ns = steady_now_ns();
            bool order_sent = adapter.send_order(symbol, side, qty, order_price, user_msg);
            if (!order_sent) {
                ++failure_count;
                std::printf("ORDER_RESULT index=%d status=send_failed\n", i + 1);
                continue;
            }
            if (debug_events) {
                std::printf("ORDER_SENT index=%d user_msg=%s send_ns=%llu\n",
                            i + 1, user_msg.c_str(),
                            static_cast<unsigned long long>(send_ns));
            }

            const uint64_t deadline_ns = send_ns + static_cast<uint64_t>(timeout_ms) * 1000000ULL;
            bool finished = false;
            while (steady_now_ns() < deadline_ns) {
                hft::OrderEvent ev;
                if (!order_queue.pop(ev)) {
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
                if (!order_event_matches(ev, user_msg, send_ns)) {
                    continue;
                }

                const uint64_t ack_ns = ev.callback_monotonic_ns ? ev.callback_monotonic_ns : steady_now_ns();
                const double latency_us = ack_ns >= send_ns
                    ? static_cast<double>(ack_ns - send_ns) / 1000.0
                    : 0.0;

                if (ev.event_type == 'A') {
                    ++ack_count;
                    latencies_us.push_back(latency_us);
                    if (cancel_after_ack && ev.order_id != 0) {
                        if (adapter.cancel_order(std::to_string(ev.order_id))) {
                            ++cancel_submit_count;
                        }
                    }
                    std::printf("ORDER_RESULT index=%d status=ack latency_us=%.3f broker_order_id=%llu\n",
                                i + 1, latency_us, static_cast<unsigned long long>(ev.order_id));
                } else if (ev.event_type == 'R') {
                    ++reject_count;
                    std::printf("ORDER_RESULT index=%d status=reject latency_us=%.3f broker_order_id=%llu\n",
                                i + 1, latency_us, static_cast<unsigned long long>(ev.order_id));
                } else if (ev.event_type == 'X') {
                    ++failure_count;
                    std::printf("ORDER_RESULT index=%d status=failure latency_us=%.3f broker_order_id=%llu\n",
                                i + 1, latency_us, static_cast<unsigned long long>(ev.order_id));
                } else {
                    continue;
                }
                finished = true;
                break;
            }

            if (!finished) {
                ++timeout_count;
                std::printf("ORDER_RESULT index=%d status=timeout\n", i + 1);
            }

            if (i + 1 < count && interval_us > 0) {
                const uint64_t pause_until = steady_now_ns() + static_cast<uint64_t>(interval_us) * 1000ULL;
                while (steady_now_ns() < pause_until) {}
            }
        }

        std::sort(latencies_us.begin(), latencies_us.end());
        if (!latencies_us.empty()) {
            std::printf("CPP_ORDER_LATENCY_SUMMARY run_id=%s count=%zu ack=%d reject=%d failure=%d"
                        " timeout=%d cancel_submit=%d min_us=%.3f avg_us=%.3f p50_us=%.3f"
                        " p90_us=%.3f p99_us=%.3f max_us=%.3f\n",
                        run_id.c_str(), latencies_us.size(), ack_count, reject_count,
                        failure_count, timeout_count, cancel_submit_count,
                        latencies_us.front(),
                        [&] {
                            double s = 0.0;
                            for (double v : latencies_us) s += v;
                            return s / static_cast<double>(latencies_us.size());
                        }(),
                        pct_us(latencies_us, 50.0),
                        pct_us(latencies_us, 90.0),
                        pct_us(latencies_us, 99.0),
                        latencies_us.back());
        } else {
            std::printf("CPP_ORDER_LATENCY_SUMMARY run_id=%s count=0 ack=%d reject=%d"
                        " failure=%d timeout=%d cancel_submit=%d\n",
                        run_id.c_str(), ack_count, reject_count, failure_count,
                        timeout_count, cancel_submit_count);
        }
    }

    adapter.disconnect();
    std::printf("OK [%s] probe complete\n", env_name);
    return 0;
}
