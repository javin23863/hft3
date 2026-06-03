#include "c_api.hpp"

#include <cstring>
#include <string>
#include <vector>
#include <new>

#include "rithmic_adapter.hpp"
#include "spsc_queue.hpp"

namespace {

struct AdapterEntry {
    hft::RithmicAdapter* adapter;
    std::string last_error;
    hft::SPSCQueue<hft::MarketDataEvent, 8192> queue;
    hft::SPSCQueue<hft::OrderEvent, 8192> order_queue;
    std::vector<std::string> env_storage;
};

inline AdapterEntry* as_entry(void* handle) {
    return reinterpret_cast<AdapterEntry*>(handle);
}

void set_error(AdapterEntry* e, const std::string& msg) {
    if (e) e->last_error = msg;
}

void populate_env_storage(AdapterEntry* e, const ConnectionConfig* cfg) {
    e->env_storage.clear();
    if (!cfg->env_vars || cfg->env_vars_count <= 0) return;
    e->env_storage.reserve(static_cast<size_t>(cfg->env_vars_count));
    for (int i = 0; i < cfg->env_vars_count; ++i) {
        if (cfg->env_vars[i]) e->env_storage.emplace_back(cfg->env_vars[i]);
    }
}

hft::ConnectionConfig build_cpp_config(const ConnectionConfig* cfg, const std::vector<std::string>& env_storage) {
    hft::ConnectionConfig out;
    if (cfg->environment)    out.environment    = cfg->environment;
    if (cfg->username)       out.username       = cfg->username;
    if (cfg->password)       out.password       = cfg->password;
    if (cfg->app_name)       out.app_name       = cfg->app_name;
    if (cfg->app_version)    out.app_version    = cfg->app_version;
    if (cfg->ssl_cert_path)  out.ssl_cert_path  = cfg->ssl_cert_path;
    if (cfg->log_file_path)  out.log_file_path  = cfg->log_file_path;
    if (cfg->md_connect_point) out.md_connect_point = cfg->md_connect_point;
    if (cfg->ts_connect_point) out.ts_connect_point = cfg->ts_connect_point;
    if (cfg->rep_connect_point) out.rep_connect_point = cfg->rep_connect_point;
    if (cfg->pnl_connect_point) out.pnl_connect_point = cfg->pnl_connect_point;
    if (cfg->ih_connect_point) out.ih_connect_point = cfg->ih_connect_point;
    out.env_vars = env_storage;
    return out;
}

} // namespace

