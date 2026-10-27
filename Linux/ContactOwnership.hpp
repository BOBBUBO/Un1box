#pragma once
#include "lighttouch.h"
#include <array>
#include <cstdint>
#include <map>
#include <set>
#include <utility>

namespace lt {
class ContactOwnership {
    std::set<uint32_t> active;
    std::array<int, 5> viewport{};
    bool viewport_known = false;

  public:
    std::map<uint32_t, std::pair<double, double>> contacts;
    std::map<int64_t, uint32_t> fingers;
    bool held() const { return !active.empty(); }
    void clear() {
        active.clear();
        contacts.clear();
        fingers.clear();
    }
    // Record only batches admitted to the worker socket, including replay.
    // Actual engine rejection clears this ledger through the normal error path.
    void admitted(const lt_touch_batch &batch) {
        for (uint32_t i = 0; i < batch.count && i < LT_MAX_CONTACTS; ++i) {
            const auto &c = batch.contacts[i];
            if (c.phase == 2 || c.phase == 3)
                active.erase(c.id);
            else
                active.insert(c.id);
        }
    }
    template <class Cancel> bool outside(bool mapped, Cancel cancel) {
        if (mapped || !held())
            return false;
        cancel();
        clear(); // Failed cancellation still drops local IDs; retry is fenced.
        return true;
    }
    template <class Cancel> bool resized(int w, int h, int guest_w, int guest_h, int rotation, Cancel cancel) {
        const std::array<int, 5> current{w, h, guest_w, guest_h, rotation};
        const bool changed = viewport_known && current != viewport;
        viewport = current;
        viewport_known = w > 0 && h > 0 && guest_w > 0 && guest_h > 0;
        if (!changed || !held())
            return false;
        cancel();
        clear();
        return true;
    }
};
} // namespace lt
