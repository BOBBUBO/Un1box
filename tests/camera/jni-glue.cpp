/* What the Android camera's JNI calls do with one ImageReader frame.
 *
 * The three entry points are sliced out of Android/app/src/main/cpp/lighttouch_jni.cpp,
 * so the plane conversion, the bounds check and the return codes are the
 * shipped ones; only the JNI environment and the portable API behind them are
 * stubs, and both are recorded so the test can look at exactly what crossed the
 * boundary.
 *
 * The conversion is checked on both layouts a phone actually reports, because
 * getting it wrong is the classic way a camera preview comes out sheared or
 * striped: a planar YUV_420_888 (pixel stride 1, three buffers) and a
 * semi-planar one (pixel stride 2, the V plane being the U plane one byte in).
 * Row strides are deliberately larger than the picture, as a real device's are.
 *
 * SLICE ./Android/app/src/main/cpp/lighttouch_jni.cpp fn Java_org_lighttouch_android_Native_cameraFrame Java_org_lighttouch_android_Native_cameraFlush Java_org_lighttouch_android_Native_cameraInfo
 * CFLAGS -Wall -Werror -Wno-missing-field-initializers -Wno-unused-parameter
 */
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <vector>

#include "Android/app/src/main/cpp/camera_planes.h"
#include "Portable/include/lighttouch.h"

/* The JNI surface these three functions touch, no more. A direct buffer is what
 * GetDirectBufferAddress() returns and its capacity, which is how one ByteBuffer
 * sliced one byte in looks from here. */
typedef int32_t jint;
typedef int64_t jlong;
typedef int32_t jsize;
struct _jobject {};
typedef _jobject *jobject;
struct DirectBuffer {
    const uint8_t *base;
    size_t capacity;
};
struct LongArray { std::vector<jlong> values; };
typedef LongArray *jlongArray;

struct JNIEnv {
    void *GetDirectBufferAddress(jobject buffer) { return const_cast<uint8_t *>(reinterpret_cast<DirectBuffer *>(buffer)->base); }
    jlong GetDirectBufferCapacity(jobject buffer) { return jlong(reinterpret_cast<DirectBuffer *>(buffer)->capacity); }
    jlongArray NewLongArray(jsize length) {
        LongArray *array = new LongArray();
        array->values.assign(size_t(length), 0);
        return array;
    }
    void SetLongArrayRegion(jlongArray array, jsize start, jsize length, const jlong *from) {
        std::memcpy(array->values.data() + start, from, size_t(length) * sizeof(jlong));
    }
};
#define JNI(name) extern "C"
#define JNICALL

struct lt_runtime { int unused; };
static lt_runtime runtime_object;
static lt_runtime *runtime = nullptr;
static std::mutex guard;

/* What the portable layer was asked to do. */
struct CameraCalls {
    int writes = 0, flushes = 0, infos = 0;
    int sensor = -1, width = 0, height = 0;
    std::vector<uint8_t> frame;
    lt_status write_result = LT_OK;
} calls;

extern "C" lt_status lt_runtime_camera_write(lt_runtime *, int sensor, const uint8_t *nv12, uint32_t bytes,
                                             uint32_t width, uint32_t height, lt_error *) {
    calls.writes++;
    calls.sensor = sensor;
    calls.width = int(width);
    calls.height = int(height);
    calls.frame.assign(nv12, nv12 + bytes);
    return calls.write_result;
}
extern "C" lt_status lt_runtime_camera_info(lt_runtime *, int sensor, lt_camera_info *info, lt_error *) {
    calls.infos++;
    if (!info || info->size < sizeof(*info)) return LT_INVALID;
    info->sensor = uint32_t(sensor);
    info->width = 640;
    info->height = 480;
    info->pushed_frames = 9001;
    info->taken_frames = 8002;
    info->replaced_frames = 999;
    info->rejected_frames = 4;
    return LT_OK;
}
extern "C" lt_status lt_runtime_camera_flush(lt_runtime *, lt_error *) {
    calls.flushes++;
    return LT_OK;
}

extern "C" jint Java_org_lighttouch_android_Native_cameraFrame(JNIEnv *, jobject, jobject, jint, jint, jobject, jint,
                                                              jint, jobject, jint, jint, jint, jint, jint);
extern "C" void Java_org_lighttouch_android_Native_cameraFlush(JNIEnv *, jobject);
extern "C" jlongArray Java_org_lighttouch_android_Native_cameraInfo(JNIEnv *, jobject, jint);

namespace {
void reset() {
    calls = CameraCalls();
    runtime = &runtime_object;
}

/* One picture's worth of planes, laid out the way the layout says, with a row
 * stride four bytes wider than the picture so a wrong stride reading shows. */
struct Planes {
    std::vector<uint8_t> y, u, v;
    DirectBuffer yBuffer, uBuffer, vBuffer;
    int yRow, yPixel, uRow, uPixel, vRow, vPixel;
    int width, height;

