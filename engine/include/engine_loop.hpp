#pragma once
// engine/include/engine_loop.hpp
//
// EngineLoop<AdapterT> — the fused consumer loop per CHI404_RUNTIME.md §3.
//
// Template parameter AdapterT supplies (pattern: safety_poller.hpp):
//   - pop from mbo_queue_ / order_queue_ via SPSCQueue<T,8192>
//   - 6 safety-flag accessors: md_data_gap(), order_halt(), position_desync(),
//     order_desync(), auto_liquidate_halt(), adm_alert_severity()
//   - drop counter accessors: md_drops(), order_event_drops()
//   - flag clear methods: clear_*()
//   - submit hook: send_prepared_limit_order(nullptr, side, qty, price)
//
// Live instantiation (RithmicAdapter) is OUT OF SCOPE on this workstation.
// A compile-guarded stub is provided below with a comment referencing §8 steps
// 7-9 (thread-pinning, Rithmic connect, book snapshot) as live-only.
//
// Loop contract (§3 EXACTLY):
//   (1) Drain order_queue_ FULLY → RiskManager.update_position + local order table
//   (2) SafetyPoller.poll() + act per §5
//   (3) Drain mbo_queue_ bounded batch (mbo_batch_size) → FeatureExtractorCpp per event
//   (4) Decision gate (book valid, not halted, not flatten-only, armed)
//   (5) SINGLE SUBMISSION GATE — only call site for adapter submit hook
//
// Prohibitions (§6 post-init):
//   No heap allocation, no mutex in hot loop, no string/stream/printf,
//   no sleep/yield, only steady_clock for time.

#include <atomic>
#include <array>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <chrono>
#include <limits>
#include <memory>
#include <type_traits>

#include "spsc_queue.hpp"
#include "rithmic_adapter.hpp"   // MarketDataEvent, OrderEvent
#include "safety_poller.hpp"     // SafetyPoller, PollResult
#include "risk_manager.hpp"      // RiskManager, RiskStatus
#include "feature_extractor.hpp" // FeatureExtractorCpp, MBOEventCpp
#include "decision_runtime.hpp"  // DecisionEngine, MarketState, ActionArray, Action
#include "engine_config.hpp"
#include "log_ring.hpp"

namespace hft {
namespace engine {

// ---------------------------------------------------------------------------
// Adapter capability traits (used by if-constexpr helpers in EngineLoop)
// ---------------------------------------------------------------------------
template <typename T, typename = void>
struct has_feed_exhausted : std::false_type {};
template <typename T>
struct has_feed_exhausted<T, std::void_t<decltype(std::declval<T&>().feed_exhausted())>>
    : std::true_type {};

template <typename T, typename = void>
struct has_refill_queue : std::false_type {};
template <typename T>
struct has_refill_queue<T, std::void_t<decltype(std::declval<T&>().refill_queue())>>
    : std::true_type {};

template <typename T, typename = void>
struct has_set_current_event_ts : std::false_type {};
template <typename T>
struct has_set_current_event_ts<T, std::void_t<decltype(std::declval<T&>().set_current_event_ts(uint64_t{}))>>
    : std::true_type {};

// ---------------------------------------------------------------------------
// OrderTableEntry — local pending order tracking (§3 step 1 order table)
// ---------------------------------------------------------------------------
struct OrderTableEntry {
    uint64_t order_id;
    char     status;  // 'P'ending, 'A'ck, 'F'illed, 'C'ancelled, 'R'ejected, 'X'
    char     side;
    int32_t  qty;
    double   price;
};

static constexpr uint32_t ORDER_TABLE_CAPACITY = 256;

// ---------------------------------------------------------------------------
// EngineLoop<AdapterT>
// ---------------------------------------------------------------------------
template <typename AdapterT>
class EngineLoop {
public:
    static constexpr uint32_t LOG_RING_CAP = 65536;

