#include "spsc_queue.hpp"

#include <atomic>
#include <cstdint>
#include <iostream>
#include <thread>

namespace {

struct Item {
    uint64_t seq = 0;
    uint64_t checksum = 0;
};

constexpr uint64_t kIterations = 200000;

uint64_t checksum(uint64_t seq) noexcept {
    return seq ^ 0x9e3779b97f4a7c15ULL;
}

} // namespace

int main() {
    hft::SPSCQueue<Item, 1024> queue;
    std::atomic<bool> start{false};
    std::atomic<bool> stop{false};
    std::atomic<int> errors{0};

    std::thread producer([&]() {
        while (!start.load(std::memory_order_acquire)) {
            std::this_thread::yield();
        }

        for (uint64_t seq = 1; seq <= kIterations && !stop.load(std::memory_order_acquire); ++seq) {
            const Item item{seq, checksum(seq)};
            while (!queue.push(item)) {
                if (stop.load(std::memory_order_acquire)) {
                    return;
                }
                std::this_thread::yield();
            }
        }
    });

    std::thread consumer([&]() {
        while (!start.load(std::memory_order_acquire)) {
            std::this_thread::yield();
        }

        Item item{};
        uint64_t expected = 1;
        while (expected <= kIterations) {
            if (!queue.pop(item)) {
                std::this_thread::yield();
                continue;
            }
            if (item.seq != expected || item.checksum != checksum(item.seq)) {
                errors.fetch_add(1, std::memory_order_relaxed);
                stop.store(true, std::memory_order_release);
                return;
            }
            ++expected;
        }
    });

    start.store(true, std::memory_order_release);
    producer.join();
    consumer.join();

    if (errors.load(std::memory_order_relaxed) != 0) {
        std::cerr << "[spsc_queue_stress] sequence or checksum mismatch\n";
        return 1;
    }

    std::cout << "[spsc_queue_stress] pushed and popped " << kIterations << " items\n";
    return 0;
}
