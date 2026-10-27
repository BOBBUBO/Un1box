/* The piece of qemu/osdep.h the camera model and hw/arm/host-camera.c use, and
 * no more: glib's allocation macros, the fixed-width types, MIN/ARRAY_SIZE and
 * the handful of QEMU's own conveniences. The real header brings all of glib and
 * config-host.h; this one is small enough to read, so the model's own file can be
 * compiled whole on a machine with no engine build. */
#ifndef CAMERA_TEST_OSDEP_H
#define CAMERA_TEST_OSDEP_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint64_t hwaddr;
/* glib's monotonic clock, which qemu/osdep.h brings in whole and host-camera.c uses to rate-limit pushes
 * while nothing is consuming them. */
typedef int64_t gint64;
/* glib's monotonic clock and sleep, which qemu/osdep.h brings in whole and host-camera.c uses to rate-limit
 * pushes while nothing is consuming them. The real engine links glib; this stub is enough to run the model
 * and the camera slot on a plain host compiler. */
#include <time.h>
static inline int64_t g_get_monotonic_time(void)
{
    struct timespec now;

    clock_gettime(CLOCK_MONOTONIC, &now);
    return (int64_t)now.tv_sec * 1000000 + now.tv_nsec / 1000;
}
static inline void g_usleep(unsigned long microseconds)
{
    struct timespec pause = { (time_t)(microseconds / 1000000), (long)(microseconds % 1000000) * 1000 };

    nanosleep(&pause, NULL);
}
/* The two memory regions are members of the device, so they need a body here. */
typedef struct MemoryRegion {
    int unused;
} MemoryRegion;
/* The ops table the model fills in, and the little of its layout it touches. */
enum { DEVICE_LITTLE_ENDIAN = 0, DEVICE_BIG_ENDIAN = 1, DEVICE_NATIVE_ENDIAN = 2 };

typedef struct MemoryRegionOps {
    uint64_t (*read)(void *opaque, hwaddr addr, unsigned size);
    void (*write)(void *opaque, hwaddr addr, uint64_t data, unsigned size);
    int endianness;
    struct {
        unsigned min_access_size;
        unsigned max_access_size;
    } valid;
} MemoryRegionOps;
typedef struct DeviceState DeviceState;
typedef struct Object Object;
typedef struct QEMUTimer QEMUTimer;

/* QEMU marks the handlers' unused arguments with a bare (void) cast; the stub build does not
 * want -Wunused-parameter to fire on the engine's own style, so the flag is off in run.py. */
#define MIN(a, b) ((a) < (b) ? (a) : (b))
#define MAX(a, b) ((a) > (b) ? (a) : (b))
#define ARRAY_SIZE(x) (sizeof(x) / sizeof((x)[0]))

static inline void *camera_test_realloc(void *block, size_t bytes)
{
    void *grown = realloc(block, bytes);

    if (bytes && !grown) {
        abort();
    }
    return grown;
}

#define g_renew(type, block, count) ((type *)camera_test_realloc((block), (count) * sizeof(type)))
#define g_new0(type, count) ((type *)calloc((count), sizeof(type)))
#define g_malloc(bytes) malloc(bytes)
#define g_realloc(block, bytes) camera_test_realloc((block), (bytes))
#define g_free(block) free(block)
#define G_GNUC_PRINTF(a, b) __attribute__((format(printf, a, b)))
void memory_region_init_io(MemoryRegion *mr, Object *owner, const MemoryRegionOps *ops, void *opaque,
                           const char *name, uint64_t size);
#define g_autofree
#define g_assert_not_reached() abort()
#endif