    /* planar: three buffers, one byte per sample -- what a phone reports as YUV_420_888 with
     * pixel stride 1. semiPlanar: U and V interleaved in one buffer with pixel stride 2, so V's
     * address is U's one byte further on and its capacity one byte smaller, which is exactly what
     * ImageReader hands out for a semi-planar camera. */
    Planes(int w, int h, bool semiPlanar) : width(w), height(h) {
        yRow = w + 4;
        yPixel = 1;
        uRow = semiPlanar ? w + 4 : w / 2 + 4;
        uPixel = semiPlanar ? 2 : 1;
        vRow = uRow;
        vPixel = uPixel;

        /* The padding is a value the picture never uses, so a stray read of it is visible. */
        y.assign(size_t(yRow) * size_t(h), 0xee);
        u.assign(size_t(uRow) * size_t(h / 2), 0xdd);
        for (int row = 0; row < h; row++) {
            for (int x = 0; x < w; x++) y[size_t(row) * size_t(yRow) + size_t(x)] = uint8_t(10 + row * 8 + x);
        }
        for (int row = 0; row < h / 2; row++) {
            for (int x = 0; x < w / 2; x++) {
                uint8_t *pair = &u[size_t(row) * size_t(uRow) + size_t(x) * size_t(uPixel)];
                pair[0] = uint8_t(0x50 + x);            /* Cb */
                if (semiPlanar) {
                    pair[1] = uint8_t(0x90 + x);        /* Cr, interleaved in the same buffer */
                }
            }
        }
        if (!semiPlanar) {
            v.assign(size_t(vRow) * size_t(h / 2), 0xdd);
            for (int row = 0; row < h / 2; row++) {
                for (int x = 0; x < w / 2; x++) {
                    v[size_t(row) * size_t(vRow) + size_t(x)] = uint8_t(0x60 + x);   /* Cr, its own buffer */
                }
            }
        }

        yBuffer = { y.data(), y.size() };
        uBuffer = { u.data(), u.size() };
        vBuffer = semiPlanar ? DirectBuffer{ u.data() + 1, u.size() - 1 } : DirectBuffer{ v.data(), v.size() };
    }
};

jint frame(JNIEnv &env, Planes &p, int sensor) {
    return Java_org_lighttouch_android_Native_cameraFrame(&env, nullptr,
        reinterpret_cast<jobject>(&p.yBuffer), jint(p.yRow), jint(p.yPixel),
        reinterpret_cast<jobject>(&p.uBuffer), jint(p.uRow), jint(p.uPixel),
        reinterpret_cast<jobject>(&p.vBuffer), jint(p.vRow), jint(p.vPixel),
        jint(p.width), jint(p.height), jint(sensor));
}
}  // namespace

