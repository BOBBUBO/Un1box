#pragma once
#include <algorithm>
#include <cstddef>
#include <vector>
namespace lt {
// Cancellation drops generated future contact transitions, while keeping
// caller-supplied checkpoints/lifecycle commands and already-consumed history.
template <class Entry> size_t discard_replay(std::vector<Entry> &entries, size_t next) {
    const size_t previous = entries.size();
    const auto first = entries.begin() + std::min(next, entries.size());
    entries.erase(std::remove_if(first, entries.end(), [](const Entry &entry) { return entry.replay; }), entries.end());
    return previous - entries.size();
}
} // namespace lt
