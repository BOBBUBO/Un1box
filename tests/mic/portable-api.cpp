/* The portable microphone API's own rules: what it rejects, what it reports.
 *
 * lt_runtime_mic_write()/mic_info()/mic_flush() are sliced out of
 * Portable/src/Runtime.cpp, so this is the code the app ships, and the
 * validation helpers (lt::require, lt::guard, lt::error, lt::abi) are the real
 * ones from Portable/src/Internal.hpp. The engine behind them is a stub whose
 * ring can be told to accept less than it was offered, which is how the LT_AGAIN
 * paths are reached.
 *
 * SLICE ./Portable/src/Runtime.cpp fn lt_runtime_mic_write lt_runtime_mic_info lt_runtime_mic_flush
 * CFLAGS -Wall -Werror -Wno-missing-field-initializers -Wno-unused-parameter
 */
#include <atomic>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <vector>

#include "Portable/include/lighttouch.h"
#include "Portable/src/Internal.hpp"

/* The slice's view of lt_runtime::engine: the three optional entry points. */
namespace lt {
struct Engine {
    int (*mic_write)(const void *, int) = nullptr;
    void (*mic_flush)() = nullptr;
    int (*mic_state)(uint32_t *, uint32_t *, uint64_t *, uint64_t *) = nullptr;
};
}  // namespace lt

struct lt_runtime {
    lt::Engine *engine = nullptr;
    std::mutex mutex;
    std::atomic<bool> done{false}, started{false};
};

lt_status lt_runtime_mic_write(lt_runtime *, const int16_t *, uint32_t, lt_error *);
lt_status lt_runtime_mic_info(lt_runtime *, lt_mic_info *, lt_error *);
lt_status lt_runtime_mic_flush(lt_runtime *, lt_error *);

namespace {
lt_error fresh_error() { return LT_INIT(lt_error); }

/* A ring that records what it was handed and can pretend to be full. */
struct FakeEngine {
    lt::Engine api;
    uint32_t capacity = 4096;
    std::vector<int16_t> frames;
    int calls = 0;
    int flush_calls = 0;
    uint32_t state_rate = 44100, state_queued = 0, state_capturing = 0;
    uint64_t state_pushed = 0, state_dropped = 0;
    int read_result = -1;

    FakeEngine() {
        api.mic_write = [](const void *frames, int bytes) {
            FakeEngine &self = *instance;
            self.calls++;
            const int16_t *in = static_cast<const int16_t *>(frames);
            const int wanted = bytes / 4;
            int taken = 0;
            for (; taken < wanted && self.frames.size() / 2 < self.capacity; taken++) {
                self.frames.push_back(in[taken * 2]);
                self.frames.push_back(in[taken * 2 + 1]);
            }
            return taken;
        };
        api.mic_flush = [] { instance->flush_calls++; };
        api.mic_state = [](uint32_t *rate, uint32_t *queued, uint64_t *pushed, uint64_t *dropped) {
            FakeEngine &self = *instance;
            if (rate) *rate = self.state_rate;
            if (queued) *queued = self.state_queued;
            if (pushed) *pushed = self.state_pushed;
            if (dropped) *dropped = self.state_dropped;
            return int(self.state_capturing);
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
}  // namespace

int main(void)
{
    int16_t block[64 * 2];
    for (int i = 0; i < 64 * 2; i++) block[i] = int16_t(i);

    {/* A whole block that fits: LT_OK, and the frames arrived interleaved. */
        Runtime r;
        lt_error e = fresh_error();
        assert(lt_runtime_mic_write(&r.runtime, block, 64, &e) == LT_OK);
        assert(r.engine.calls == 1);
        assert(r.engine.frames.size() == 128);
        assert(r.engine.frames[0] == block[0] && r.engine.frames[127] == block[127]);
    }
    {/* A full ring: the guest is not reading. LT_AGAIN, not an error. */
        Runtime r;
        r.engine.capacity = 16;
        lt_error e = fresh_error();
        assert(lt_runtime_mic_write(&r.runtime, block, 64, &e) == LT_AGAIN);
        assert(std::strstr(e.message, "ring full") != nullptr);
    }
    {/* Nothing to write, and a block longer than a second. */
        Runtime r;
        lt_error e = fresh_error();
        assert(lt_runtime_mic_write(&r.runtime, block, 0, &e) == LT_INVALID);
        assert(lt_runtime_mic_write(&r.runtime, nullptr, 64, &e) == LT_INVALID);
        static int16_t huge[(LT_MIC_RATE + 1) * 2];  /* interleaved stereo frames */
        assert(lt_runtime_mic_write(&r.runtime, huge, LT_MIC_RATE + 1, &e) == LT_INVALID);
        assert(r.engine.calls == 0);
        /* A whole second is the largest single block the API accepts. */
        r.engine.capacity = LT_MIC_RATE;
        assert(lt_runtime_mic_write(&r.runtime, huge, LT_MIC_RATE, &e) == LT_OK);
    }
    {/* An engine from before the microphone tap: LT_UNSUPPORTED, engine untouched. */
        Runtime r;
        lt::Engine older;
        r.runtime.engine = &older;
        lt_error e = fresh_error();
        assert(lt_runtime_mic_write(&r.runtime, block, 64, &e) == LT_UNSUPPORTED);
        lt_mic_info info = LT_INIT(lt_mic_info);
        assert(lt_runtime_mic_info(&r.runtime, &info, &e) == LT_UNSUPPORTED);
        assert(lt_runtime_mic_flush(&r.runtime, &e) == LT_UNSUPPORTED);
    }
    {/* A guest that is not running reads nothing: say so instead of filling a ring. */
        Runtime r;
        r.runtime.started = false;
        lt_error e = fresh_error();
        assert(lt_runtime_mic_write(&r.runtime, block, 64, &e) == LT_AGAIN);
        assert(r.engine.calls == 0);
    }
    {/* A block below the ABI's minimum size is refused rather than guessed at. */
        Runtime r;
        lt_error e = fresh_error();
        lt_mic_info small{};
        small.size = sizeof(small) - 8;
        small.version = LT_ABI_VERSION;
        assert(lt_runtime_mic_info(&r.runtime, &small, &e) != LT_OK);
    }
    {/* The counters copy field for field, and capturing is normalised to 0/1. */
        Runtime r;
        r.engine.state_rate = 22050;
        r.engine.state_queued = 137;
        r.engine.state_capturing = 7;
        r.engine.state_pushed = 900001;
        r.engine.state_dropped = 12;
        lt_mic_info info = LT_INIT(lt_mic_info);
        lt_error e = fresh_error();
        assert(lt_runtime_mic_info(&r.runtime, &info, &e) == LT_OK);
        assert(info.sample_rate == 22050);
        assert(info.queued_frames == 137);
        assert(info.capturing == 1);
        assert(info.pushed_frames == 900001);
        assert(info.dropped_frames == 12);
        /* A null output is a programming error, not a crash. */
        assert(lt_runtime_mic_info(&r.runtime, nullptr, &e) == LT_INVALID);
    }
    {/* Flush reaches the engine. */
        Runtime r;
        lt_error e = fresh_error();
        assert(lt_runtime_mic_flush(&r.runtime, &e) == LT_OK);
        assert(r.engine.flush_calls == 1);
    }
    std::puts("portable API: validation, ring-full and non-running guest all report as documented");
    return 0;
}
