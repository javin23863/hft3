// engine/src/engine_config.cpp
//
// load_config — parse a simple key=value text file into EngineConfig.
// Keys are case-sensitive; unknown keys are silently ignored.
// Returns false on any I/O error; caller must abort (§8 step 1 fail-closed).

#include "engine_config.hpp"
#include <fstream>
#include <sstream>
#include <stdexcept>

namespace hft {
namespace engine {

bool load_config(const std::string& path, EngineConfig& out, std::string& err_out) {
    std::ifstream f(path);
    if (!f.is_open()) {
        err_out = "Cannot open config file: " + path;
        return false;
    }

    std::string line;
    int line_no = 0;
    while (std::getline(f, line)) {
        ++line_no;
        // Strip comments and trailing whitespace.
        const auto comment = line.find('#');
        if (comment != std::string::npos) line = line.substr(0, comment);
        while (!line.empty() && (line.back() == ' ' || line.back() == '\r' || line.back() == '\t'))
            line.pop_back();
        if (line.empty()) continue;

        const auto eq = line.find('=');
        if (eq == std::string::npos) continue;

        const std::string key   = line.substr(0, eq);
        const std::string value = line.substr(eq + 1);

        try {
            if      (key == "mode") {
                if      (value == "REPLAY") out.mode = EngineMode::REPLAY;
                else if (value == "PAPER")  out.mode = EngineMode::PAPER;
                else if (value == "LIVE")   out.mode = EngineMode::LIVE;
                else { err_out = "Unknown mode: " + value; return false; }
            }
            else if (key == "weights_path")          out.weights_path            = value;
            else if (key == "feed_path")             out.feed_path               = value;
            else if (key == "latency_ns")            out.latency_ns              = std::stoll(value);
            else if (key == "warmup_events")         out.warmup_events           = static_cast<uint32_t>(std::stoul(value));
            else if (key == "mbo_batch_size")        out.mbo_batch_size          = static_cast<uint32_t>(std::stoul(value));
            else if (key == "max_position")          out.max_position            = static_cast<int32_t>(std::stoi(value));
            else if (key == "daily_loss_limit")      out.daily_loss_limit        = std::stod(value);
            else if (key == "max_order_rate_per_sec") out.max_order_rate_per_sec = static_cast<uint32_t>(std::stoul(value));
            else if (key == "max_cancel_rate_per_sec") out.max_cancel_rate_per_sec = static_cast<uint32_t>(std::stoul(value));
            else if (key == "LIVE_MAX_ORDER_SIZE")   out.live_max_order_size     = std::stoi(value);
            else if (key == "LIVE_DAILY_LOSS_LIMIT") out.live_daily_loss_limit   = std::stod(value);
            else if (key == "LIVE_KILL_SWITCH")      out.live_kill_switch        = (value == "1" || value == "true");
            else if (key == "LIVE_RISK_ENABLED")     out.live_risk_enabled       = (value == "1" || value == "true");
            else if (key == "log_ring_capacity")     out.log_ring_capacity       = static_cast<uint32_t>(std::stoul(value));
            // Unknown keys silently ignored.
        } catch (const std::exception& e) {
            err_out = "Parse error at line " + std::to_string(line_no) + " key=" + key + ": " + e.what();
            return false;
        }
    }
    return true;
}

} // namespace engine
} // namespace hft
