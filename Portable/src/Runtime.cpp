#include "Internal.hpp"
#include "qemu-ios-ui.h"
#include "qemu-macos-extras.h"
#include <atomic>
#include <array>
#include <chrono>
#include <cmath>
#include <deque>
#include <dlfcn.h>
#include <fcntl.h>
#include <memory>
#include <mutex>
#include <thread>
#include <unistd.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <time.h>
#include <limits>
namespace lt {
static uint64_t now_ns() {
    timespec ts{};
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return uint64_t(ts.tv_sec) * 1000000000ull + uint64_t(ts.tv_nsec);
}
struct Engine {
    void *library = nullptr;
    decltype(&qemu_ios_main) main = nullptr;
    decltype(&qemu_ios_portable_board_at) board_at = nullptr;
    decltype(&qemu_ios_portable_board) board = nullptr;
    decltype(&qemu_ios_portable_frame_copy) frame = nullptr;
    decltype(&qemu_ios_portable_touch) touch = nullptr;
    decltype(&qemu_ios_portable_input_status) input_status = nullptr;
    decltype(&qemu_ios_portable_motion) motion = nullptr;
    decltype(&qemu_ios_portable_cancel_input) cancel = nullptr;
    decltype(&qemu_ios_ui_ready) ready = nullptr;
    decltype(&qemu_ios_ui_paused) paused = nullptr;
    decltype(&qemu_ios_ui_storage_failed) storage_failed = nullptr;
    decltype(&qemu_ios_ui_guest_shutdown_confirmed) shutdown_confirmed = nullptr;
    decltype(&qemu_ios_ui_pause) pause = nullptr;
    decltype(&qemu_ios_ui_resume) resume = nullptr;
    decltype(&qemu_ios_ui_shutdown) shutdown = nullptr;
    decltype(&qemu_ios_ui_quit) quit = nullptr;
    decltype(&qemu_ios_ui_button) button = nullptr;
    decltype(&qemu_ios_audio_capture_start) audio_start = nullptr;
    decltype(&qemu_ios_audio_capture_read) audio_read = nullptr;
    decltype(&qemu_ios_audio_capture_stop) audio_stop = nullptr;
    template <class F> F symbol(const char *n) {
        auto p = dlsym(library, n);
        require(p, std::string("engine symbol missing: ") + n, LT_UNSUPPORTED);
        return reinterpret_cast<F>(p);
    }
    explicit Engine(const char *path) {
        require(path && *path, "engine library missing");
        // QEMU registers process-global type/module callbacks at library load. Keep
        // code resident even when a discovery-only handle is released.
        int flags = RTLD_NOW | RTLD_GLOBAL;
#ifdef RTLD_NODELETE
        flags |= RTLD_NODELETE;
#endif
        library = dlopen(path, flags);
        if (!library) {
            const char *e = dlerror();
            throw Failure(LT_ENGINE, e ? e : "engine load failed");
        }
        try {
            auto version = symbol<decltype(&qemu_ios_api_version)>("qemu_ios_api_version");
            require((version() >> 16) == 2, "incompatible qemu_ios API major", LT_UNSUPPORTED);
            auto portable = symbol<decltype(&qemu_ios_portable_version)>("qemu_ios_portable_version");
            require(portable() == 1, "incompatible portable engine ABI", LT_UNSUPPORTED);
#define LOAD(member, name) member = symbol<decltype(member)>(#name)
            LOAD(main, qemu_ios_main);
            LOAD(board_at, qemu_ios_portable_board_at);
            LOAD(board, qemu_ios_portable_board);
            LOAD(frame, qemu_ios_portable_frame_copy);
            LOAD(touch, qemu_ios_portable_touch);
            LOAD(input_status, qemu_ios_portable_input_status);
            LOAD(motion, qemu_ios_portable_motion);
            LOAD(cancel, qemu_ios_portable_cancel_input);
            LOAD(ready, qemu_ios_ui_ready);
            LOAD(paused, qemu_ios_ui_paused);
            LOAD(storage_failed, qemu_ios_ui_storage_failed);
            LOAD(shutdown_confirmed, qemu_ios_ui_guest_shutdown_confirmed);
            LOAD(pause, qemu_ios_ui_pause);
            LOAD(resume, qemu_ios_ui_resume);
            LOAD(shutdown, qemu_ios_ui_shutdown);
            LOAD(quit, qemu_ios_ui_quit);
            LOAD(button, qemu_ios_ui_button);
            LOAD(audio_start, qemu_ios_audio_capture_start);
            LOAD(audio_read, qemu_ios_audio_capture_read);
            LOAD(audio_stop, qemu_ios_audio_capture_stop);
#undef LOAD
        } catch (...) {
            dlclose(library);
            library = nullptr;
            throw;
        }
    }
    ~Engine() {
        if (library)
            dlclose(library);
    }
    QemuIosPortableBoard find_board(const std::string &id) {
        for (int index = 0; index < 128; index++) {
            QemuIosPortableBoard facts{sizeof(facts), 1, {}, {}, 0, 0, 0, 0, 0, 0};
            int result = board_at(index, &facts);
            if (result == QEMU_IOS_UNSUPPORTED)
                break;
            require(result == QEMU_IOS_OK, "engine board discovery failed", LT_ENGINE);
            if (id == facts.board || id == facts.machine)
                return facts;
        }
        throw Failure(LT_UNSUPPORTED, "engine does not expose board: " + id);
    }
};
static void result(int r) {
    if (r == QEMU_IOS_OK || r == QEMU_IOS_QUEUED)
        return;
    if (r == QEMU_IOS_UNSUPPORTED)
        throw Failure(LT_UNSUPPORTED, "guest board does not support this operation");
    if (r == QEMU_IOS_NOT_READY || r == QEMU_IOS_BUSY || r == QEMU_IOS_STALE)
        throw Failure(LT_AGAIN, "engine not ready, busy, or stale input");
    throw Failure(LT_INVALID, "engine rejected operation: " + std::to_string(r));
}
static std::atomic<bool> process_started{false};
struct PcmPacket {
    std::array<int16_t, 8192> samples{};
    uint32_t bytes = 0;
    uint64_t timestamp = 0;
};
} // namespace lt
struct lt_runtime {
    lt_device *device;
    std::unique_ptr<lt::Engine> engine;
    std::thread guest, publisher;
    std::atomic<bool> done{false}, started{false}, stopping{false}, confirmed{false}, storage_failed{false},
        publisher_running{false};
    std::atomic<int> exit_code{0};
    std::atomic<uint64_t> frame_count{0};
    std::mutex mutex;
    int frame_fd = -1;
    lt_shared_frame *shared = nullptr;
    size_t mapping_bytes = 0;
    std::vector<char *> argv;
    std::vector<uint8_t> scratch;
    uint64_t serial = 0, audio_generation = 0, input_id = 0, last_motion = 0, pcm_dropped = 0;
    std::deque<lt::PcmPacket> pcm;
    bool audio_enabled = false;
    std::string fault;
    explicit lt_runtime(lt_device *d) : device(d) {}
    ~lt_runtime() {
        if (guest.joinable())
            guest.join();
        if (publisher.joinable())
            publisher.join();
        if (shared)
            munmap(shared, mapping_bytes);
        if (frame_fd >= 0)
            close(frame_fd);
    }
    void publish() {
        bool quit_sent = false;
        while (!done) {
            try {
                if (engine->ready()) {
                    if (engine->storage_failed())
                        storage_failed = true;
                    if (engine->shutdown_confirmed()) {
                        confirmed = true;
                        if (!quit_sent) {
                            engine->cancel();
                            engine->quit();
                            quit_sent = true;
                        }
                    }
                    QemuIosPortableFrame f{sizeof(f), 1, 0, 0, 0, 0, 0, 0, 0};
                    int r = engine->frame(scratch.data(), scratch.size(), serial, &f);
                    if (r == QEMU_IOS_OK) {
                        lt::require(f.required_bytes <= LT_FRAME_CAPACITY && f.stride >= f.width * 4u &&
                                        f.width <= 4096 && f.height <= 4096,
                                    "engine returned invalid frame", LT_ENGINE);
                        __atomic_add_fetch(&shared->generation, 1, __ATOMIC_SEQ_CST);
                        shared->frame = {
                            sizeof(lt_frame_info), 1, f.width, f.height, f.stride, f.format, f.serial, f.timestamp_ns,
                            f.required_bytes};
                        memcpy(reinterpret_cast<uint8_t *>(shared) + sizeof(*shared), scratch.data(),
                               size_t(f.required_bytes));
                        __atomic_add_fetch(&shared->generation, 1, __ATOMIC_SEQ_CST);
                        serial = f.serial;
                        frame_count++;
                    } else if (r != QEMU_IOS_NO_FRAME && r != QEMU_IOS_NOT_READY)
                        lt::result(r);
                    if (audio_enabled) {
                        if (!audio_generation)
                            audio_generation = engine->audio_start();
                        if (audio_generation) {
                            for (int i = 0; i < 8; i++) {
                                lt::PcmPacket p;
                                double seconds = 0;
                                int bytes = engine->audio_read(audio_generation, p.samples.data(),
                                                               int(sizeof p.samples), &seconds);
                                if (bytes < 0) {
                                    throw lt::Failure(LT_ENGINE, "engine PCM capture overflow/failure");
                                }
                                if (!bytes)
                                    break;
                                lt::require(bytes <= int(sizeof p.samples) && bytes % 4 == 0,
                                            "invalid engine PCM packet", LT_ENGINE);
                                p.bytes = uint32_t(bytes);
                                p.timestamp = uint64_t(std::max(0.0, seconds) * 1e9);
                                std::lock_guard<std::mutex> lock(mutex);
                                if (pcm.size() == 16) {
                                    pcm_dropped += pcm.front().bytes / 4;
                                    pcm.pop_front();
                                }
                                pcm.push_back(std::move(p));
                            }
                        }
                    }
                }
            } catch (const std::exception &e) {
                std::lock_guard<std::mutex> lock(mutex);
                fault = e.what();
                storage_failed = true;
                if (engine->ready() && !quit_sent) {
                    engine->cancel();
                    engine->quit();
                    quit_sent = true;
                }
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
        try {
            lt::clean_device(device, confirmed && !storage_failed && exit_code == 0);
        } catch (const std::exception &e) {
            std::lock_guard<std::mutex> lock(mutex);
            fault = e.what();
            storage_failed = true;
        }
        device->active = false;
        publisher_running = false;
    }
};
extern "C" {
lt_status lt_engine_board(const char *library, const char *board, lt_board_info *out, lt_error *e) {
    return lt::guard(e, [&] {
        lt::abi(out);
        lt::require(board, "board required");
        lt::Engine engine(library);
        auto f = engine.find_board(board);
        static_assert(sizeof(lt_board_info) == sizeof(QemuIosPortableBoard));
        memcpy(out, &f, sizeof f);
    });
}
lt_status lt_runtime_create(const lt_runtime_config *c, lt_runtime **out, lt_error *e) {
    return lt::guard(e, [&] {
        lt::abi(c);
        lt::abi(&c->boot);
        lt::require(out && c->device, "runtime requires leased device");
        *out = nullptr;
        lt::require(!c->device->active, "device already active", LT_BUSY);
        lt::require(!lt::process_started, "engine process already consumed: restart in fresh process", LT_BUSY);
        auto runtime = std::make_unique<lt_runtime>(c->device);
        runtime->engine = std::make_unique<lt::Engine>(c->engine_library);
        auto facts = runtime->engine->find_board(c->device->lock.at("board").string());
        lt_boot_config boot = c->boot;
        if (boot.machine)
            lt::require(std::string(boot.machine) == facts.machine, "machine does not match prepared base");
        else
            boot.machine = facts.machine;
        int argc = 0;
        const char *const *argv = nullptr;
        lt_error nested{sizeof(nested), 1, 0, {}};
        lt_status status = lt_device_boot_argv(c->device, &boot, &argc, &argv, &nested);
        lt::require(status == LT_OK, nested.message, status);
        for (int i = 0; i < argc; i++)
            runtime->argv.push_back(const_cast<char *>(argv[i]));
        runtime->argv.push_back(nullptr);
        runtime->audio_enabled = boot.audio_enabled;
#ifdef SYS_memfd_create
        runtime->frame_fd = int(syscall(SYS_memfd_create, "lighttouch-frame", MFD_CLOEXEC | MFD_ALLOW_SEALING));
#else
 runtime->frame_fd=-1;errno=ENOSYS;
#endif
        lt::io(runtime->frame_fd >= 0, "private frame memfd");
        runtime->mapping_bytes = sizeof(lt_shared_frame) + LT_FRAME_CAPACITY;
        lt::io(ftruncate(runtime->frame_fd, off_t(runtime->mapping_bytes)) == 0, "size frame memfd");
        runtime->shared = static_cast<lt_shared_frame *>(
            mmap(nullptr, runtime->mapping_bytes, PROT_READ | PROT_WRITE, MAP_SHARED, runtime->frame_fd, 0));
        if (runtime->shared == MAP_FAILED) {
            runtime->shared = nullptr;
            throw lt::Failure(LT_IO, "map frame memfd");
        }
        runtime->shared->size = sizeof(lt_shared_frame);
        runtime->shared->version = 1;
        runtime->shared->capacity = LT_FRAME_CAPACITY;
        runtime->scratch.resize(LT_FRAME_CAPACITY);
        lt::io(fcntl(runtime->frame_fd, F_ADD_SEALS, F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL) == 0,
               "seal frame size");
        *out = runtime.release();
    });
}
lt_status lt_runtime_start(lt_runtime *r, lt_error *e) {
    return lt::guard(e, [&] {
        lt::require(r, "null runtime");
        lt::require(!lt::process_started, "one engine start per process; use fresh worker", LT_BUSY);
        lt::atomic_write(r->device->state / ".dirty", "schema=1\nbase=" + r->device->base_digest + "\n");
        r->device->dirty = true;
        lt::io(setenv("IT_TVOUT_READY", "1", 1) == 0, "set shared boot environment");
        bool expected = false;
        lt::require(lt::process_started.compare_exchange_strong(expected, true),
                    "one engine start per process; use fresh worker", LT_BUSY);
        r->device->active = true;
        r->started = true;
        // Start supervisor first. A guest thread creation failure leaves a terminal dirty
        // state; no destructor can block waiting on a guest without a supervisor.
        try {
            r->publisher_running = true;
            r->publisher = std::thread([r] { r->publish(); });
            r->guest = std::thread([r] {
                int result = r->engine->main(int(r->argv.size() - 1), r->argv.data());
                if (r->engine->storage_failed())
                    r->storage_failed = true;
                if (r->engine->shutdown_confirmed())
                    r->confirmed = true;
                r->exit_code = result;
                r->done = true;
            });
        } catch (const std::exception &x) {
            r->exit_code = -1;
            r->storage_failed = true;
            r->done = true;
            if (r->publisher.joinable())
                r->publisher.join();
            r->publisher_running = false;
            r->device->active = false;
            throw lt::Failure(LT_ENGINE, std::string("worker startup failed: ") + x.what());
        }
    });
}
lt_status lt_runtime_poll(lt_runtime *r, lt_runtime_status *out, lt_error *e) {
    return lt::guard(e, [&] {
        lt::require(r, "null runtime");
        lt::abi(out);
        bool failed = r->storage_failed || r->exit_code != 0;
        out->state = !r->started           ? LT_STOPPED
                     : failed              ? LT_FAILED
                     : r->done             ? (r->publisher_running ? LT_STOPPING : LT_STOPPED)
                     : r->stopping         ? LT_STOPPING
                     : !r->engine->ready() ? LT_STARTING
                     : r->engine->paused() ? LT_PAUSED
                                           : LT_RUNNING;
        out->guest_shutdown_confirmed = r->confirmed;
        out->storage_failed = r->storage_failed;
        out->dirty = r->device->dirty;
        out->engine_exit = r->exit_code;
        out->frames = r->frame_count;
        std::lock_guard<std::mutex> lock(r->mutex);
        if (!r->fault.empty())
            throw lt::Failure(LT_ENGINE, r->fault);
    });
}
lt_status lt_runtime_pause(lt_runtime *r, uint32_t paused, lt_error *e) {
    return lt::guard(e, [&] {
        lt::require(r && r->started && !r->done && !r->stopping, "runtime not active", LT_AGAIN);
        r->engine->cancel();
        if (paused)
            r->engine->pause();
        else
            r->engine->resume();
    });
}
lt_status lt_runtime_request_shutdown(lt_runtime *r, lt_error *e) {
    return lt::guard(e, [&] {
        lt::require(r && r->started && !r->done, "runtime not active", LT_AGAIN);
        r->engine->cancel();
        if (r->engine->paused())
            r->engine->resume();
        lt::require(r->engine->shutdown() > 0, "guest shutdown request was not admitted", LT_AGAIN);
        r->stopping = true;
    });
}
lt_status lt_runtime_destroy(lt_runtime *r, lt_error *e) {
    return lt::guard(e, [&] {
        lt::require(r, "null runtime");
        lt::require(!r->started || r->done, "wait for stop or terminate worker process", LT_BUSY);
        delete r;
    });
}
lt_status lt_runtime_copy_frame(lt_runtime *r, void *p, uint64_t capacity, uint64_t *seq, lt_frame_info *out,
                                lt_error *e) {
    return lt::guard(e, [&] {
        lt::require(r && p && seq, "invalid frame output");
        lt::abi(out);
        lt::require(capacity <= SIZE_MAX, "frame capacity overflow");
        QemuIosPortableFrame f{sizeof(f), 1, 0, 0, 0, 0, 0, 0, 0};
        int result = r->engine->frame(p, size_t(capacity), *seq, &f);
        if (result == QEMU_IOS_NO_FRAME)
            throw lt::Failure(LT_AGAIN, "no newer frame");
        if (result == QEMU_IOS_BUFFER_SMALL)
            throw lt::Failure(LT_INVALID, "frame buffer too small; requires " + std::to_string(f.required_bytes));
        lt::result(result);
        *out = {sizeof(*out), 1, f.width, f.height, f.stride, f.format, f.serial, f.timestamp_ns, f.required_bytes};
        *seq = f.serial;
    });
}
lt_status lt_runtime_frame_fd(lt_runtime *r, int *fd, uint64_t *bytes, lt_error *e) {
    return lt::guard(e, [&] {
        lt::require(r && fd && bytes, "invalid frame FD output");
        std::string path = "/proc/self/fd/" + std::to_string(r->frame_fd);
        int copy = open(path.c_str(), O_RDONLY | O_CLOEXEC);
        lt::io(copy >= 0, "readonly frame FD");
        *fd = copy;
        *bytes = r->mapping_bytes;
    });
}
lt_status lt_runtime_touch(lt_runtime *r, const lt_touch_batch *b, lt_error *e) {
    return lt::guard(e, [&] {
        lt::require(r && r->started && !r->done && !r->stopping, "input runtime inactive", LT_AGAIN);
        lt::abi(b);
        lt::require(b->count > 0 && b->count <= LT_MAX_CONTACTS, "invalid touch count");
        QemuIosPortableContact contacts[LT_MAX_CONTACTS]{};
        for (uint32_t i = 0; i < b->count; i++) {
            lt::abi(&b->contacts[i]);
            contacts[i] = {sizeof(contacts[i]), 1, b->contacts[i].id, b->contacts[i].phase, b->contacts[i].x,
                           b->contacts[i].y};
        }
        std::lock_guard<std::mutex> lock(r->mutex);
        lt::result(r->engine->touch(++r->input_id, b->timestamp_ns, contacts, b->count));
    });
}
lt_status lt_runtime_input_status(lt_runtime *r, lt_input_status *out, lt_error *e) {
    return lt::guard(e, [&] {
        lt::require(r, "null runtime");
        lt::abi(out);
        std::lock_guard<std::mutex> lock(r->mutex);
        out->request_id = r->input_id;
        out->engine_result = r->input_id ? r->engine->input_status(r->input_id) : QEMU_IOS_NOT_READY;
        out->state = !r->input_id                            ? 0
                     : out->engine_result == QEMU_IOS_QUEUED ? 1
                     : out->engine_result == QEMU_IOS_OK     ? 2
                                                             : 3;
    });
}
lt_status lt_runtime_cancel_input(lt_runtime *r, lt_error *e) {
    return lt::guard(e, [&] {
        lt::require(r, "null runtime");
        lt::result(r->engine->cancel());
        std::lock_guard<std::mutex> lock(r->mutex);
        r->last_motion = 0;
    });
}
lt_status lt_runtime_button(lt_runtime *r, uint32_t button, uint32_t down, lt_error *e) {
    return lt::guard(e, [&] {
        lt::require(r && r->started && !r->done && !r->stopping, "button runtime inactive", LT_AGAIN);
        lt::require(button <= 3 && down <= 1, "invalid hardware button");
        r->engine->button(int(button), down != 0);
    });
}
lt_status lt_runtime_motion(lt_runtime *r, const lt_motion *m, lt_error *e) {
    return lt::guard(e, [&] {
        lt::require(r && r->started && !r->done && !r->stopping && !r->engine->paused(), "motion inactive", LT_AGAIN);
        lt::abi(m);
        uint64_t now = lt::now_ns();
        lt::require(m->timestamp_ns <= now && now - m->timestamp_ns <= 250000000, "stale/future motion", LT_AGAIN);
        std::lock_guard<std::mutex> lock(r->mutex);
        lt::require(!r->last_motion || m->timestamp_ns > r->last_motion + 5000000, "motion must increase and <=200Hz",
                    LT_AGAIN);
        QemuIosPortableMotion sample{};
        static_assert(sizeof(sample) == sizeof(*m));
        memcpy(&sample, m, sizeof sample);
        lt::result(r->engine->motion(&sample));
        r->last_motion = m->timestamp_ns;
    });
}
lt_status lt_map_motion(uint32_t rotation, uint64_t timestamp, uint64_t source_now, const lt_motion *raw,
                        lt_motion *out, lt_error *e) {
    return lt::guard(e, [&] {
        lt::abi(raw);
        lt::abi(out);
        lt::require(rotation == 0 || rotation == 90 || rotation == 180 || rotation == 270, "invalid motion rotation");
        lt::require(timestamp <= source_now && source_now - timestamp <= 250000000, "stale adapter motion", LT_AGAIN);
        lt_motion mapped = *raw;
        uint64_t now = lt::now_ns();
        lt::require(now >= source_now - timestamp, "invalid clock conversion");
        mapped.timestamp_ns = now - (source_now - timestamp);
        auto convert = [&](const double *in, double *dest) {
            double x = in[0], y = in[1], z = in[2];
            lt::require(std::isfinite(x) && std::isfinite(y) && std::isfinite(z), "nonfinite motion");
            switch (rotation) {
            case 0:
                dest[0] = x;
                dest[1] = y;
                break;
            case 90:
                dest[0] = -y;
                dest[1] = x;
                break;
            case 180:
                dest[0] = -x;
                dest[1] = -y;
                break;
            case 270:
                dest[0] = y;
                dest[1] = -x;
                break;
            }
            dest[2] = z;
        };
        convert(raw->acceleration, mapped.acceleration);
        convert(raw->angular_velocity, mapped.angular_velocity);
        *out = mapped;
    });
}
lt_status lt_map_contact(double px, double py, double left, double top, double width, double height, uint32_t rotation,
                         double *x, double *y) {
    if (!x || !y || !std::isfinite(px) || !std::isfinite(py) || !std::isfinite(left) || !std::isfinite(top) ||
        !std::isfinite(width) || !std::isfinite(height) || width <= 0 || height <= 0)
        return LT_INVALID;
    double nx = (px - left) / width, ny = (py - top) / height;
    if (nx < 0 || nx > 1 || ny < 0 || ny > 1)
        return LT_AGAIN;
    switch (rotation) {
    case 0:
        *x = nx;
        *y = ny;
        break;
    case 90:
        *x = ny;
        *y = 1 - nx;
        break;
    case 180:
        *x = 1 - nx;
        *y = 1 - ny;
        break;
    case 270:
        *x = 1 - ny;
        *y = nx;
        break;
    default:
        return LT_INVALID;
    }
    return LT_OK;
}
lt_status lt_runtime_pcm(lt_runtime *r, int16_t *out, uint32_t capacity, lt_pcm_info *info, lt_error *e) {
    return lt::guard(e, [&] {
        lt::require(r && out, "invalid PCM output");
        lt::abi(info);
        lt::require(r->audio_enabled, "audio disabled", LT_UNSUPPORTED);
        lt::require(capacity >= 4096, "PCM capacity needs 4096 stereo frames");
        std::lock_guard<std::mutex> lock(r->mutex);
        lt::require(!r->pcm.empty(), "no PCM ready", LT_AGAIN);
        auto &p = r->pcm.front();
        memcpy(out, p.samples.data(), p.bytes);
        *info = {sizeof(*info), 1, 44100, 2, p.bytes / 4, 0, p.timestamp, r->pcm_dropped};
        r->pcm.pop_front();
    });
}
}
