/* The piece of qemu/osdep.h hw/arm/host-camera.c uses: nothing but glib's
 * g_renew(), which grows a sensor's buffer as frames arrive. The real header
 * brings all of glib; here it is one allocation macro, so the slot logic can be
 * checked without a build system. A frame that cannot be stored is a test bug,
 * not a runtime condition, so a failed reallocation aborts. */
#ifndef CAMERA_TEST_OSDEP_H
#define CAMERA_TEST_OSDEP_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

static inline void *camera_test_realloc(void *block, size_t bytes)
{
    void *grown = realloc(block, bytes);

    if (bytes && !grown) {
        abort();
    }
    return grown;
}

#define g_renew(type, block, count) ((type *)camera_test_realloc((block), (count) * sizeof(type)))
#endif
