#pragma once

#include <atomic>
#include <cstddef>
#include <cassert>
#include <array>

namespace hft {

// Hardware interference size is typically 64 bytes (cache line).
constexpr size_t CACHE_LINE_SIZE = 64;

// A highly optimized Single-Producer Single-Consumer lock-free queue.
// Fully heap-allocation free. Aligned and padded to completely eliminate false sharing.
template<typename T, size_t Capacity>
class alignas(CACHE_LINE_SIZE) SPSCQueue {
public:
    static_assert((Capacity & (Capacity - 1)) == 0, "Capacity must be a power of 2");

    SPSCQueue() : head_(0), tail_(0) {}

    bool push(const T& item) noexcept {
        const size_t current_tail = tail_.load(std::memory_order_relaxed);
        const size_t next_tail = (current_tail + 1) & (Capacity - 1);

        if (next_tail == head_.load(std::memory_order_acquire)) [[unlikely]] {
            // Queue is full
            return false;
        }

        buffer_[current_tail] = item;
        tail_.store(next_tail, std::memory_order_release);
        return true;
    }

    bool pop(T& item) noexcept {
        const size_t current_head = head_.load(std::memory_order_relaxed);

        if (current_head == tail_.load(std::memory_order_acquire)) [[unlikely]] {
            // Queue is empty
            return false;
        }

        item = buffer_[current_head];
        head_.store((current_head + 1) & (Capacity - 1), std::memory_order_release);
        return true;
    }

private:
    // Ensure buffer does not share a cache line with the pointers.
    alignas(CACHE_LINE_SIZE) std::array<T, Capacity> buffer_{};
    
    // Explicit padding to ensure head and tail are on different cache lines
    alignas(CACHE_LINE_SIZE) std::atomic<size_t> head_{0};
    char pad0_[CACHE_LINE_SIZE - sizeof(std::atomic<size_t>)];

    alignas(CACHE_LINE_SIZE) std::atomic<size_t> tail_{0};
    char pad1_[CACHE_LINE_SIZE - sizeof(std::atomic<size_t>)];
};

} // namespace hft