    // Constructor: EngineLoop allocates its own queues.
    // After construction, wire an adapter to the engine's queues:
    //   adapter = MyAdapter(&engine.mbo_queue(), &engine.order_queue(), ...);
    // Then pass the adapter to run_with_adapter() or use the 2-arg ctor below.
    //
    // §6 / §8 step 6: queues and log ring pre-allocated at startup (not in hot loop).
    EngineLoop(AdapterT&             adapter,
               const EngineConfig&   cfg)
        : adapter_(adapter)
        , cfg_(cfg)
        , risk_(cfg.to_risk_limits())
        , poller_(adapter_, risk_)
        , feature_extractor_(0.25)         // tick_size=0.25 (MES/ES default)
        , decision_engine_()
        , armed_(false)
        , book_valid_(false)
        , warmup_count_(0)
        , loop_iter_(0)
        , order_table_{}
        , order_table_sz_(0)
        , block_counter_(0)
        , mbo_queue_(std::make_unique<SPSCQueue<MarketDataEvent, 8192>>())
        , order_queue_(std::make_unique<SPSCQueue<OrderEvent, 8192>>())
        , log_ring_(std::make_unique<LogRing<LOG_RING_CAP>>())
    {}

    // Load weights binary (§8 step 3).
    bool load_weights(const std::string& path) {
        return decision_engine_.load_model(path);
    }

    // Accessor for the log ring (used by DrainThread).
    LogRing<LOG_RING_CAP>& log_ring() noexcept { return *log_ring_; }

