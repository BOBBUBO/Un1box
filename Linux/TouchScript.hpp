#pragma once
#include "lighttouch.h"
#include <cmath>
#include <istream>
#include <set>
#include <stdexcept>

namespace lt {
inline lt_touch_batch parse_touch_contacts(std::istream &input, uint32_t count) {
    if (!count || count > LT_MAX_CONTACTS)
        throw std::runtime_error("script touch count outside native bound");
    lt_touch_batch batch{sizeof(batch), 1, 0, count, 0, {}};
    std::set<uint32_t> ids;
    for (uint32_t i = 0; i < count; i++) {
        auto &c = batch.contacts[i];
        c = {sizeof(c), 1, 0, 0, 0, 0};
        if (!(input >> c.id >> c.phase >> c.x >> c.y) || c.phase > 3 || !std::isfinite(c.x) || !std::isfinite(c.y) ||
            c.x < 0 || c.x > 1 || c.y < 0 || c.y > 1 || !ids.insert(c.id).second)
            throw std::runtime_error("invalid scripted contact or duplicate ID");
    }
    input >> std::ws;
    if (!input.eof())
        throw std::runtime_error("unexpected script touch arguments");
    return batch;
}
} // namespace lt
