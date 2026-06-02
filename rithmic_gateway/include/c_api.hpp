#ifndef HFT_RITHMIC_C_API_H
#define HFT_RITHMIC_C_API_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct ConnectionConfig {
    const char* environment;
    const char* username;
    const char* password;
    const char* app_name;
    const char* app_version;
    const char* ssl_cert_path;
    const char* log_file_path;
    const char* md_connect_point;
    const char* ts_connect_point;
    const char* rep_connect_point;
    const char* pnl_connect_point;
    const char* ih_connect_point;
    const char** env_vars;
    int env_vars_count;
} ConnectionConfig;

typedef struct MarketDataEvent {
    uint64_t timestamp_ns;
    uint64_t order_id;
    char action;
    char side;
    double price;
    int32_t size;
} MarketDataEvent;

typedef struct OrderEvent {
    uint64_t timestamp_ns;
    uint64_t order_id;
    char event_type;
    char side;
    char order_type;
    double price;
    int32_t size;
    int32_t filled_size;
    int32_t total_filled;
    int32_t total_unfilled;
} OrderEvent;

void* hft_rithmic_adapter_create(const ConnectionConfig* cfg);
int   hft_rithmic_adapter_initialize(void* handle);
int   hft_rithmic_adapter_connect(void* handle);
void  hft_rithmic_adapter_disconnect(void* handle);
void  hft_rithmic_adapter_destroy(void* handle);
int   hft_rithmic_adapter_subscribe_mbo(void* handle, const char* symbol, const char* exchange);
int   hft_rithmic_adapter_send_order(void* handle, const char* symbol, char side, int32_t qty, double price);
int   hft_rithmic_adapter_cancel_order(void* handle, const char* order_id);
int   hft_rithmic_adapter_try_pop_event(void* handle, MarketDataEvent* out_event);
int   hft_rithmic_adapter_try_pop_order_event(void* handle, OrderEvent* out_event);
const char* hft_rithmic_adapter_last_error(void* handle);
const char* hft_rithmic_adapter_get_env_key(void* handle);
const char* hft_rithmic_adapter_get_account_id(void* handle);
const char* hft_rithmic_adapter_get_trade_route(void* handle);
int         hft_rithmic_adapter_is_connected(void* handle);

#ifdef __cplusplus
}
#endif

#endif
