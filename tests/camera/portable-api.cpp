/* The portable camera API's own rules: what it rejects, what it reports.
 *
 * lt_runtime_camera_write()/camera_info()/camera_flush()/camera_sensor() are
 * sliced out of Portable/src/Runtime.cpp, so this is the code the app ships, and
 * the validation helpers (lt::require, lt::guard, lt::error, lt::abi) are the
 * real ones from Portable/src/Internal.hpp. The engine behind them is a stub
 * that records what it was handed and can be told to refuse, which is how the
 * LT_AGAIN paths are reached.
 *
 * The frame's own shape is checked here rather than left to the engine: a phone
 * camera can hand out any size and any stride, and the one place that can say no
 * before memory is written is this API.
 *
 * SLICE ./Portable/src/Runtime.cpp fn lt_runtime_camera_write lt_runtime_camera_info lt_runtime_camera_flush lt_runtime_camera_sensor
 * CFLAGS -Wall -Werror -Wno-missing-field-initializers -Wno-unused-parameter
 */
#include <algorithm>
#include <atomic>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <vector>

#include "Portable/include/lighttouch.h"
#include "Portable/src/Internal.hpp"

/* The slice's view of lt_runtime::engine: the four optional entry points. */
namespace lt {
struct Engine {
    int (*camera_write)(int, const uint8_t *, int, int, int) = nullptr;
    void (*camera_flush)() = nullptr;
    int (*camera_state)(int, uint32_t *, uint32_t *, uint64_t *, uint64_t *, uint64_t *, uint64_t *) = nullptr;
    int (*camera_sensor)() = nullptr;
};
}  // namespace lt

struct lt_runtime {
    lt::Engine *engine = nullptr;
    std::mutex mutex;
    std::atomic<bool> done{false}, started{false};
};

lt_status lt_runtime_camera_write(lt_runtime *, int, const uint8_t *, uint32_t, uint32_t, uint32_t, lt_error *);
lt_status lt_runtime_camera_info(lt_runtime *, int, lt_camera_info *, lt_error *);
lt_status lt_runtime_camera_flush(lt_runtime *, lt_error *);
lt_status lt_runtime_camera_sensor(lt_runtime *, int *, lt_error *);

namespace {
lt_error fresh_error() { return LT_INIT(lt_error); }

/* A tap that records one frame, and can answer 0/-1 the way a real one does:
 * 0 when the guest is not taking frames, negative when it refused the frame. */
struct FakeEngine {
    lt::Engine api;
    int calls = 0;
    int flush_calls = 0;
    int sensor_calls = 0;
    int accepted = 1;
    int last_sensor = -1, last_width = 0, last_height = 0, last_bytes = 0;
    std::vector<uint8_t> frame;
    uint32_t state_width = 640, state_height = 480;
    uint64_t state_pushed = 0, state_taken = 0, state_replaced = 0, state_rejected = 0;
    int state_waiting = 1;
    int device_sensor = LT_CAMERA_BACK;

    FakeEngine() {
        api.camera_write = [](int sensor, const uint8_t *nv12, int bytes, int width, int height) {
            FakeEngine &self = *instance;
            self.calls++;
            self.last_sensor = sensor;
            self.last_width = width;
            self.last_height = height;
            self.last_bytes = bytes;
            if (self.accepted <= 0) return self.accepted;
            self.frame.assign(nv12, nv12 + bytes);
            return self.accepted;
        };
        api.camera_flush = [] { instance->flush_calls++; };
        api.camera_state = [](int, uint32_t *w, uint32_t *h, uint64_t *pushed, uint64_t *taken,
                              uint64_t *replaced, uint64_t *rejected) {
            FakeEngine &self = *instance;
            if (w) *w = self.state_width;
            if (h) *h = self.state_height;
            if (pushed) *pushed = self.state_pushed;
            if (taken) *taken = self.state_taken;
            if (replaced) *replaced = self.state_replaced;
            if (rejected) *rejected = self.state_rejected;
            return self.state_waiting;
        };
        api.camera_sensor = [] {
            instance->sensor_calls++;
            return instance->device_sensor;
        };
        instance = this;
    }
    static inline FakeEngine *instance = nullptr;
};

struct Runtime {
    lt_runtime runtime;
    FakeEngine engine;
    Runtime() {
        runtime.engine = &engine.api;
        runtime.started = true;
    }
};

/* A 640x480 NV12 frame: all luma, then all chroma, so a mixed-up plane shows. */
std::vector<uint8_t> nv12(int width, int height, uint8_t y = 0x40, uint8_t c = 0x80) {
    std::vector<uint8_t> frame(size_t(width) * size_t(height) * 3 / 2);
    std::fill(frame.begin(), frame.begin() + size_t(width) * size_t(height), y);
    std::fill(frame.begin() + size_t(width) * size_t(height), frame.end(), c);
    return frame;
}
}  // namespace

