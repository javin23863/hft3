#pragma once
// engine/include/log_ring.hpp
//
// Log subsystem (CHI404_RUNTIME.md §7).
//
// LogRecord — fixed-size POD: {ts_ns:u64, type:u32, a:u64, b:u64, c:u64, d:u64} = 40 bytes.
// LogRing   — pre-allocated SPSC ring; hot thread = producer; drain thread = consumer.
//             Overflow: drop counter incremented atomically, never blocks (§7.3).
// DrainThread — reads records from the ring and appends them to a binary log file.
//
// Log event types (§7.4 mandatory events):
//   POLL_RESULT          — every non-empty PollResult
//   DECISION_EVALUATE    — every evaluate_actions() call: best action + EV encoded
//   GATE_VERDICT         — every submit_gate() call: PASS/BLOCK/HALT/FLATTEN
//   ORDER_SUBMITTED      — every order sent
//   ORDER_FILL           — every fill
//   ORDER_BUST           — every bust
//   ORDER_NOT_MODIFIED   — every not-modified
//   AUTO_LIQUIDATE       — every auto-liquidate
//   BOOK_RESYNC_START    — book resync start
//   BOOK_RESYNC_END      — book resync end
//   WATCHDOG_FAIL        — log-drain watchdog fired (§7.6)
//   WARMUP_COMPLETE      — armed = true

#include <atomic>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <string>
#include <thread>

namespace hft {
namespace engine {

// ---------------------------------------------------------------------------
// LogRecord — 40-byte fixed-size POD (§7.1)
// ---------------------------------------------------------------------------
// §7.1: fixed-size POD {ts_ns:u64, event_enum:u32, 4×u64 payload}.
// Layout note: CHI404_RUNTIME.md §7.1 states 40 bytes, but the stated fields
// {u64 ts_ns, u32 type, 4×u64} with natural alignment require 48 bytes
// (4-byte padding after type before first u64 payload).  We use 48 bytes
// (6 u64 fields) and document the deviation here.  The determinism invariant
// holds: byte-identical across identical inputs at 48 bytes per record.
struct LogRecord {
    uint64_t ts_ns;  // 8 — nanosecond timestamp
    uint64_t type;   // 8 — LogEventType (u64 to avoid struct padding)
    uint64_t a;      // 8 — payload slot 0
    uint64_t b;      // 8 — payload slot 1
    uint64_t c;      // 8 — payload slot 2
    uint64_t d;      // 8 — payload slot 3
    // Total: 6×8 = 48 bytes
};
static_assert(sizeof(LogRecord) == 48, "LogRecord must be 48 bytes (see layout note above)");

enum LogEventType : uint32_t {
    LOG_POLL_RESULT       = 0,
    LOG_DECISION_EVALUATE = 1,
    LOG_GATE_VERDICT      = 2,
    LOG_ORDER_SUBMITTED   = 3,
    LOG_ORDER_FILL        = 4,
    LOG_ORDER_BUST        = 5,
    LOG_ORDER_NOT_MODIFIED= 6,
    LOG_AUTO_LIQUIDATE    = 7,
    LOG_BOOK_RESYNC_START = 8,
    LOG_BOOK_RESYNC_END   = 9,
    LOG_WATCHDOG_FAIL     = 10,
    LOG_WARMUP_COMPLETE   = 11,
};

// Gate verdict codes stored in payload field 'a' for LOG_GATE_VERDICT
enum GateVerdict : uint64_t {
    GATE_PASS    = 0,
    GATE_BLOCK   = 1,
    GATE_HALT    = 2,
    GATE_FLATTEN = 3,
};

// ---------------------------------------------------------------------------
// LogRing — lock-free SPSC ring (§7.2 / §7.3)
//
// Capacity must be power of 2.  Producer (hot thread): push_or_drop().
// Consumer (drain thread): pop().
// ---------------------------------------------------------------------------
template <uint32_t Capacity>
class LogRing {
    static_assert((Capacity & (Capacity - 1)) == 0, "Capacity must be power of 2");
public:
    LogRing() : head_(0), tail_(0), drop_counter_(0) {}

