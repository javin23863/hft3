#pragma once

#include <string>
#include <vector>
#include <functional>
#include <cstdint>
#include <mutex>
#include <condition_variable>
#include <atomic>
#include "spsc_queue.hpp"

namespace hft {

struct MarketDataEvent {
    uint64_t timestamp_ns;
    uint64_t order_id;
    char action; // 'A' add, 'C' cancel, 'M' modify, 'T' trade
    char side;    // 'B' bid, 'A' ask
    double price;
    int32_t size;
};

struct ConnectionConfig {
    std::string environment;
    std::string username;
    std::string password;
    std::string app_name;
    std::string app_version;
    std::string ssl_cert_path;
    std::string log_file_path;

    std::string md_connect_point;
    std::string ts_connect_point;
    std::string rep_connect_point;

    std::vector<std::string> env_vars;
};

class RithmicAdapter {
public:
    explicit RithmicAdapter(const ConnectionConfig& config,
                           SPSCQueue<MarketDataEvent, 8192>* mbo_queue);
    ~RithmicAdapter();

    bool initialize();
    bool connect();
    void disconnect();

    bool subscribe_mbo(const std::string& symbol, const std::string& exchange = "CME");
    bool send_order(const std::string& symbol, char side, int32_t qty, double price);
    bool cancel_order(const std::string& order_id);

private:
    ConnectionConfig config_;
    SPSCQueue<MarketDataEvent, 8192>* mbo_queue_;

    void* engine_;
    void* callbacks_;
    void* adm_callbacks_;

    std::atomic<bool> connected_{false};
    std::atomic<bool> logged_in_{false};

    std::mutex login_mutex_;
    std::condition_variable login_cv_;
    std::atomic<int> rep_login_status_{0};
    std::atomic<int> md_login_status_{0};
    std::atomic<bool> agreements_received_{false};
    std::atomic<int> unaccepted_mandatory_agreements_{0};

    std::vector<char*> env_strings_;

    void build_envp();
    void cleanup_envp();

    static const int LOGIN_NOT_LOGGED_IN = 0;
    static const int LOGIN_AWAITING      = 1;
    static const int LOGIN_FAILED        = 2;
    static const int LOGIN_COMPLETE      = 3;
};

} // namespace hft