int main(void)
{
    {/* A whole preview frame: LT_OK, and the engine saw the size, the sensor and every byte. */
        Runtime r;
        auto frame = nv12(640, 480);
        lt_error e = fresh_error();
        assert(lt_runtime_camera_write(&r.runtime, LT_CAMERA_FRONT, frame.data(), uint32_t(frame.size()),
                                       640, 480, &e) == LT_OK);
        assert(r.engine.calls == 1);
        assert(r.engine.last_sensor == LT_CAMERA_FRONT);
        assert(r.engine.last_width == 640 && r.engine.last_height == 480);
        assert(r.engine.last_bytes == int(frame.size()));
        assert(r.engine.frame.size() == frame.size());
        assert(r.engine.frame[0] == 0x40 && r.engine.frame.back() == 0x80);
    }
    {/* A full slot: the guest has not taken the frame it replaced. LT_AGAIN, not an error. */
        Runtime r;
        auto frame = nv12(64, 64);
        r.engine.accepted = 0;
        lt_error e = fresh_error();
        assert(lt_runtime_camera_write(&r.runtime, LT_CAMERA_BACK, frame.data(), uint32_t(frame.size()),
                                       64, 64, &e) == LT_AGAIN);
        assert(std::strstr(e.message, "refused") != nullptr);
    }
    {/* The engine refusing outright is an error, not a dropped frame. */
        Runtime r;
        auto frame = nv12(64, 64);
        r.engine.accepted = -1;
        lt_error e = fresh_error();
        assert(lt_runtime_camera_write(&r.runtime, LT_CAMERA_BACK, frame.data(), uint32_t(frame.size()),
                                       64, 64, &e) == LT_INVALID);
    }
    {/* What the API refuses before an engine sees it. */
        Runtime r;
        auto frame = nv12(64, 64);
        const uint32_t bytes = uint32_t(frame.size());
        lt_error e = fresh_error();
        assert(lt_runtime_camera_write(&r.runtime, LT_CAMERA_BACK, nullptr, bytes, 64, 64, &e) == LT_INVALID);
        assert(lt_runtime_camera_write(&r.runtime, 2, frame.data(), bytes, 64, 64, &e) == LT_INVALID);
        assert(lt_runtime_camera_write(&r.runtime, -1, frame.data(), bytes, 64, 64, &e) == LT_INVALID);
        assert(lt_runtime_camera_write(&r.runtime, LT_CAMERA_BACK, frame.data(), bytes, 63, 64, &e) == LT_INVALID);
        assert(lt_runtime_camera_write(&r.runtime, LT_CAMERA_BACK, frame.data(), bytes, 64, 65, &e) == LT_INVALID);
        assert(lt_runtime_camera_write(&r.runtime, LT_CAMERA_BACK, frame.data(), bytes, 0, 0, &e) == LT_INVALID);
        /* Not NV12: the engine reads width*height*3/2 bytes, so anything else is refused here rather
         * than read past the end of the caller's buffer. */
        assert(lt_runtime_camera_write(&r.runtime, LT_CAMERA_BACK, frame.data(), bytes - 1, 64, 64, &e) == LT_INVALID);
        assert(lt_runtime_camera_write(&r.runtime, LT_CAMERA_BACK, frame.data(), bytes + 1, 64, 64, &e) == LT_INVALID);
        /* Above the tap's limit: bounded, so the engine's two buffers per sensor stay bounded. */
        auto huge = nv12(LT_CAMERA_MAX_WIDTH, LT_CAMERA_MAX_HEIGHT + 2);
        assert(lt_runtime_camera_write(&r.runtime, LT_CAMERA_BACK, huge.data(), uint32_t(huge.size()),
                                       LT_CAMERA_MAX_WIDTH, LT_CAMERA_MAX_HEIGHT + 2, &e) == LT_INVALID);
        assert(r.engine.calls == 0);
        /* The limit itself is fine. */
        auto biggest = nv12(LT_CAMERA_MAX_WIDTH, LT_CAMERA_MAX_HEIGHT);
        assert(lt_runtime_camera_write(&r.runtime, LT_CAMERA_BACK, biggest.data(), uint32_t(biggest.size()),
                                       LT_CAMERA_MAX_WIDTH, LT_CAMERA_MAX_HEIGHT, &e) == LT_OK);
    }
    {/* An engine from before the camera tap: LT_UNSUPPORTED, engine untouched. */
        Runtime r;
        lt::Engine older;
        r.runtime.engine = &older;
        auto frame = nv12(64, 64);
        lt_error e = fresh_error();
        assert(lt_runtime_camera_write(&r.runtime, LT_CAMERA_BACK, frame.data(), uint32_t(frame.size()),
                                       64, 64, &e) == LT_UNSUPPORTED);
        lt_camera_info info = LT_INIT(lt_camera_info);
        assert(lt_runtime_camera_info(&r.runtime, LT_CAMERA_BACK, &info, &e) == LT_UNSUPPORTED);
        assert(lt_runtime_camera_flush(&r.runtime, &e) == LT_UNSUPPORTED);
        int sensor = -1;
        assert(lt_runtime_camera_sensor(&r.runtime, &sensor, &e) == LT_UNSUPPORTED);
    }
    {/* A guest that is not running takes no frames: say so instead of filling a slot. */
        Runtime r;
        r.runtime.started = false;
        auto frame = nv12(64, 64);
        lt_error e = fresh_error();
        assert(lt_runtime_camera_write(&r.runtime, LT_CAMERA_BACK, frame.data(), uint32_t(frame.size()),
                                       64, 64, &e) == LT_AGAIN);
        assert(r.engine.calls == 0);
    }
    {/* The counters copy field for field, and the sensor is the one asked for. */
        Runtime r;
        r.engine.state_width = 1024;
        r.engine.state_height = 768;
        r.engine.state_pushed = 4242;
        r.engine.state_taken = 40;
        r.engine.state_replaced = 1200;
        r.engine.state_rejected = 3;
        lt_camera_info info = LT_INIT(lt_camera_info);
        lt_error e = fresh_error();
        assert(lt_runtime_camera_info(&r.runtime, LT_CAMERA_FRONT, &info, &e) == LT_OK);
        assert(info.sensor == uint32_t(LT_CAMERA_FRONT));
        assert(info.width == 1024 && info.height == 768);
        assert(info.pushed_frames == 4242 && info.taken_frames == 40);
        assert(info.replaced_frames == 1200 && info.rejected_frames == 3);
        /* No frame waiting reads as a zero size, and a bad sensor or output is refused. */
        r.engine.state_width = r.engine.state_height = 0;
        assert(lt_runtime_camera_info(&r.runtime, LT_CAMERA_BACK, &info, &e) == LT_OK);
        assert(info.width == 0 && info.height == 0);
        assert(lt_runtime_camera_info(&r.runtime, LT_CAMERA_BACK, nullptr, &e) == LT_INVALID);
        assert(lt_runtime_camera_info(&r.runtime, 5, &info, &e) == LT_INVALID);
        lt_camera_info small{};
        small.size = sizeof(small) - 8;
        small.version = LT_ABI_VERSION;
        assert(lt_runtime_camera_info(&r.runtime, LT_CAMERA_BACK, &small, &e) != LT_OK);
    }
    {/* Which sensor the guest uses: the front one when it says front, the back one otherwise. */
        Runtime r;
        int sensor = -1;
        lt_error e = fresh_error();
        assert(lt_runtime_camera_sensor(&r.runtime, &sensor, &e) == LT_OK);
        assert(sensor == LT_CAMERA_BACK && r.engine.sensor_calls == 1);
        r.engine.device_sensor = LT_CAMERA_FRONT;
        assert(lt_runtime_camera_sensor(&r.runtime, &sensor, &e) == LT_OK);
        assert(sensor == LT_CAMERA_FRONT);
        /* An engine that reports something else is read as the back camera rather than passed on as
         * a sensor the app cannot open. */
        r.engine.device_sensor = 9;
        assert(lt_runtime_camera_sensor(&r.runtime, &sensor, &e) == LT_OK);
        assert(sensor == LT_CAMERA_BACK);
        assert(lt_runtime_camera_sensor(&r.runtime, nullptr, &e) == LT_INVALID);
    }
    {/* Flush reaches the engine. */
        Runtime r;
        lt_error e = fresh_error();
        assert(lt_runtime_camera_flush(&r.runtime, &e) == LT_OK);
        assert(r.engine.flush_calls == 1);
    }
    std::puts("portable API: NV12 shape, sensor choice, refusal and non-running guest all report as documented");
    return 0;
}