    // Hot-path producer: push or increment drop counter — never blocks (§7.3).
    void push_or_drop(const LogRecord& rec) noexcept {
        const uint32_t cur_tail = tail_.load(std::memory_order_relaxed);
        const uint32_t next_tail = (cur_tail + 1) & (Capacity - 1);
        if (next_tail == head_.load(std::memory_order_acquire)) [[unlikely]] {
            drop_counter_.fetch_add(1, std::memory_order_relaxed);
            return;
        }
        buf_[cur_tail] = rec;
        tail_.store(next_tail, std::memory_order_release);
    }

    // Drain-thread consumer.
    bool pop(LogRecord& out) noexcept {
        const uint32_t cur_head = head_.load(std::memory_order_relaxed);
        if (cur_head == tail_.load(std::memory_order_acquire)) [[unlikely]] {
            return false;
        }
        out = buf_[cur_head];
        head_.store((cur_head + 1) & (Capacity - 1), std::memory_order_release);
        return true;
    }

    uint64_t drop_count() const noexcept {
        return drop_counter_.load(std::memory_order_relaxed);
    }

private:
    LogRecord buf_[Capacity]{};
    alignas(64) std::atomic<uint32_t> head_;
    char _pad0[60]{};
    alignas(64) std::atomic<uint32_t> tail_;
    char _pad1[60]{};
    std::atomic<uint64_t> drop_counter_;
};

// ---------------------------------------------------------------------------
// DrainThread — wraps a LogRing and writes records to a binary file (§7.5).
// Also maintains drain_heartbeat_ for the watchdog (§7.6).
// ---------------------------------------------------------------------------
template <uint32_t Capacity>
class DrainThread {
public:
    DrainThread(LogRing<Capacity>& ring, const std::string& log_path)
        : ring_(ring)
        , log_path_(log_path)
        , stop_(false)
        , drain_heartbeat_(0)
    {}

    ~DrainThread() { stop(); }

    // Start the drain thread.  Returns false if already running.
    bool start() {
        if (thread_.joinable()) return false;
        out_.open(log_path_, std::ios::binary | std::ios::app);
        if (!out_.is_open()) return false;
        thread_ = std::thread([this] { run(); });
        return true;
    }

    void stop() {
        stop_.store(true, std::memory_order_relaxed);
        if (thread_.joinable()) thread_.join();
        if (out_.is_open()) {
            // Drain remaining records before close.
            LogRecord rec{};
            while (ring_.pop(rec)) {
                out_.write(reinterpret_cast<const char*>(&rec), sizeof(rec));
            }
            out_.close();
        }
    }

    // Called by hot thread to check heartbeat staleness (§7.6).
    uint64_t heartbeat_ns() const noexcept {
        return drain_heartbeat_.load(std::memory_order_relaxed);
    }

private:
    void run() {
        LogRecord rec{};
        while (!stop_.load(std::memory_order_relaxed)) {
            bool drained = false;
            while (ring_.pop(rec)) {
                out_.write(reinterpret_cast<const char*>(&rec), sizeof(rec));
                drained = true;
            }
            if (drained) {
                out_.flush();
            }
            // Update heartbeat every pass (§7.6).
            drain_heartbeat_.store(
                static_cast<uint64_t>(
                    std::chrono::duration_cast<std::chrono::nanoseconds>(
                        std::chrono::steady_clock::now().time_since_epoch()).count()),
                std::memory_order_relaxed);
            // Yield only when queue was empty to avoid busy-spin on log drain thread.
            if (!drained) {
                std::this_thread::yield();
            }
        }
    }

    LogRing<Capacity>&       ring_;
    std::string              log_path_;
    std::atomic<bool>        stop_;
    std::atomic<uint64_t>    drain_heartbeat_;
    std::ofstream            out_;
    std::thread              thread_;
};

} // namespace engine
} // namespace hft
