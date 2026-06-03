#include "c_api.hpp"

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

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

static const char* repo_root() {
    const char* env = std::getenv("HFT3_REPO_DIR");
    return env ? env : "/root/hft3/repo";
}

static void trim(std::string& s) {
    size_t a = 0;
    while (a < s.size() && (s[a] == ' ' || s[a] == '\t' || s[a] == '\r' || s[a] == '\n')) ++a;
    size_t b = s.size();
    while (b > a && (s[b - 1] == ' ' || s[b - 1] == '\t' || s[b - 1] == '\r' || s[b - 1] == '\n')) --b;
    s = s.substr(a, b - a);
}

static void unquote(std::string& s) {
    if (s.size() >= 2 && (s.front() == '"' || s.front() == '\'')
        && s.back() == s.front()) {
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
        if (start == raw.size() || raw[start] == '#') continue;
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

static std::string fixed_cstr(const char* data, size_t len) {
    size_t n = 0;
    while (n < len && data[n] != '\0') ++n;
    return std::string(data, data + n);
}

static bool order_event_matches(const OrderEvent& ev, const std::string& user_msg,
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

static const char* last_error(void* handle) {
    const char* err = hft_rithmic_adapter_last_error(handle);
    return err ? err : "";
}

int main() {
    std::setvbuf(stdout, nullptr, _IONBF, 0);
    std::setvbuf(stderr, nullptr, _IONBF, 0);

    const char* user = get_env_or("RITHMIC_USERNAME", "");
    const char* pass = get_env_or("RITHMIC_PASSWORD", "");
    if (!user[0] || !pass[0]) {
        std::fprintf(stderr, "FAIL: Set RITHMIC_USERNAME and RITHMIC_PASSWORD\n");
        return 1;
    }

    std::string repo = repo_root();
    std::vector<std::string> env_vars;
    std::string yaml_path = repo + "/packages/data_system/config/rithmic_api_test.yaml";
    if (!load_mml_env_vars(yaml_path, env_vars)) {
        std::fprintf(stderr, "FAIL: could not load MML_* env from %s\n", yaml_path.c_str());
        return 2;
    }
    env_vars.push_back("MML_SSL_CLNT_AUTH_FILE=" + repo + "/rithmic_gateway/RApiPlus/13.7.0.0/etc/rithmic_ssl_cert_auth_params");

    std::vector<const char*> env_ptrs;
    env_ptrs.reserve(env_vars.size());
    for (const std::string& v : env_vars) env_ptrs.push_back(v.c_str());

    std::string log_path = repo + "/runtime/rithmic_capi_latency_probe.log";
    ConnectionConfig cfg{};
    cfg.environment = "Rithmic Test";
    cfg.username = user;
    cfg.password = pass;
    cfg.app_name = "HFT3-CAPI-LatencyProbe";
    cfg.app_version = "1.0";
    cfg.log_file_path = log_path.c_str();
    cfg.md_connect_point = "login_agent_tpc";
    cfg.ts_connect_point = "login_agent_opc";
    cfg.rep_connect_point = "login_agent_repositoryc";
    cfg.pnl_connect_point = "login_agent_pnlc";
    cfg.ih_connect_point = "login_agent_historyc";
    cfg.env_vars = env_ptrs.data();
    cfg.env_vars_count = static_cast<int>(env_ptrs.size());

    void* handle = hft_rithmic_adapter_create(&cfg);
    if (!handle) {
        std::fprintf(stderr, "FAIL: hft_rithmic_adapter_create returned null\n");
        return 3;
    }

    int rc = hft_rithmic_adapter_initialize(handle);
    if (rc != 0) {
        std::fprintf(stderr, "FAIL: initialize rc=%d error=%s\n", rc, last_error(handle));
        hft_rithmic_adapter_destroy(handle);
        return 4;
    }

    uint64_t connect_start = steady_now_ns();
    rc = hft_rithmic_adapter_connect(handle);
    uint64_t connect_end = steady_now_ns();
    if (rc != 0) {
        std::fprintf(stderr, "FAIL: connect rc=%d error=%s\n", rc, last_error(handle));
        hft_rithmic_adapter_destroy(handle);
        return 5;
    }
    std::printf("OK [capi] connect %.3f ms account=%s route=%s env_key=%s\n",
                static_cast<double>(connect_end - connect_start) / 1000000.0,
                hft_rithmic_adapter_get_account_id(handle),
                hft_rithmic_adapter_get_trade_route(handle),
                hft_rithmic_adapter_get_env_key(handle));

    const char* symbol = get_env_or("RITHMIC_PROBE_SYMBOL", "MESM6");
    const char* exchange = get_env_or("RITHMIC_PROBE_EXCHANGE", "CME");
    const char side = get_env_or("RITHMIC_PROBE_ORDER_SIDE", "B")[0] == 'S' ? 'S' : 'B';
    const double price = std::atof(get_env_or("RITHMIC_PROBE_ORDER_PRICE", "0"));
    const int count = get_env_int_or("RITHMIC_PROBE_ORDER_COUNT", 1);
    const int qty = get_env_int_or("RITHMIC_PROBE_ORDER_QTY", 1);
    const int timeout_ms = get_env_int_or("RITHMIC_PROBE_ORDER_TIMEOUT_MS", 10000);
    const int interval_us = get_env_int_or("RITHMIC_PROBE_ORDER_INTERVAL_US", 0);
    const bool cancel_after_ack = get_env_bool_or("RITHMIC_PROBE_CANCEL_AFTER_ACK", true);
    const bool debug_events = get_env_bool_or("RITHMIC_PROBE_DEBUG_EVENTS", false);
    if (price <= 0.0 || count <= 0 || qty <= 0) {
        std::fprintf(stderr, "FAIL: set positive RITHMIC_PROBE_ORDER_PRICE/count/qty\n");
        hft_rithmic_adapter_destroy(handle);
        return 6;
    }

    uint64_t warm_start = steady_now_ns();
    rc = hft_rithmic_adapter_warm_price_increment(handle, symbol, exchange);
    uint64_t warm_end = steady_now_ns();
    if (rc != 0) {
        std::fprintf(stderr, "FAIL: warm_price_increment rc=%d error=%s\n", rc, last_error(handle));
        hft_rithmic_adapter_destroy(handle);
        return 7;
    }

    const std::string run_id = run_id_utc();
    std::printf("OK [capi] order_burst_start run_id=%s symbol=%s exchange=%s side=%c qty=%d"
                " price=%.2f count=%d warm_price_incr=%.3f ms\n",
                run_id.c_str(), symbol, exchange, side, qty, price, count,
                static_cast<double>(warm_end - warm_start) / 1000000.0);

    OrderEvent stale{};
    while (hft_rithmic_adapter_try_pop_order_event(handle, &stale) == 0) {}

    std::vector<double> latencies_us;
    latencies_us.reserve(static_cast<size_t>(count));
    int ack_count = 0;
    int reject_count = 0;
    int failure_count = 0;
    int timeout_count = 0;
    int cancel_submit_count = 0;
    int debug_event_count = 0;

    for (int i = 0; i < count; ++i) {
        std::ostringstream tag;
        tag << "hft3capi-" << run_id << "-" << (i + 1);
        std::string user_msg = tag.str();

        uint64_t send_ns = steady_now_ns();
        rc = hft_rithmic_adapter_send_order_with_user_msg(handle, symbol, side, qty, price, user_msg.c_str());
        if (rc != 0) {
            ++failure_count;
            std::printf("ORDER_RESULT index=%d status=send_failed rc=%d error=%s\n",
                        i + 1, rc, last_error(handle));
            continue;
        }
        if (debug_events) {
            std::printf("ORDER_SENT index=%d user_msg=%s send_ns=%llu\n",
                        i + 1, user_msg.c_str(),
                        static_cast<unsigned long long>(send_ns));
        }

        uint64_t deadline_ns = send_ns + static_cast<uint64_t>(timeout_ms) * 1000000ULL;
        bool finished = false;
        while (steady_now_ns() < deadline_ns) {
            OrderEvent ev{};
            rc = hft_rithmic_adapter_try_pop_order_event(handle, &ev);
            if (rc == 2) continue;
            if (rc != 0) {
                ++failure_count;
                std::printf("ORDER_RESULT index=%d status=pop_failed rc=%d error=%s\n",
                            i + 1, rc, last_error(handle));
                finished = true;
                break;
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
            if (!order_event_matches(ev, user_msg, send_ns)) continue;

            uint64_t ack_ns = ev.callback_monotonic_ns ? ev.callback_monotonic_ns : steady_now_ns();
            double latency_us = ack_ns >= send_ns
                ? static_cast<double>(ack_ns - send_ns) / 1000.0
                : 0.0;
            if (ev.event_type == 'A') {
                ++ack_count;
                latencies_us.push_back(latency_us);
                if (cancel_after_ack && ev.order_id != 0) {
                    std::string oid = std::to_string(ev.order_id);
                    if (hft_rithmic_adapter_cancel_order(handle, oid.c_str()) == 0) {
                        ++cancel_submit_count;
                    }
                }
                std::printf("ORDER_RESULT index=%d status=ack latency_us=%.3f broker_order_id=%llu\n",
                            i + 1, latency_us,
                            static_cast<unsigned long long>(ev.order_id));
            } else if (ev.event_type == 'R') {
                ++reject_count;
                std::printf("ORDER_RESULT index=%d status=reject latency_us=%.3f broker_order_id=%llu\n",
                            i + 1, latency_us,
                            static_cast<unsigned long long>(ev.order_id));
            } else if (ev.event_type == 'X') {
                ++failure_count;
                std::printf("ORDER_RESULT index=%d status=failure latency_us=%.3f broker_order_id=%llu\n",
                            i + 1, latency_us,
                            static_cast<unsigned long long>(ev.order_id));
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
            uint64_t pause_until = steady_now_ns() + static_cast<uint64_t>(interval_us) * 1000ULL;
            while (steady_now_ns() < pause_until) {}
        }
    }

    std::sort(latencies_us.begin(), latencies_us.end());
    if (!latencies_us.empty()) {
        double sum = 0.0;
        for (double v : latencies_us) sum += v;
        std::printf("CPP_CAPI_ORDER_LATENCY_SUMMARY run_id=%s count=%zu ack=%d reject=%d"
                    " failure=%d timeout=%d cancel_submit=%d min_us=%.3f avg_us=%.3f"
                    " p50_us=%.3f p90_us=%.3f p99_us=%.3f max_us=%.3f\n",
                    run_id.c_str(), latencies_us.size(), ack_count, reject_count,
                    failure_count, timeout_count, cancel_submit_count,
                    latencies_us.front(), sum / static_cast<double>(latencies_us.size()),
                    pct_us(latencies_us, 50.0), pct_us(latencies_us, 90.0),
                    pct_us(latencies_us, 99.0), latencies_us.back());
    } else {
        std::printf("CPP_CAPI_ORDER_LATENCY_SUMMARY run_id=%s count=0 ack=%d reject=%d"
                    " failure=%d timeout=%d cancel_submit=%d\n",
                    run_id.c_str(), ack_count, reject_count, failure_count,
                    timeout_count, cancel_submit_count);
    }

    hft_rithmic_adapter_disconnect(handle);
    hft_rithmic_adapter_destroy(handle);
    return 0;
}