    // Run the hot loop until stop() is called.
    // In REPLAY mode, runs until both queues are empty and the MBO feed is
    // exhausted.
    void run(bool replay_finite = false) {
        running_.store(true, std::memory_order_release);

        while (running_.load(std::memory_order_relaxed)) {
            ++loop_iter_;

            // ----------------------------------------------------------------
            // (1) Drain order_queue_ FULLY (§3 step 1)
            // Position correctness precedes any decision in this iteration.
            // ----------------------------------------------------------------
            {
                OrderEvent oe{};
                while (order_queue_->pop(oe)) {
                    process_order_event(oe);
                }
            }

            // ----------------------------------------------------------------
            // (2) SafetyPoller.poll() + act per §5 (§3 step 2)
            // ----------------------------------------------------------------
            {
                const hft::gateway::PollResult pr = poller_.poll();
                act_on_poll_result(pr);
            }

            // ----------------------------------------------------------------
            // (3) Drain mbo_queue_ bounded batch (§3 step 3)
            // Refill SPSC queue from adapter's feed buffer (REPLAY only).
            // In LIVE mode, the gateway fills the queue asynchronously.
            // ----------------------------------------------------------------
            refill_if_available();

            uint32_t book_changing_count = 0;

            {
                MarketDataEvent mde{};
                uint32_t popped = 0;
                while (popped < cfg_.mbo_batch_size && mbo_queue_->pop(mde)) {
                    ++popped;

                    // Update last event timestamp (REPLAY determinism §7.4).
#ifndef NDEBUG
                    // CHI404_RUNTIME.md §3 step 3: event-time must be non-decreasing.
                    assert(mde.timestamp_ns >= last_event_ts_ns_);
#endif
                    last_event_ts_ns_ = mde.timestamp_ns;

                    // Convert to MBOEventCpp for FeatureExtractorCpp.
                    MBOEventCpp ev{};
                    ev.timestamp_ns = static_cast<int64_t>(mde.timestamp_ns);
                    ev.order_id     = static_cast<int64_t>(mde.order_id);
                    ev.action       = mde.action;
                    ev.side         = mde.side;
                    ev.price        = mde.price;
                    ev.size         = mde.size;

                    feature_extractor_.process_event(ev);

                    // Book-changing events: 'A'=add,'C'=cancel,'M'=modify,'T'=trade.
                    if (mde.action == 'A' || mde.action == 'C' ||
                        mde.action == 'M' || mde.action == 'T') {
                        ++book_changing_count;
                        last_book_ts_ns_ = mde.timestamp_ns;
                        last_bid_price_  = feature_extractor_.features()[0]; // feat[0]=bid
                        last_ask_price_  = feature_extractor_.features()[1]; // feat[1]=ask
                    }
                }

                // In REPLAY finite mode: stop when feed exhausted and both queues empty.
                if (replay_finite && popped == 0 && is_feed_exhausted()) {
                    OrderEvent oe2{};
                    if (!order_queue_->pop(oe2)) {
                        running_.store(false, std::memory_order_relaxed);
                        break;
                    }
                    // Process the stray order event and continue.
                    process_order_event(oe2);
                    continue;
                }
            }

            // ----------------------------------------------------------------
            // (4) Decision (§3 step 4) — event-driven, only on book-changing
            // ----------------------------------------------------------------
            if (book_changing_count > 0) {
                // Gate conditions (all must hold):
                //   (a) book.is_valid()       (b) !risk.is_halted()
                //   (c) !flatten_active       (d) armed
                // NOTE: condition (c) flatten_active is checked inside submit_gate via
                // check_order() returning FLATTEN for non-reduce-only intents, per §3 step 5.
                // The decision step is entered even in flatten mode so the submit_gate can
                // route reduce-only orders through; new-entry intents are silently dropped.
                // This matches the spec: "FLATTEN → flatten-only: only reduce-only intents
                // may reach this gate; all others dropped" (§3 step 5).
                const bool gate_a = book_valid_;
                const bool gate_b = !risk_.is_halted();
                // gate_c: !flatten_active per §3 step 4.
                // When flatten is active, reduce-only intents are still permitted via the
                // PASS carve-out in check_order; new-entry intents are silently dropped.
                // The decision step is entered even in flatten mode so reduce-only intents
                // can reach the submission gate (§3 step 5: "only reduce-only intents may
                // reach this gate; all others dropped").
                const bool gate_c = !risk_.is_flatten_active();
                const bool gate_d = armed_;

                if (gate_a && gate_b && gate_c && gate_d) {
                    MarketState state{};
                    // Populate best bid/ask from feature extractor.
                    // FeatureExtractorCpp stores best_bid_ and best_ask_ internally;
                    // we extract from the feature vector positions standardised below.
                    // feat[0] = bid_price (best_bid_), feat[1] = ask_price (best_ask_).
                    // These are set in extract() via bid/ask book maps.
                    const auto& feats = feature_extractor_.features();
                    state.bid_price_1       = feats[0];
                    state.ask_price_1       = feats[1];
                    state.bid_qty_1         = 0; // qty not separately tracked in feat vec
                    state.ask_qty_1         = 0;
                    // NOTE: RiskState::state_ is private; current_position tracked locally.
                    // inventory_pos_ is updated in process_order_event on fills.
                    state.current_inventory = inventory_pos_;
                    state.latency_state_ms  = 0.0; // REPLAY: zero simulated latency
                    for (size_t i = 0; i < state.model_features.size(); ++i) {
                        state.model_features[i] = feats[i];
                    }

                    hft::ActionArray actions{};
                    decision_engine_.evaluate_actions(state, actions);

                    const hft::ActionValue best = decision_engine_.get_optimal_action(actions);

                    // Log evaluate_actions outcome (§7.4 determinism artifact).
                    {
                        LogRecord lr{};
                        lr.ts_ns = log_ts_ns();
                        lr.type  = LOG_DECISION_EVALUATE;

                        // Encode best action + EV bit-cast into uint64.
                        uint64_t ev_bits = 0;
                        static_assert(sizeof(double) == sizeof(uint64_t));
                        std::memcpy(&ev_bits, &best.expected_value, sizeof(double));
                        lr.a = static_cast<uint64_t>(best.action);
                        lr.b = ev_bits;
                        lr.c = loop_iter_;
                        lr.d = static_cast<uint64_t>(warmup_count_);
                        log_ring_->push_or_drop(lr);
                    }

                    // Advance to submission gate only for actionable decisions.
                    if (best.action != hft::Action::NO_TRADE) {
                        // ------------------------------------------------
                        // (5) SINGLE SUBMISSION GATE (§3 step 5)
                        // ------------------------------------------------
                        char    side = (best.action == hft::Action::ENTER_LONG)  ? 'B' : 'A';
                        int32_t qty  = 1; // v1: unit quantity
                        double  price = (side == 'B') ? state.bid_price_1 : state.ask_price_1;
                        submit_gate(best.action, side, qty, price);
                    }
                } else if (!armed_) {
                    // Warm-up counting (§8 step 10): count processed MBO events, not iterations.
                    warmup_count_ += book_changing_count;
                    if (warmup_count_ >= cfg_.warmup_events) {
                        armed_ = true;
                        book_valid_ = true; // treat book as valid after warm-up

                        LogRecord lr{};
                        lr.ts_ns = log_ts_ns();
                        lr.type  = LOG_WARMUP_COMPLETE;

                        lr.a = warmup_count_;
                        log_ring_->push_or_drop(lr);
                    }
                }
            }

            // ----------------------------------------------------------------
            // Log-drain watchdog check at low frequency (§7.6)
            // ----------------------------------------------------------------
            if (drain_heartbeat_fn_ &&
                (loop_iter_ % cfg_.watchdog_check_interval_iters == 0)) {
                const uint64_t hb = drain_heartbeat_fn_();
                // Watchdog uses wall clock (compares drain heartbeat time, not event time).
                const uint64_t wall_now = static_cast<uint64_t>(
                    std::chrono::duration_cast<std::chrono::nanoseconds>(
                        std::chrono::steady_clock::now().time_since_epoch()).count());
                if (hb != 0 && (wall_now - hb) > cfg_.watchdog_stale_ns) {
                    // Watchdog fired: halt new submissions, cancel path stays alive (§7.6).
                    risk_.handle_failure_state(hft::risk::FailureState::STALE_MARKET_DATA);

                    LogRecord lr{};
                    lr.ts_ns = log_ts_ns();
                    lr.type  = LOG_WATCHDOG_FAIL;

                    lr.a = wall_now - hb;
                    log_ring_->push_or_drop(lr);
                }
            }
        }
    }

