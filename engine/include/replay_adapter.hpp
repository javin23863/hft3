#pragma once
// engine/include/replay_adapter.hpp
//
// ReplayAdapter — AdapterT implementation for REPLAY mode (CHI404_RUNTIME.md §10).
//
// Flat binary feed format (little-endian, packed):
//   Per record (33 bytes):
//     int64_t  ts_ns     — exchange timestamp nanoseconds
//     uint64_t order_id  — order identifier
//     int8_t   action    — 'A'=add, 'C'=cancel, 'M'=modify, 'T'=trade
//     int8_t   side      — 'B'=bid, 'A'=ask
//     double   price     — limit price (IEEE-754 little-endian)
//     double   size      — quantity (IEEE-754 little-endian)
//
// Total: 8 + 8 + 1 + 1 + 8 + 8 = 34 bytes per record (but side+action packed 2B,
// price/size 8B each = 8+8+1+1+8+8 = 34 bytes; we use 34-byte aligned records).
//
// Note: the struct is laid out as 34 bytes but the serialiser writes it as:
//   i64 ts_ns (8), u64 order_id (8), i8 action (1), i8 side (1), f64 price (8), f64 size (8)
// Total on-disk: 34 bytes per record (no padding in the flat binary).
//
// Synthetic ack (§ architecture, REPLAY fidelity):
//   Order submissions are pushed as OrderEvent 'A' (ack) onto the order_queue_
//   with ack_ts = submit_ts + latency_ns (configurable), exercising the loop's
//   drain-order-queue-first path.
//
// Safety flags:
//   All 6 flag accessors are stubbed as const false / 0.
//   Drop counter accessors return 0; no-op clear methods provided.
//   This is the "Forbidden (assert_replay_safe)" path per §10.
//
// submit_hook:
//   send_prepared_limit_order equivalent — pushes a synthetic ack onto the
//   order_queue_ and increments the submit_counter_; does not wire to exchange.

#include <atomic>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>

#include "spsc_queue.hpp"
#include "rithmic_adapter.hpp"  // for MarketDataEvent, OrderEvent types

namespace hft {
namespace engine {

// On-disk record (34 bytes, no padding, little-endian).
#pragma pack(push, 1)
struct FeedRecord {
    int64_t  ts_ns;
    uint64_t order_id;
    int8_t   action;   // 'A','C','M','T'
    int8_t   side;     // 'B','A'
    double   price;
    double   size;
};
#pragma pack(pop)
static_assert(sizeof(FeedRecord) == 34, "FeedRecord must be 34 bytes");

class ReplayAdapter {
public:
    // Construct with references to the queues (owned by EngineLoop), a path to
    // the flat binary feed, and the synthetic ack latency in nanoseconds.
    explicit ReplayAdapter(
        SPSCQueue<MarketDataEvent, 8192>* mbo_queue,
        SPSCQueue<OrderEvent, 8192>*      order_queue,
        const std::string&                feed_path,
        int64_t                           latency_ns = 500)
        : mbo_queue_(mbo_queue)
        , order_queue_(order_queue)
        , feed_path_(feed_path)
        , latency_ns_(latency_ns)
        , submit_counter_(0)
    {}

    // Load all feed records from the flat binary file into an internal vector.
    // Returns number of records loaded, or -1 on file error.
    // Call refill_queue() to stream records into the mbo_queue as the loop runs.
    int64_t load_feed() {
        std::ifstream f(feed_path_, std::ios::binary);
        if (!f.is_open()) return -1;

        f.seekg(0, std::ios::end);
        const int64_t file_bytes = static_cast<int64_t>(f.tellg());
        f.seekg(0, std::ios::beg);

        constexpr int64_t REC_SIZE = static_cast<int64_t>(sizeof(FeedRecord));
        if (file_bytes < 0 || file_bytes % REC_SIZE != 0) return -1;
        const int64_t n_records = file_bytes / REC_SIZE;

        feed_records_.resize(static_cast<size_t>(n_records));
        f.read(reinterpret_cast<char*>(feed_records_.data()),
               n_records * REC_SIZE);
        if (!f && !f.eof()) {
            feed_records_.clear();
            return -1;
        }
        feed_cursor_ = 0;

        // Prime the queue with the first batch.
        refill_queue();
        return n_records;
    }