extern "C" {

void* hft_rithmic_adapter_create(const ConnectionConfig* cfg) {
    if (!cfg) return nullptr;
    AdapterEntry* entry = nullptr;
    try {
        entry = new AdapterEntry();
    } catch (const std::bad_alloc&) {
        return nullptr;
    }
    entry->adapter = nullptr;
    try {
        populate_env_storage(entry, cfg);
        hft::ConnectionConfig cpp_cfg = build_cpp_config(cfg, entry->env_storage);
        entry->adapter = new hft::RithmicAdapter(cpp_cfg, &entry->queue, &entry->order_queue);
    } catch (...) {
        set_error(entry, "exception during adapter construction");
        delete entry->adapter;
        delete entry;
        return nullptr;
    }
    return entry;
}

int hft_rithmic_adapter_initialize(void* handle) {
    AdapterEntry* e = as_entry(handle);
    if (!e || !e->adapter) { return 1; }
    try {
        if (!e->adapter->initialize()) {
            set_error(e, "initialize() failed");
            return 2;
        }
    } catch (...) {
        set_error(e, "exception during initialize()");
        return 3;
    }
    return 0;
}

int hft_rithmic_adapter_connect(void* handle) {
    AdapterEntry* e = as_entry(handle);
    if (!e || !e->adapter) { return 1; }
    try {
        if (!e->adapter->connect()) {
            set_error(e, e->adapter->last_connect_error());
            return 2;
        }
    } catch (...) {
        set_error(e, "exception during connect()");
        return 3;
    }
    return 0;
}

void hft_rithmic_adapter_disconnect(void* handle) {
    AdapterEntry* e = as_entry(handle);
    if (!e || !e->adapter) return;
    try { e->adapter->disconnect(); } catch (...) { }
}

void hft_rithmic_adapter_destroy(void* handle) {
    AdapterEntry* e = as_entry(handle);
    if (!e) return;
    if (e->adapter) {
        try { e->adapter->disconnect(); } catch (...) { }
        delete e->adapter;
        e->adapter = nullptr;
    }
    delete e;
}

int hft_rithmic_adapter_subscribe_mbo(void* handle, const char* symbol, const char* exchange) {
    AdapterEntry* e = as_entry(handle);
    if (!e || !e->adapter) { return 1; }
    if (!symbol) { set_error(e, "symbol is null"); return 4; }
    try {
        std::string sym(symbol);
        std::string ex = exchange ? std::string(exchange) : std::string("CME");
        if (!e->adapter->subscribe_mbo(sym, ex)) {
            set_error(e, "subscribe_mbo() failed for " + sym);
            return 2;
        }
    } catch (...) {
        set_error(e, "exception during subscribe_mbo()");
        return 3;
    }
    return 0;
}

int hft_rithmic_adapter_send_order(void* handle, const char* symbol, char side, int32_t qty, double price) {
    return hft_rithmic_adapter_send_order_with_user_msg(handle, symbol, side, qty, price, nullptr);
}

int hft_rithmic_adapter_send_order_with_user_msg(void* handle, const char* symbol, char side, int32_t qty, double price, const char* user_msg) {
    AdapterEntry* e = as_entry(handle);
    if (!e || !e->adapter) { return 1; }
    if (!symbol) { set_error(e, "symbol is null"); return 4; }
    try {
        std::string sym(symbol);
        std::string msg = user_msg ? std::string(user_msg) : std::string();
        if (!e->adapter->send_order(sym, side, qty, price, msg)) {
            set_error(e, "send_order() failed for " + sym);
            return 2;
        }
    } catch (...) {
        set_error(e, "exception during send_order()");
        return 3;
    }
    return 0;
}

int hft_rithmic_adapter_cancel_order(void* handle, const char* order_id) {
    AdapterEntry* e = as_entry(handle);
    if (!e || !e->adapter) { return 1; }
    if (!order_id) { set_error(e, "order_id is null"); return 4; }
    try {
        std::string oid(order_id);
        if (!e->adapter->cancel_order(oid)) {
            set_error(e, "cancel_order() failed for " + oid);
            return 2;
        }
    } catch (...) {
        set_error(e, "exception during cancel_order()");
        return 3;
    }
    return 0;
}

int hft_rithmic_adapter_try_pop_event(void* handle, MarketDataEvent* out_event) {
    AdapterEntry* e = as_entry(handle);
    if (!e || !out_event) { return 1; }
    hft::MarketDataEvent evt;
    if (!e->queue.pop(evt)) {
        return 2;
    }
    out_event->timestamp_ns = evt.timestamp_ns;
    out_event->order_id     = evt.order_id;
    out_event->action       = evt.action;
    out_event->side         = evt.side;
    out_event->price        = evt.price;
    out_event->size         = evt.size;
    return 0;
}

int hft_rithmic_adapter_try_pop_order_event(void* handle, OrderEvent* out_event) {
    AdapterEntry* e = as_entry(handle);
    if (!e || !out_event) { return 1; }
    hft::OrderEvent evt;
    if (!e->order_queue.pop(evt)) {
        return 2;
    }
    out_event->timestamp_ns  = evt.timestamp_ns;
    out_event->callback_monotonic_ns = evt.callback_monotonic_ns;
    out_event->callback_wall_ns = evt.callback_wall_ns;
    out_event->order_id      = evt.order_id;
    out_event->event_type    = evt.event_type;
    out_event->side          = evt.side;
    out_event->order_type    = evt.order_type;
    out_event->price         = evt.price;
    out_event->size          = evt.size;
    out_event->filled_size   = evt.filled_size;
    out_event->total_filled  = evt.total_filled;
    out_event->total_unfilled = evt.total_unfilled;
    std::memcpy(out_event->user_msg, evt.user_msg, sizeof(out_event->user_msg));
    std::memcpy(out_event->tag, evt.tag, sizeof(out_event->tag));
    return 0;
}

const char* hft_rithmic_adapter_last_error(void* handle) {
    AdapterEntry* e = as_entry(handle);
    if (!e) return "invalid handle";
    return e->last_error.c_str();
}

const char* hft_rithmic_adapter_get_env_key(void* handle) {
    AdapterEntry* e = as_entry(handle);
    if (!e || !e->adapter) return "";
    return e->adapter->cached_env_key();
}

const char* hft_rithmic_adapter_get_account_id(void* handle) {
    AdapterEntry* e = as_entry(handle);
    if (!e || !e->adapter) return "";
    return e->adapter->cached_account_id();
}

const char* hft_rithmic_adapter_get_trade_route(void* handle) {
    AdapterEntry* e = as_entry(handle);
    if (!e || !e->adapter) return "";
    return e->adapter->cached_trade_route();
}

int hft_rithmic_adapter_is_connected(void* handle) {
    AdapterEntry* e = as_entry(handle);
    if (!e || !e->adapter) return 0;
    return e->adapter->has_account() ? 1 : 0;
}

} // extern "C"