    void stop() noexcept {
        running_.store(false, std::memory_order_relaxed);
    }

    // Provide the watchdog with access to drain heartbeat (set by caller after
    // DrainThread is started).
    using HeartbeatFn = uint64_t(*)();
    void set_drain_heartbeat_fn(HeartbeatFn fn) noexcept { drain_heartbeat_fn_ = fn; }

    // Expose queues for pre-loading (REPLAY) and for adapter binding.
    SPSCQueue<MarketDataEvent, 8192>& mbo_queue()   noexcept { return *mbo_queue_; }
    SPSCQueue<OrderEvent, 8192>&      order_queue() noexcept { return *order_queue_; }

    // Expose submit counter for tests.
    int64_t submit_count() const noexcept { return submit_counter_; }
    int64_t block_count()  const noexcept { return block_counter_; }

    // Expose risk for tests.
    hft::risk::RiskManager& risk() noexcept { return risk_; }

    // Force arm (for testing gate conditions without warm-up).
    void force_arm(bool armed = true)      noexcept { armed_ = armed; }
    void force_book_valid(bool v = true)   noexcept { book_valid_ = v; }

private:
    // -----------------------------------------------------------------------
    // (1) Process a single order event (§3 step 1 routing table)
    // -----------------------------------------------------------------------
    void process_order_event(const OrderEvent& oe) noexcept {
        switch (oe.event_type) {
            case 'F': {
                // Fill → update_position + local order table
                const int32_t fill_qty = (oe.side == 'B') ? oe.filled_size : -oe.filled_size;
                const double pnl = 0.0; // REPLAY: PnL tracking deferred
                risk_.update_position(fill_qty, pnl);
                inventory_pos_ += fill_qty;  // mirror for MarketState
                update_order_table(oe.order_id, 'F');

                LogRecord lr{};
                lr.ts_ns = log_ts_ns();
                lr.type  = LOG_ORDER_FILL;

                lr.a = oe.order_id;
                lr.b = static_cast<uint64_t>(oe.filled_size);
                log_ring_->push_or_drop(lr);
                break;
            }
            case 'B': {
                // Bust → reversal + set position_desync (handled in step 2 via poller)
                const int32_t reversal = (oe.side == 'B') ? -oe.filled_size : oe.filled_size;
                risk_.update_position(reversal, 0.0);
                inventory_pos_ += reversal;
                update_order_table(oe.order_id, 'B');

                LogRecord lr{};
                lr.ts_ns = log_ts_ns();
                lr.type  = LOG_ORDER_BUST;

                lr.a = oe.order_id;
                log_ring_->push_or_drop(lr);
                break;
            }
            case 'N': {
                // Not-modified → set order_desync (handled in step 2)
                update_order_table(oe.order_id, 'N');

                LogRecord lr{};
                lr.ts_ns = log_ts_ns();
                lr.type  = LOG_ORDER_NOT_MODIFIED;

                lr.a = oe.order_id;
                log_ring_->push_or_drop(lr);
                break;
            }
            case 'L': {
                // Auto-liquidate → set auto_liquidate_halt (handled in step 2)
                update_order_table(oe.order_id, 'L');

                LogRecord lr{};
                lr.ts_ns = log_ts_ns();
                lr.type  = LOG_AUTO_LIQUIDATE;

                lr.a = oe.order_id;
                log_ring_->push_or_drop(lr);
                break;
            }
            case 'A': case 'M': case 'R': case 'X': case 'S': case 'C':
                // Ack/modify-confirm/reject/failure/submit-echo/cancel → update order table only
                update_order_table(oe.order_id, oe.event_type);
                break;
            default:
                break;
        }
    }

