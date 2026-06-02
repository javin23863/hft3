#include "rithmic_adapter.hpp"
#include "spsc_queue.hpp"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>
#include <thread>

static const char* get_env_or(const char* key, const char* def) {
    const char* v = std::getenv(key);
    return v ? v : def;
}

static const char* repo_root() {
    const char* env = std::getenv("HFT3_REPO_DIR");
    if (env) return env;
    // fallback: probe runs as build/rithmic_gateway/rithmic_latency_probe
    return "/root/hft3/repo";
}

int main(int argc, char** argv) {
    (void)argc; (void)argv;

    const char* user = get_env_or("RITHMIC_USERNAME", "");
    const char* pass = get_env_or("RITHMIC_PASSWORD", "");
    if (!user[0] || !pass[0]) {
        std::fprintf(stderr, "FAIL: Set RITHMIC_USERNAME and RITHMIC_PASSWORD\n");
        return 1;
    }

    bool use_test = get_env_or("RITHMIC_PROBE_ENV", "test")[0] == 't';

    std::vector<std::string> env_vars;
    if (use_test) {
        env_vars = {
            "MML_DMN_SRVR_ADDR=rituz00100.00.rithmic.com:65000~rituz00100.00.rithmic.net:65000~rituz00100.00.theomne.net:65000~rituz00100.00.theomne.com:65000",
            "MML_DOMAIN_NAME=rithmic_uat_dmz_domain",
            "MML_LIC_SRVR_ADDR=rituz00100.00.rithmic.com:56000~rituz00100.00.rithmic.net:56000~rituz00100.00.theomne.net:56000~rituz00100.00.theomne.com:56000",
            "MML_LOC_BROK_ADDR=rituz00100.00.rithmic.com:64100",
            "MML_LOGGER_ADDR=rituz00100.00.rithmic.com:45454~rituz00100.00.rithmic.net:45454~rituz00100.00.theomne.net:45454~rituz00100.00.theomne.com:45454",
            "MML_LOG_TYPE=log_net",
        };
    } else {
        env_vars = {
            "MML_DMN_SRVR_ADDR=ritpz04063.04.rithmic.com:65000",
            "MML_DOMAIN_NAME=rithmic_paper_domain",
            "MML_LIC_SRVR_ADDR=ritpz04063.04.rithmic.com:56000",
            "MML_LOC_BROK_ADDR=ritpz04063.04.rithmic.com:64100",
            "MML_LOGGER_ADDR=ritpz04063.04.rithmic.com:45454",
            "MML_LOG_TYPE=log_net",
        };
    }

    std::string repo = repo_root();
    std::string ssl_path = repo + "/rithmic_gateway/RApiPlus/13.7.0.0/etc/rithmic_ssl_cert_auth_params";
    env_vars.push_back("MML_SSL_CLNT_AUTH_FILE=" + ssl_path);

    hft::ConnectionConfig cfg;
    cfg.environment = use_test ? "Rithmic Test" : "Rithmic Paper Trading";
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
    const char* env_name = use_test ? "test" : "paper";

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

    // --- Phase 3: Subscribe MBO ---
    const char* symbol = get_env_or("RITHMIC_PROBE_SYMBOL", "MES");
    const char* exchange = get_env_or("RITHMIC_PROBE_EXCHANGE", "CME");

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

    // --- Phase 4: Send order ---
    auto t_ord = std::chrono::steady_clock::now();
    bool order_sent = adapter.send_order(symbol, 'B', 1, 0.0);
    if (!order_sent) {
        std::fprintf(stderr, "WARN [%s] send_order rejected\n", env_name);
    } else {
        auto t_poll = std::chrono::steady_clock::now();
        bool got_ack = false;
        while (std::chrono::duration_cast<std::chrono::milliseconds>(
                   std::chrono::steady_clock::now() - t_poll).count() < 10000) {
            hft::OrderEvent ev;
            if (order_queue.pop(ev)) {
                long long submit_ack_us = std::chrono::duration_cast<std::chrono::microseconds>(
                    std::chrono::steady_clock::now() - t_ord).count();
                std::printf("OK [%s] order_event type=%c id=%llu side=%c price=%.2f"
                            "  (submit=ack=%.3f ms)\n",
                            env_name, ev.event_type,
                            (unsigned long long)ev.order_id,
                            ev.side, ev.price,
                            submit_ack_us / 1000.0);
                got_ack = true;
                break;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        if (!got_ack) {
            std::fprintf(stderr, "WARN [%s] no order ack within 10s\n", env_name);
        }
    }

    adapter.disconnect();
    std::printf("OK [%s] probe complete\n", env_name);
    return 0;
}
