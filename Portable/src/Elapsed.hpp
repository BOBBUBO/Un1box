#pragma once
#include <cstdint>

namespace lt {
inline constexpr uint64_t worker_shutdown_timeout_ns = 30000000000ull;
inline constexpr uint64_t parent_shutdown_timeout_ns = 35000000000ull;

inline bool elapsed_exceeds(uint64_t current, uint64_t started, uint64_t duration) {
    return current >= started && current - started > duration;
}
} // namespace lt
