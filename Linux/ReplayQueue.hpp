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
// Check a stop request before each overdue command, including when several
// commands became due in one loop iteration. Stop may discard generated replay
// entries, so recheck the queue before taking the next entry.
template <class Entry, class Requested, class Stop, class Admit, class Dispatch>
void dispatch_due_script(std::vector<Entry> &entries, size_t &next, double elapsed, Requested requested, Stop stop,
                         Admit admit, Dispatch dispatch) {
    while (next < entries.size() && elapsed >= entries[next].at) {
        if (requested())
            stop();
        if (next >= entries.size() || elapsed < entries[next].at)
            break;
        const auto command = entries[next++];
        if (admit(command))
            dispatch(command);
    }
}
} // namespace lt