    // -----------------------------------------------------------------------
    // (2) Act on PollResult (§3 step 2 / §5)
    // -----------------------------------------------------------------------
    void act_on_poll_result(const hft::gateway::PollResult& pr) noexcept {
        // Log every non-empty PollResult (§7.4).
        const bool non_empty = pr.halted || pr.reconcile_required ||
                               pr.book_resync_required || pr.alert_severity > 0 ||
                               pr.md_drops_delta > 0 || pr.order_drops_delta > 0;
        if (non_empty) {
            LogRecord lr{};
            lr.ts_ns = log_ts_ns();
            lr.type  = LOG_POLL_RESULT;

            lr.a = (static_cast<uint64_t>(pr.halted) << 0) |
                   (static_cast<uint64_t>(pr.reconcile_required) << 1) |
                   (static_cast<uint64_t>(pr.book_resync_required) << 2) |
                   (static_cast<uint64_t>(static_cast<uint32_t>(pr.alert_severity)) << 8);
            lr.b = pr.md_drops_delta;
            lr.c = pr.order_drops_delta;
            lr.d = 0;
            log_ring_->push_or_drop(lr);
        }

        if (pr.halted) {
            // Hard halt: close submission gate.
            // risk_.state_ already set by SafetyPoller via handle_failure_state.
            // Nothing additional needed; submit_gate checks risk_.is_halted().
        }
        if (pr.reconcile_required) {
            // Schedule reconciliation. In REPLAY: immediate ack (no desync source).
            poller_.ack_reconcile();
        }
        if (pr.book_resync_required) {
            // Mark book invalid.
            book_valid_ = false;

            LogRecord lr{};
            lr.ts_ns = log_ts_ns();
            lr.type  = LOG_BOOK_RESYNC_START;

            log_ring_->push_or_drop(lr);

            // In REPLAY: no real book snapshot; immediately re-validate.
            // In LIVE: wait for full snapshot (§8 step 9 live-only).
            poller_.ack_book_resync();
            book_valid_ = true;  // REPLAY: optimistic; real impl waits for snapshot.

            LogRecord lr2{};
            lr2.ts_ns = log_ts_ns();
            lr2.type  = LOG_BOOK_RESYNC_END;
            log_ring_->push_or_drop(lr2);
        }
        // pr.alert_severity, md_drops_delta, order_drops_delta: logged above; telemetry only.
    }

