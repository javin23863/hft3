#include "risk_manager.hpp"

#include <atomic>
#include <cstdint>
#include <iostream>
#include <thread>
#include <vector>

namespace {

constexpr int kReaderThreads = 4;
constexpr int kWriterIterations = 100000;

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
    hft::risk::RiskManager risk(stress_limits());
    std::atomic<bool> start{false};
    std::atomic<bool> done{false};
    std::atomic<int> errors{0};
    std::atomic<uint64_t> reader_checks{0};

    std::thread writer([&]() {
        while (!start.load(std::memory_order_acquire)) {
            std::this_thread::yield();
        }

        for (int i = 0; i < kWriterIterations; ++i) {
            risk.update_position((i % 2 == 0) ? 1 : -1, 0.0);
            risk.record_order_sent();
            risk.record_cancel_sent();
            if (i % 256 == 0) {
                std::this_thread::yield();
            }
        }

        done.store(true, std::memory_order_release);
    });

    std::vector<std::thread> readers;
    readers.reserve(kReaderThreads);
    for (int i = 0; i < kReaderThreads; ++i) {
        readers.emplace_back([&]() {
            while (!start.load(std::memory_order_acquire)) {
                std::this_thread::yield();
            }

            do {
                const auto order_status = risk.check_order(1);
                const auto cancel_status = risk.check_cancel();
                if (order_status == hft::risk::RiskStatus::HALT ||
                    order_status == hft::risk::RiskStatus::FLATTEN ||
                    cancel_status != hft::risk::RiskStatus::PASS) {
                    errors.fetch_add(1, std::memory_order_relaxed);
                }
                reader_checks.fetch_add(1, std::memory_order_relaxed);
            } while (!done.load(std::memory_order_acquire));
        });
    }

    start.store(true, std::memory_order_release);
    writer.join();
    for (auto& reader : readers) {
        reader.join();
    }

    if (errors.load(std::memory_order_relaxed) != 0) {
        std::cerr << "[risk_manager_atomic_stress] unexpected risk status\n";
        return 1;
    }
    if (reader_checks.load(std::memory_order_relaxed) == 0) {
        std::cerr << "[risk_manager_atomic_stress] readers did not run\n";
        return 1;
    }

    std::cout << "[risk_manager_atomic_stress] reader checks: "
              << reader_checks.load(std::memory_order_relaxed) << "\n";
    return 0;
}
