#include "safety_poller.hpp"

#include <atomic>
#include <cstdint>
#include <iostream>
#include <thread>

namespace {

struct AtomicFakeAdapter {
    std::atomic<bool> order_halt_flag{false};
    std::atomic<bool> auto_liquidate_flag{false};
    std::atomic<bool> position_desync_flag{false};
    std::atomic<bool> order_desync_flag{false};
    std::atomic<bool> md_data_gap_flag{false};
    std::atomic<int> adm_severity{0};
    std::atomic<uint64_t> md_drops_val{0};
    std::atomic<uint64_t> order_drops_val{0};

    bool order_halt() const noexcept { return order_halt_flag.load(std::memory_order_relaxed); }
    bool auto_liquidate_halt() const noexcept { return auto_liquidate_flag.load(std::memory_order_relaxed); }
    bool position_desync() const noexcept { return position_desync_flag.load(std::memory_order_relaxed); }
    bool order_desync() const noexcept { return order_desync_flag.load(std::memory_order_relaxed); }
    bool md_data_gap() const noexcept { return md_data_gap_flag.load(std::memory_order_relaxed); }
    int adm_alert_severity() const noexcept { return adm_severity.load(std::memory_order_relaxed); }
    uint64_t md_drops() const noexcept { return md_drops_val.load(std::memory_order_relaxed); }
    uint64_t order_event_drops() const noexcept { return order_drops_val.load(std::memory_order_relaxed); }

    void clear_position_desync() noexcept { position_desync_flag.store(false, std::memory_order_relaxed); }
    void clear_order_desync() noexcept { order_desync_flag.store(false, std::memory_order_relaxed); }
    void clear_md_data_gap() noexcept { md_data_gap_flag.store(false, std::memory_order_relaxed); }
};

constexpr int kMutations = 100000;

hft::risk::RiskLimits stress_limits() {
    hft::risk::RiskLimits limits{};
    limits.max_position = 1000000;
    limits.daily_loss_limit = -1.0e12;
    limits.max_order_rate_per_sec = 1000000;
    limits.max_cancel_rate_per_sec = 1000000;
    return limits;
}

} // namespace

int main() {
    AtomicFakeAdapter adapter;
    hft::risk::RiskManager risk(stress_limits());
    hft::gateway::SafetyPoller<AtomicFakeAdapter> poller(adapter, risk);

    std::atomic<bool> start{false};
    std::atomic<bool> done{false};
    std::atomic<uint64_t> polls{0};
    std::atomic<uint64_t> md_delta_sum{0};
    std::atomic<uint64_t> order_delta_sum{0};

    std::thread poller_thread([&]() {
        while (!start.load(std::memory_order_acquire)) {
            std::this_thread::yield();
        }

        while (!done.load(std::memory_order_acquire)) {
            const auto result = poller.poll();
            polls.fetch_add(1, std::memory_order_relaxed);
            md_delta_sum.fetch_add(result.md_drops_delta, std::memory_order_relaxed);
            order_delta_sum.fetch_add(result.order_drops_delta, std::memory_order_relaxed);
            if (result.reconcile_required) {
                poller.ack_reconcile();
            }
            if (result.book_resync_required) {
                poller.ack_book_resync();
            }
        }

        const auto result = poller.poll();
        polls.fetch_add(1, std::memory_order_relaxed);
        md_delta_sum.fetch_add(result.md_drops_delta, std::memory_order_relaxed);
        order_delta_sum.fetch_add(result.order_drops_delta, std::memory_order_relaxed);
    });

    std::thread mutator_thread([&]() {
        while (!start.load(std::memory_order_acquire)) {
            std::this_thread::yield();
        }

        for (int i = 1; i <= kMutations; ++i) {
            adapter.order_halt_flag.store(i % 4096 == 0, std::memory_order_relaxed);
            adapter.auto_liquidate_flag.store(i % 8192 == 0, std::memory_order_relaxed);
            adapter.position_desync_flag.store(i % 7 == 0, std::memory_order_relaxed);
            adapter.order_desync_flag.store(i % 11 == 0, std::memory_order_relaxed);
            adapter.md_data_gap_flag.store(i % 5 == 0, std::memory_order_relaxed);
            adapter.adm_severity.store((i % 257 == 0) ? 3 : ((i % 31 == 0) ? 2 : 0),
                                       std::memory_order_relaxed);
            adapter.md_drops_val.fetch_add(1, std::memory_order_relaxed);
            if (i % 2 == 0) {
                adapter.order_drops_val.fetch_add(1, std::memory_order_relaxed);
            }
            if (i % 256 == 0) {
                std::this_thread::yield();
            }
        }

        done.store(true, std::memory_order_release);
    });

    start.store(true, std::memory_order_release);
    mutator_thread.join();
    poller_thread.join();

    if (polls.load(std::memory_order_relaxed) == 0) {
        std::cerr << "[safety_poller_concurrent] poller did not run\n";
        return 1;
    }
    if (adapter.md_drops_val.load(std::memory_order_relaxed) == 0 ||
        adapter.order_drops_val.load(std::memory_order_relaxed) == 0) {
        std::cerr << "[safety_poller_concurrent] mutator did not run\n";
        return 1;
    }

    std::cout << "[safety_poller_concurrent] polls=" << polls.load(std::memory_order_relaxed)
              << " md_delta_sum=" << md_delta_sum.load(std::memory_order_relaxed)
              << " order_delta_sum=" << order_delta_sum.load(std::memory_order_relaxed)
              << "\n";
    return 0;
}