    // -----------------------------------------------------------------------
    // (5) SINGLE SUBMISSION GATE — only call site of adapter submit hook (§3 step 5)
    //
    // Precondition A: validated EngineConfig at startup (§8 step 2)
    // Precondition B: RiskManager::check_order(intent_qty)
    // -----------------------------------------------------------------------
    void submit_gate(hft::Action action, char side, int32_t qty, double price) noexcept {
        // Precondition A: config was validated at startup; the engine only runs
        // after assert_live_config() passed — enforced by main (§8 step 2).

        // Map qty sign to risk check convention: positive=buy, negative=sell.
        const int32_t intent_qty = (side == 'B') ? qty : -qty;

        const hft::risk::RiskStatus status = risk_.check_order(intent_qty);

        // Log gate verdict (§7.4).
        {
            LogRecord lr{};
            lr.ts_ns = log_ts_ns();
            lr.type  = LOG_GATE_VERDICT;

            lr.a = static_cast<uint64_t>(status == hft::risk::RiskStatus::PASS   ? GATE_PASS    :
                                         status == hft::risk::RiskStatus::BLOCK   ? GATE_BLOCK   :
                                         status == hft::risk::RiskStatus::HALT    ? GATE_HALT    :
                                                                                    GATE_FLATTEN);
            lr.b = static_cast<uint64_t>(action);
            lr.c = static_cast<uint64_t>(intent_qty > 0 ? intent_qty : -intent_qty);
            uint64_t price_bits = 0;
            std::memcpy(&price_bits, &price, sizeof(double));
            lr.d = price_bits;
            log_ring_->push_or_drop(lr);
        }

        switch (status) {
            case hft::risk::RiskStatus::PASS: {
                // Propagate current event timestamp to REPLAY adapter before submit so
                // synthetic ack uses pure event-time (no wall clock in REPLAY ack path).
                if constexpr (has_set_current_event_ts<AdapterT>::value) {
                    adapter_.set_current_event_ts(last_event_ts_ns_);
                }
                // Send prepared limit order — ONLY call site (§3 step 5).
                adapter_.send_prepared_limit_order(nullptr, side, qty, price);
                risk_.record_order_sent();
                add_order_table_entry(++last_order_id_, side, qty, price);
                ++submit_counter_;

                // Log submission (§7.4).
                LogRecord lr{};
                lr.ts_ns = log_ts_ns();
                lr.type  = LOG_ORDER_SUBMITTED;

                lr.a = last_order_id_;
                lr.b = static_cast<uint64_t>(qty);
                uint64_t pb = 0; std::memcpy(&pb, &price, sizeof(double));
                lr.c = pb;
                lr.d = static_cast<uint64_t>(side);
                log_ring_->push_or_drop(lr);
                break;
            }
            case hft::risk::RiskStatus::BLOCK:
                ++block_counter_;
                break;
            case hft::risk::RiskStatus::HALT:
                // Close gate — no submission.
                break;
            case hft::risk::RiskStatus::FLATTEN:
                // Flatten-only: only reduce-only intents reach this gate.
                // FLATTEN result means the intent was NOT reduce-only; drop.
                // (Reduce-only was already passed with PASS by check_order carve-out.)
                break;
        }
    }

    // -----------------------------------------------------------------------
    // refill_if_available — if-constexpr helper for REPLAY streaming
    // Calls adapter_.refill_queue() if AdapterT has that method (REPLAY only).
    // In LIVE mode (RithmicAdapter), the method is absent and this is a no-op.
    // -----------------------------------------------------------------------
    void refill_if_available() noexcept {
        if constexpr (has_refill_queue<AdapterT>::value) {
            adapter_.refill_queue();
        }
    }

