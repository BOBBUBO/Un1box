#pragma once
#include <cstdint>
#include <deque>
#include <utility>

namespace lt {
// Only the publisher calls this helper and the engine's capture APIs.
struct PcmCaptureEpoch {
    uint64_t epoch = 0, generation = 0;
    template <class Stop, class Start>
    void synchronize(uint64_t requested_epoch, bool suspended, bool vm_paused, Stop stop, Start start) {
        if (epoch != requested_epoch || suspended || vm_paused) {
            if (generation)
                stop(generation);
            generation = 0;
            epoch = requested_epoch;
        }
        if (!suspended && !vm_paused && !generation)
            generation = start(); // Engine start clears its ring synchronously.
    }
};
// All methods require the runtime queue mutex. Capture calls remain owned by
// the publisher; a read begun before a lifecycle boundary cannot append later.
template <class Packet> struct PcmEpochQueue {
    uint64_t epoch = 1, dropped = 0;
    bool suspended = false;
    std::deque<Packet> packets;
    void transition(bool pause) {
        ++epoch;
        suspended = pause;
        for (const auto &packet : packets)
            dropped += packet.bytes / 4;
        packets.clear();
    }
    bool append(uint64_t read_epoch, Packet packet) {
        if (read_epoch != epoch || suspended) {
            dropped += packet.bytes / 4;
            return false;
        }
        if (packets.size() == 16) {
            dropped += packets.front().bytes / 4;
            packets.pop_front();
        }
        packets.push_back(std::move(packet));
        return true;
    }
};
} // namespace lt
