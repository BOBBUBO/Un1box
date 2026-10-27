#include "../AudioQueue.hpp"
#include "../ContactOwnership.hpp"
#include "../ControlDelivery.hpp"
#include "../PauseFence.hpp"
#include <cassert>
#include <vector>

int main() {
    lt::ContactOwnership owner;
    lt::ControlDelivery delivery;
    int cancelled = 0;
    auto cancel = [&] {
        ++cancelled;
        return delivery.send(lt::ControlDelivery::Kind::cancel, [] { return false; });
    };
    owner.resized(480, 800, 320, 480, 0, cancel);
    lt_touch_batch batch{sizeof(batch), 1, 0, 2, 0, {}};
    batch.contacts[0] = {sizeof(lt_contact), 1, 101, 0, .3, .5};
    batch.contacts[1] = {sizeof(lt_contact), 1, 102, 0, .7, .5};
    owner.admitted(batch); // Replay has no mouse/finger map, still owned.
    assert(!owner.resized(480, 800, 320, 480, 0, cancel));
    assert(owner.resized(800, 480, 320, 480, 0, cancel));
    assert(!owner.held() && cancelled == 1 && delivery.needs_cancel());
    assert(!delivery.send(lt::ControlDelivery::Kind::input, [] { return true; }));
    delivery.retry_cancel([] { return true; });
    assert(!delivery.needs_cancel());
    owner.admitted(batch);
    owner.contacts[1] = {.3, .5};
    owner.fingers[70] = 1;
    double x = 0, y = 0;
    assert(lt_map_contact(10, 400, 40, 0, 400, 800, 0, &x, &y) == LT_AGAIN);
    assert(owner.outside(lt_map_contact(10, 400, 40, 0, 400, 800, 0, &x, &y) == LT_OK, cancel));
    assert(owner.contacts.empty() && owner.fingers.empty() && !owner.held());
    // Reentry cannot find an old ID; only a fresh Down creates ownership.
    assert(!owner.outside(lt_map_contact(240, 400, 40, 0, 400, 800, 0, &x, &y) == LT_OK, cancel));
    assert(owner.contacts.count(1) == 0);
    delivery.retry_cancel([] { return true; });
    batch.count = 1;
    batch.contacts[0].id = 1;
    owner.admitted(batch);
    assert(owner.held());
    batch.contacts[0].phase = 2;
    owner.admitted(batch);
    assert(!owner.held());

    lt::AudioQueue sink;
    uint32_t bytes = 0;
    std::vector<int> calls;
    auto pause = [&](bool muted) { calls.push_back(muted ? 1 : 0); };
    auto depth = [&] {
        assert(calls.back() == 1); // Sample tail only after device paused.
        return bytes;
    };
    auto clear = [&] { calls.push_back(2); bytes = 0; };
    auto enqueue = [&](uint32_t frames) { bytes += frames * 4; return true; };
    sink.acknowledge(0, 1, true, pause);
    assert(sink.append(1, 4096, bytes, enqueue) == 4096);
    assert(sink.append(1, 4096, bytes, enqueue) == 314);
    assert(bytes == 17640 && sink.peak_depth == 4410);
    bytes -= 400; // SDL dequeued100 frames, not proof DAC played100.
    sink.request(1, depth, pause, clear);
    assert(sink.muted && bytes == 0 && sink.discarded == 4310 && sink.consumed_estimate == 100);
    assert(sink.append(1, 512, bytes, enqueue) == 0);
    sink.request(2, depth, pause, clear);
    sink.acknowledge(1, 2, true, pause); // Delayed pause-era status cannot unmute.
    assert(sink.muted);
    sink.acknowledge(2, 3, true, pause);
    assert(!sink.muted && sink.append(2, 512, bytes, enqueue) == 0);
    assert(sink.append(3, 512, bytes, enqueue) == 512);
    int stops = 0;
    assert(sink.append(3, 10, bytes, [](uint32_t) { return false; }) == 0);
    sink.fault([&] { ++stops; });
    sink.fault([&] { ++stops; });
    sink.acknowledge(2, 3, true, pause);
    assert(stops == 1 && sink.failed && sink.muted && sink.errors == 1);
    assert(sink.append(3, 512, bytes, enqueue) == 0);
    sink.stopped = true;
    sink.mute(depth, pause, clear);
    sink.acknowledge(2, 4, true, pause);
    assert(sink.muted && bytes == 0);

    lt::PauseFence fence;
    uint64_t resume_command = 0;
    fence.pause_admitted();
    assert(fence.defer_resume(2));
    assert(!fence.resume_ready(false, false, resume_command));
    assert(fence.resume_ready(true, false, resume_command) && resume_command == 2);
    assert(!fence.resume_ready(true, false, resume_command));
    fence.pause_admitted();
    assert(fence.defer_resume(4));
    fence.pause_admitted(); // A newer Pause supersedes the old deferred Resume.
    assert(!fence.resume_ready(true, false, resume_command));
    fence.pause_admitted();
    assert(fence.defer_resume(6));
    assert(!fence.resume_ready(true, true, resume_command));
    assert(!fence.resume_ready(true, false, resume_command));
}
