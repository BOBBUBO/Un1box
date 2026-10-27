#pragma once
#include <algorithm>
#include <cstdint>

namespace lt {
// Queue accounting is an SDL dequeue estimate, never a DAC/playback-head or
// physical-audibility observation. Callbacks are the actual adapter operations.
struct AudioQueue {
    static constexpr uint32_t rate = 44100, channels = 2, bytes_per_frame = 4, cap_frames = 4410;
    uint64_t queued = 0, discarded = 0, consumed_estimate = 0, errors = 0;
    uint64_t epoch = 0, command = 0;
    uint32_t depth = 0, peak_depth = 0;
    bool muted = true, failed = false, stopped = false;
    void observe(uint32_t bytes) {
        depth = bytes / bytes_per_frame;
        peak_depth = std::max(peak_depth, depth);
        const uint64_t retained = discarded + depth;
        if (queued >= retained)
            consumed_estimate = std::max(consumed_estimate, queued - retained);
    }
    template <class Depth, class Pause, class Clear> void mute(Depth bytes, Pause pause, Clear clear) {
        pause(true);
        observe(bytes());
        discarded += depth;
        clear();
        depth = 0;
        muted = true;
    }
    template <class Depth, class Pause, class Clear> void request(uint64_t id, Depth bytes, Pause pause, Clear clear) {
        command = id;
        mute(bytes, pause, clear);
    }
    template <class Pause> void acknowledge(uint64_t id, uint64_t generation, bool running, Pause pause) {
        if (id != command || stopped || failed)
            return;
        epoch = generation;
        muted = !running;
        pause(muted);
    }
    template <class Stop> void fault(Stop stop) {
        if (!failed) {
            failed = muted = true;
            ++errors;
            stop();
        }
    }
    template <class Queue> uint32_t append(uint64_t generation, uint32_t frames, uint32_t bytes, Queue queue) {
        observe(bytes);
        if (muted || failed || stopped || generation != epoch) {
            // Transport discard is separate from SDL accepted/discarded counts.
            return 0;
        }
        const uint32_t admit = std::min(frames, depth < cap_frames ? cap_frames - depth : 0);
        if (admit && !queue(admit))
            return 0; // Caller reports error and performs ordinary Stop.
        queued += admit;
        depth += admit;
        peak_depth = std::max(peak_depth, depth);
        return admit;
    }
};
} // namespace lt