int main(void)
{
    JNIEnv env;
    {/* A planar frame: the picture is what crossed the boundary, in NV12, row by row. */
        reset();
        Planes p(4, 2, false);
        assert(frame(env, p, LT_CAMERA_BACK) == jint(lt::nv12Bytes(4, 2)));
        assert(calls.writes == 1);
        assert(calls.sensor == LT_CAMERA_BACK && calls.width == 4 && calls.height == 2);
        assert(calls.frame.size() == lt::nv12Bytes(4, 2));
        /* Y rows without the row padding. */
        for (int x = 0; x < 4; x++) {
            assert(calls.frame[size_t(x)] == uint8_t(10 + x));
            assert(calls.frame[4 + size_t(x)] == uint8_t(10 + 8 + x));
        }
        /* Then one CbCr pair per two pixels, from U and V separately. */
        for (int x = 0; x < 2; x++) {
            assert(calls.frame[4 * 2 + size_t(x) * 2] == uint8_t(0x50 + x));
            assert(calls.frame[4 * 2 + size_t(x) * 2 + 1] == uint8_t(0x60 + x));
        }
    }
    {/* A semi-planar frame: stride 2, V one byte into U's buffer. The classic wrong
     * answer here is reading U twice or reading past the shared buffer's end. */
        reset();
        Planes p(4, 2, true);
        assert(frame(env, p, LT_CAMERA_FRONT) == jint(lt::nv12Bytes(4, 2)));
        assert(calls.sensor == LT_CAMERA_FRONT);
        for (int x = 0; x < 2; x++) {
            assert(calls.frame[4 * 2 + size_t(x) * 2] == uint8_t(0x50 + x));
            assert(calls.frame[4 * 2 + size_t(x) * 2 + 1] == uint8_t(0x90 + x));
        }
        /* The luma survived too, and no plane's padding did. */
        assert(calls.frame[0] == 10 && calls.frame[4] == 18);
    }
    {/* A buffer that cannot hold the frame is refused, not read past. */
        reset();
        Planes p(4, 2, false);
        DirectBuffer shortY = { p.y.data(), p.y.size() - 8 };
        DirectBuffer shortU = { p.u.data(), 1 };
        const jint refused = Java_org_lighttouch_android_Native_cameraFrame(&env, nullptr,
            reinterpret_cast<jobject>(&shortY), jint(p.yRow), jint(p.yPixel),
            reinterpret_cast<jobject>(&p.uBuffer), jint(p.uRow), jint(p.uPixel),
            reinterpret_cast<jobject>(&p.vBuffer), jint(p.vRow), jint(p.vPixel),
            jint(p.width), jint(p.height), jint(LT_CAMERA_BACK));
        assert(refused == -jint(LT_INVALID));
        assert(calls.writes == 0);
        const jint refusedChroma = Java_org_lighttouch_android_Native_cameraFrame(&env, nullptr,
            reinterpret_cast<jobject>(&p.yBuffer), jint(p.yRow), jint(p.yPixel),
            reinterpret_cast<jobject>(&shortU), jint(p.uRow), jint(p.uPixel),
            reinterpret_cast<jobject>(&p.vBuffer), jint(p.vRow), jint(p.vPixel),
            jint(p.width), jint(p.height), jint(LT_CAMERA_BACK));
        assert(refusedChroma == -jint(LT_INVALID));
        assert(calls.writes == 0);
    }
    {/* A frame it cannot represent at all: odd width, no buffers. */
        reset();
        Planes p(4, 2, false);
        assert(Java_org_lighttouch_android_Native_cameraFrame(&env, nullptr,
            reinterpret_cast<jobject>(&p.yBuffer), jint(p.yRow), jint(p.yPixel),
            reinterpret_cast<jobject>(&p.uBuffer), jint(p.uRow), jint(p.uPixel),
            reinterpret_cast<jobject>(&p.vBuffer), jint(p.vRow), jint(p.vPixel),
            3, 2, jint(LT_CAMERA_BACK)) == -jint(LT_INVALID));
        assert(Java_org_lighttouch_android_Native_cameraFrame(&env, nullptr,
            nullptr, jint(p.yRow), jint(p.yPixel),
            reinterpret_cast<jobject>(&p.uBuffer), jint(p.uRow), jint(p.uPixel),
            reinterpret_cast<jobject>(&p.vBuffer), jint(p.vRow), jint(p.vPixel),
            jint(p.width), jint(p.height), jint(LT_CAMERA_BACK)) == -jint(LT_INVALID));
        assert(calls.writes == 0);
    }
    {/* The engine's answers: a frame nobody wanted (LT_AGAIN) is a dropped frame, 0. */
        reset();
        Planes p(4, 2, false);
        calls.write_result = LT_AGAIN;
        assert(frame(env, p, LT_CAMERA_BACK) == 0);
        /* Anything else keeps its status, negated, so the capture can stop and say why. */
        reset();
        calls.write_result = LT_UNSUPPORTED;
        assert(frame(env, p, LT_CAMERA_BACK) == -jint(LT_UNSUPPORTED));
        /* Nothing is called before the guest's engine exists. */
        reset();
        runtime = nullptr;
        assert(frame(env, p, LT_CAMERA_BACK) == 0);
        assert(calls.writes == 0);
        Java_org_lighttouch_android_Native_cameraFlush(&env, nullptr);
        assert(calls.flushes == 0);
    }
    {/* The counters come back in this order: status, sensor, width, height, pushed,
     * taken, replaced, rejected. */
        reset();
        LongArray *info = Java_org_lighttouch_android_Native_cameraInfo(&env, nullptr, jint(LT_CAMERA_FRONT));
        assert(info->values.size() == 8);
        assert(info->values[0] == jlong(LT_OK));
        assert(info->values[1] == jlong(LT_CAMERA_FRONT));
        assert(info->values[2] == 640);
        assert(info->values[3] == 480);
        assert(info->values[4] == 9001);
        assert(info->values[5] == 8002);
        assert(info->values[6] == 999);
        assert(info->values[7] == 4);
        delete info;
        /* A stopped runtime answers LT_AGAIN with zeroed counters instead of inventing values. */
        reset();
        runtime = nullptr;
        info = Java_org_lighttouch_android_Native_cameraInfo(&env, nullptr, jint(LT_CAMERA_BACK));
        assert(info->values[0] == jlong(LT_AGAIN));
        assert(info->values[2] == 0 && info->values[7] == 0);
        delete info;
    }
    {/* Stopping the capture drops what the guest has not taken. */
        reset();
        Java_org_lighttouch_android_Native_cameraFlush(&env, nullptr);
        assert(calls.flushes == 1);
    }
    std::puts("JNI glue: planar and semi-planar frames become one NV12 frame, and every status maps to the capture's contract");
    return 0;
}
