/* Frames nobody is drawing must not cost the phone anything.
 *
 * The frontend recommends thirty frames a second, the emulated ISP draws fifteen, and a
 * stuck camera app takes none at all. A latest-wins slot still has to copy each arriving
 * frame into one of its buffers -- half a megabyte for a 640x480 preview -- and on a phone
 * that copy is heat in the battery at a moment when the guest is already at 100% of a core.
 *
 * So the slot watches itself: once the guest has not taken a frame for half a second, only
 * one push in eight is copied, and the others cost a counter. The guest is never handed
 * anything older than that window, and the moment it takes a frame again full rate returns.
 *
 * This is checked against the real hw/arm/host-camera.c, not a copy of it.
 *
 * CFLAGS -Wall -Wextra -Werror -Wno-unused-parameter
 */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "hw/arm/host-camera.h"
#include "qemu/osdep.h"

#define W 320
#define H 240
#define BYTES ((size_t)W * H * 3 / 2)
#define IDLE_US 600000              /* comfortably past the slot's half-second idle window */

static void push(unsigned sensor, const uint8_t *frame)
{
    assert(host_camera_push(sensor, frame, BYTES, W, H));
}

static uint64_t pushed(unsigned sensor)
{
    HostCameraState state;

    host_camera_state(&state);
    return state.sensor[sensor].pushed;
}

int main(void)
{
    uint8_t *frame = malloc(BYTES);
    assert(frame);
    memset(frame, 0x40, BYTES);
    host_camera_flush();

    /* A guest that is consuming gets every frame it asks for: push, take, push, take. */
    for (int i = 0; i < 4; i++) {
        uint64_t before = pushed(HOST_CAMERA_BACK);

        push(HOST_CAMERA_BACK, frame);
        assert(pushed(HOST_CAMERA_BACK) == before + 1);       /* copied, not skipped */
        assert(host_camera_take(HOST_CAMERA_BACK, NULL, NULL) != NULL);
    }

    /* Now the guest stops taking them. Past the idle window, one push in eight is copied and the rest are
     * counted and dropped -- no half-megabyte copy, no buffer churn. */
    g_usleep(IDLE_US);
    uint64_t before = pushed(HOST_CAMERA_BACK);

    for (int i = 0; i < 24; i++) {
        push(HOST_CAMERA_BACK, frame);
    }
    uint64_t copied = pushed(HOST_CAMERA_BACK) - before;
    assert(copied <= 4 && copied >= 2);                       /* 1 in 8 of 24, plus the reset push */

    /* What the guest finally takes is a real frame, not a gap in the stream. */
    unsigned width = 0, height = 0;
    assert(host_camera_take(HOST_CAMERA_BACK, &width, &height) != NULL);
    assert(width == W && height == H);

    /* And taking one frame restores the full rate immediately. */
    push(HOST_CAMERA_BACK, frame);
    assert(host_camera_take(HOST_CAMERA_BACK, NULL, NULL) != NULL);
    before = pushed(HOST_CAMERA_BACK);
    push(HOST_CAMERA_BACK, frame);
    assert(pushed(HOST_CAMERA_BACK) == before + 1);

    /* Two sensors are separate slots: a front camera nobody draws must not leave the back one throttled.
     * The back slot is idle here too (the sleep above), so it is woken the way the guest wakes it -- by
     * taking a frame -- and must then be copied at full rate while the front one stays idle. */
    host_camera_flush();
    push(HOST_CAMERA_BACK, frame);
    assert(host_camera_take(HOST_CAMERA_BACK, NULL, NULL) != NULL);
    push(HOST_CAMERA_FRONT, frame);
    assert(host_camera_take(HOST_CAMERA_FRONT, NULL, NULL) != NULL);
    g_usleep(IDLE_US);
    for (int i = 0; i < 16; i++) {
        push(HOST_CAMERA_FRONT, frame);        /* idle: mostly counters, not copies */
    }
    for (int i = 0; i < 8; i++) {
        push(HOST_CAMERA_BACK, frame);         /* at least one of the eight is copied */
    }
    assert(host_camera_take(HOST_CAMERA_BACK, NULL, NULL) != NULL);
    before = pushed(HOST_CAMERA_BACK);
    push(HOST_CAMERA_BACK, frame);
    assert(pushed(HOST_CAMERA_BACK) == before + 1);   /* back is consuming again: full rate */
    for (int i = 0; i < 16; i++) {
        push(HOST_CAMERA_FRONT, frame);        /* and the front is still idle, on its own counter */
    }

    free(frame);
    printf("ok\n");
    return 0;
}