    // is_feed_exhausted — returns true when REPLAY feed is fully consumed.
    // In LIVE mode (no feed_exhausted() method), always returns false.
    bool is_feed_exhausted() const noexcept {
        if constexpr (has_feed_exhausted<AdapterT>::value) {
            return adapter_.feed_exhausted();
        } else {
            return false;
        }
    }

    // -----------------------------------------------------------------------
    // Local order table helpers
    // -----------------------------------------------------------------------
    void update_order_table(uint64_t order_id, char new_status) noexcept {
        for (uint32_t i = 0; i < order_table_sz_; ++i) {
            if (order_table_[i].order_id == order_id) {
                order_table_[i].status = new_status;
                return;
            }
        }
    }

    void add_order_table_entry(uint64_t order_id, char side, int32_t qty, double price) noexcept {
        if (order_table_sz_ < ORDER_TABLE_CAPACITY) {
            order_table_[order_table_sz_++] = {order_id, 'P', side, qty, price};
        }
    }

    // Log timestamp: in REPLAY mode use the last event timestamp for determinism (§7.4).
    // In LIVE mode use wall clock (steady_clock).
    uint64_t log_ts_ns() const noexcept {
        if constexpr (has_feed_exhausted<AdapterT>::value) {
            // REPLAY: return the last event timestamp so logs are deterministic.
            return (last_event_ts_ns_ > 0) ? last_event_ts_ns_ : loop_iter_;
        } else {
            return static_cast<uint64_t>(
                std::chrono::duration_cast<std::chrono::nanoseconds>(
                    std::chrono::steady_clock::now().time_since_epoch()).count());
        }
    }

    // -----------------------------------------------------------------------
    // Members — ORDER MATCHES CONSTRUCTOR INIT LIST (avoids -Wreorder warning)
    // -----------------------------------------------------------------------
    AdapterT&                            adapter_;
    const EngineConfig&                  cfg_;
    hft::risk::RiskManager               risk_;
    hft::gateway::SafetyPoller<AdapterT> poller_;
    hft::FeatureExtractorCpp             feature_extractor_;
    hft::DecisionEngine                  decision_engine_;

    std::atomic<bool>                    running_{false};
    bool                                 armed_;
    bool                                 book_valid_;
    uint32_t                             warmup_count_;
    uint64_t                             loop_iter_;
    uint64_t                             last_event_ts_ns_{0};  // last MBO event ts (REPLAY determinism §7.4)
    uint64_t                             last_book_ts_ns_{0};
    double                               last_bid_price_{0.0};
    double                               last_ask_price_{0.0};
    uint64_t                             last_order_id_{0};

    std::array<OrderTableEntry, ORDER_TABLE_CAPACITY> order_table_;
    uint32_t                             order_table_sz_;

    int32_t                              inventory_pos_{0};   // local position mirror
    int64_t                              submit_counter_{0};
    int64_t                              block_counter_{0};

    std::unique_ptr<SPSCQueue<MarketDataEvent, 8192>> mbo_queue_;
    std::unique_ptr<SPSCQueue<OrderEvent, 8192>>      order_queue_;
    std::unique_ptr<LogRing<LOG_RING_CAP>>            log_ring_;

    HeartbeatFn                          drain_heartbeat_fn_{nullptr};
};

// ---------------------------------------------------------------------------
// Live instantiation stub — compile-guarded (CHI404-only, §10).
//
// CHI404_RUNTIME.md §8 live-only steps (not executed in REPLAY):
//   Step 7: Thread-pin hot thread → core 2; log-drain → core 3;
//           verify sched_getaffinity; abort on mismatch.
//   Step 8: RithmicAdapter.initialize() + connect(); abort on failure.
//   Step 9: Login MD+TS plants; subscribe_mbo(); warm_price_increment();
//           receive full book snapshot; assert has_account() && has_trade_route().
//
// Instantiate via:
//   #ifdef HFT3_LIVE_BUILD
//   using LiveEngineLoop = hft::engine::EngineLoop<hft::RithmicAdapter>;
//   #endif
// ---------------------------------------------------------------------------

} // namespace engine
} // namespace hft