    // Push more records from the feed vector into mbo_queue_.
    // Call at the start of each loop iteration to keep the queue fed.
    // Returns true if there are still records remaining.
    bool refill_queue() noexcept {
        if (!mbo_queue_ || feed_cursor_ >= static_cast<int64_t>(feed_records_.size())) {
            return false;
        }
        const int64_t n = static_cast<int64_t>(feed_records_.size());
        while (feed_cursor_ < n) {
            const FeedRecord& rec = feed_records_[static_cast<size_t>(feed_cursor_)];
            MarketDataEvent ev{};
            ev.timestamp_ns          = static_cast<uint64_t>(rec.ts_ns);
            ev.callback_monotonic_ns = static_cast<uint64_t>(rec.ts_ns);
            ev.order_id              = rec.order_id;
            ev.action                = static_cast<char>(rec.action);
            ev.side                  = static_cast<char>(rec.side);
            ev.price                 = rec.price;
            ev.size                  = static_cast<int32_t>(rec.size);

            if (!mbo_queue_->push(ev)) {
                break; // queue full; try again next iteration
            }
            ++feed_cursor_;
        }
        return feed_cursor_ < n;
    }

    bool feed_exhausted() const noexcept {
        return feed_cursor_ >= static_cast<int64_t>(feed_records_.size());
    }

    int64_t feed_record_count() const noexcept {
        return static_cast<int64_t>(feed_records_.size());
    }

    // Rebind queue pointers after construction (used when the owning EngineLoop
    // is created after the adapter).  Call before load_feed() or run().
    void rebind_queues(SPSCQueue<MarketDataEvent, 8192>* mbo,
                       SPSCQueue<OrderEvent, 8192>*      order) noexcept {
        mbo_queue_   = mbo;
        order_queue_ = order;
    }

    // submit_hook — called by submit_gate().
    // Pushes a synthetic ack onto the order_queue_ (ack_ts = submit_ts + latency_ns).
    // This is the ONLY place an order submission happens in REPLAY mode.
    bool send_prepared_limit_order(void* /*prepared*/, char side,
                                   int32_t qty, double price,
                                   const char* /*user_msg*/ = nullptr) {
        // Record submission timestamp.
        const int64_t submit_ts = std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch()).count();

        // Synthetic ack on the order queue.
        OrderEvent ack{};
        ack.timestamp_ns          = static_cast<uint64_t>(submit_ts + latency_ns_);
        ack.callback_monotonic_ns = ack.timestamp_ns;
        ack.callback_wall_ns      = ack.timestamp_ns;
        ack.order_id              = static_cast<uint64_t>(submit_counter_.load(std::memory_order_relaxed) + 1);
        ack.event_type            = 'A'; // ack
        ack.side                  = side;
        ack.order_type            = 'L'; // limit
        ack.price                 = price;
        ack.size                  = qty;
        ack.filled_size           = 0;
        ack.total_filled          = 0;
        ack.total_unfilled        = qty;
        std::memset(ack.user_msg, 0, sizeof(ack.user_msg));
        std::memset(ack.tag, 0, sizeof(ack.tag));

        order_queue_->push(ack);
        submit_counter_.fetch_add(1, std::memory_order_relaxed);
        return true;
    }

    // --- Safety flag accessors (stub: all safe, no real exchange) ---
    uint64_t md_drops()            const noexcept { return 0; }
    uint64_t order_event_drops()   const noexcept { return 0; }
    bool     md_data_gap()         const noexcept { return false; }
    bool     order_halt()          const noexcept { return false; }
    bool     position_desync()     const noexcept { return false; }
    bool     order_desync()        const noexcept { return false; }
    bool     auto_liquidate_halt() const noexcept { return false; }
    int      adm_alert_severity()  const noexcept { return 0; }

    // --- Flag clear stubs (no-op) ---
    void clear_md_data_gap()         noexcept {}
    void clear_order_halt()          noexcept {}
    void clear_position_desync()     noexcept {}
    void clear_order_desync()        noexcept {}
    void clear_auto_liquidate_halt() noexcept {}
    void clear_adm_alert_severity()  noexcept {}

    // --- Stats ---
    int64_t submit_count() const noexcept {
        return submit_counter_.load(std::memory_order_relaxed);
    }

private:
    SPSCQueue<MarketDataEvent, 8192>* mbo_queue_;
    SPSCQueue<OrderEvent, 8192>*      order_queue_;
    std::string                        feed_path_;
    int64_t                            latency_ns_;
    std::atomic<int64_t>               submit_counter_;
    std::vector<FeedRecord>            feed_records_;  // all records in memory
    int64_t                            feed_cursor_{0}; // next record to inject
};

} // namespace engine
} // namespace hft
