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

struct OrderEvent {
    uint64_t timestamp_ns;
    uint64_t order_id;
    char event_type;   // 'S' submit, 'A' ack, 'F' fill, 'C' cancel, 'M' modify, 'R' reject, 'X' failure
    char side;         // 'B' buy, 'A' sell, ' ' unknown
    char order_type;   // 'L' limit, 'M' market, ' ' unknown
    double price;
    int32_t size;
    int32_t filled_size;
    int32_t total_filled;
    int32_t total_unfilled;
    char user_msg[64];
    char tag[64];
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
    std::string pnl_connect_point;
    std::string ih_connect_point;

    std::vector<std::string> env_vars;
};

class RithmicAdapter {
    friend class MyCallbacks;
    friend class MyAdmCallbacks;

public:
    explicit RithmicAdapter(const ConnectionConfig& config,
                           SPSCQueue<MarketDataEvent, 8192>* mbo_queue,
                           SPSCQueue<OrderEvent, 8192>* order_queue);
    ~RithmicAdapter();

    bool initialize();
    bool connect();
    void disconnect();

    bool subscribe_mbo(const std::string& symbol, const std::string& exchange = "CME");
    bool send_order(const std::string& symbol, char side, int32_t qty, double price,
                    const std::string& user_msg = "");
    bool cancel_order(const std::string& order_id);

    bool has_account() const { return account_ready_.load(); }
    bool has_trade_route() const { return trade_route_ready_.load(); }
    const char* cached_account_id();
    const char* cached_trade_route();
    const char* cached_env_key() const { return discovered_env_key_.c_str(); }
    const char* last_connect_error() const { return last_connect_error_.c_str(); }

    bool list_agreements();
    bool has_agreement_list() const { return agreement_list_ready_.load(); }
    const char* last_agreement_list_text() const;

private:
    ConnectionConfig config_;
    SPSCQueue<MarketDataEvent, 8192>* mbo_queue_;
    SPSCQueue<OrderEvent, 8192>* order_queue_;

    void* engine_;
    void* callbacks_;
    void* adm_callbacks_;

    std::atomic<bool> connected_{false};
    std::atomic<bool> logged_in_{false};
    std::atomic<bool> account_ready_{false};
    std::atomic<bool> trade_route_ready_{false};
    std::atomic<int> md_login_status_{0};
    std::atomic<int> ts_login_status_{0};
    std::atomic<int> rep_login_status_{0};

    std::mutex login_mutex_;
    std::condition_variable login_cv_;
    std::mutex account_mutex_;
    std::condition_variable account_cv_;
    std::mutex trade_route_mutex_;
    std::condition_variable trade_route_cv_;
    std::mutex price_incr_mutex_;
    std::condition_variable price_incr_cv_;
    std::mutex env_mutex_;
    std::condition_variable env_cv_;
    std::condition_variable env_list_cv_;

    std::string account_id_;
    std::string fcm_id_;
    std::string ib_id_;
    std::string trade_route_;
    std::string discovered_env_key_;
    std::string last_connect_error_;
    std::string last_agreement_list_text_;
    mutable std::mutex agreement_mutex_;
    std::condition_variable agreement_cv_;
    std::atomic<bool> env_list_ready_{false};
    std::atomic<bool> env_ready_{false};
    std::atomic<bool> agreement_list_ready_{false};
    std::atomic<int> unaccepted_mandatory_agreements_{0};
    std::atomic<bool> price_incr_ready_{false};
    std::vector<std::string> env_storage_;
    std::vector<char*> env_strings_;

    void build_envp();
    void cleanup_envp();

    static const int LOGIN_NOT_LOGGED_IN = 0;
    static const int LOGIN_AWAITING      = 1;
    static const int LOGIN_FAILED        = 2;
    static const int LOGIN_COMPLETE      = 3;
};

} // namespace hft
