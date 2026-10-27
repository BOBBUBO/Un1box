/*
 * The camera mailbox, against the real hw/arm/host-camera.c.
 *
 * What matters here is the contract the ISP depends on: a frame before the ISP exists is dropped rather than
 * held, the newest frame wins and an unread one is counted as replaced rather than queued, the ISP keeps the
 * picture until the app flushes it (a viewfinder does not blink back to a test pattern), and a frame that is not
 * a picture is refused instead of drawn.
 */
#include "qemu/osdep.h"
#include "hw/arm/host-camera.h"
#include <stdio.h>

static int failures;

static void check(bool ok, const char *what)
{
    if (!ok) {
        printf("FAIL %s\n", what);
        failures++;
    }
}

static void fill(uint8_t *frame, size_t bytes, uint8_t value)
{
    memset(frame, value, bytes);
}

static bool draws(const HostCameraFrame *frame)
{
    return frame->data != NULL;
}

int main(void)
{
    const unsigned W = 64, H = 48;
    const size_t need = (size_t)W * H * 3 / 2;
    uint8_t *frame = g_malloc(need);
    HostCameraFrame got;
    uint64_t pushed, taken, replaced;
    unsigned w = 0, h = 0;

    /* Before the ISP is there: a frame has nowhere to go, and saying so is not an error. */
    host_camera_flush();
    host_camera_snapshot(&got);
    check(!draws(&got), "there is no frame before the phone sends one");
    fill(frame, need, 0x11);
    check(host_camera_write(HOST_CAMERA_BACK, frame, W, H) == 0,
          "a frame written while no ISP exists is dropped, not held");

    /* With the device: the frame is kept, and the ISP draws it until the app replaces it. */
    host_camera_attach();
    check(host_camera_write(HOST_CAMERA_BACK, frame, W, H) == 1, "a 64x48 frame is accepted");
    host_camera_snapshot(&got);
    check(draws(&got), "the frame is there to be drawn");
    check(got.width == W && got.height == H && got.size == need, "its size comes back with it");
    check(got.data && got.data[0] == 0x11 && got.data[need - 1] == 0x11,
          "the whole frame is copied, not just the start");
    host_camera_snapshot(&got);
    check(draws(&got), "the picture stays until the app replaces it: a viewfinder does not blink");
    host_camera_counts(&pushed, &taken, &replaced, &w, &h);
    check(pushed == 1 && taken == 1 && replaced == 0, "one frame sent, one drawn, none replaced");
    check(w == W && h == H, "the size of the held picture is reported");

    /* The newest wins: what nobody drew is replaced, and the counters say so. */
    fill(frame, need, 0x22);
    check(host_camera_write(HOST_CAMERA_BACK, frame, W, H) == 1, "a second frame is accepted");
    fill(frame, need, 0x33);
    check(host_camera_write(HOST_CAMERA_BACK, frame, W, H) == 1, "a third frame is accepted");
    host_camera_counts(&pushed, &taken, &replaced, &w, &h);
    check(pushed == 3 && taken == 1 && replaced == 1, "3 sent, 1 drawn, 1 replaced: the middle one never drew");
    host_camera_snapshot(&got);
    check(got.data && got.data[0] == 0x33, "the newest frame is the one drawn, not the oldest");
    host_camera_counts(&pushed, &taken, &replaced, &w, &h);
    check(taken == 2, "the draw is counted once, when it happens");

    /* The guest's own switch is only a label: the picture keeps coming either way. */
    host_camera_select(HOST_CAMERA_FRONT);
    check(host_camera_active() == HOST_CAMERA_FRONT, "the guest's switch moves the named camera");
    host_camera_select(7);
    check(host_camera_active() == HOST_CAMERA_FRONT, "a camera that does not exist does not become the named one");
    fill(frame, need, 0x44);
    check(host_camera_write(HOST_CAMERA_FRONT, frame, W, H) == 1,
          "a frame from the phone's other camera is drawn too: one mailbox serves whichever the guest is on");
    host_camera_snapshot(&got);
    check(got.data && got.data[0] == 0x44 && got.sensor == HOST_CAMERA_FRONT,
          "the frame says which camera it came from");

    /* Frames that are not pictures. */
    check(host_camera_write(HOST_CAMERA_BACK, frame, W - 1, H) == -1, "an odd width is refused");
    check(host_camera_write(HOST_CAMERA_BACK, frame, W + 1, H) == -1, "an odd width is refused there too");
    check(host_camera_write(HOST_CAMERA_BACK, frame, W, 0) == -1, "an empty frame is refused");
    check(host_camera_write(HOST_CAMERA_BACK, frame, 4097, 4097) == -1, "an impossible size is refused");
    check(host_camera_write(HOST_CAMERA_BACK, frame, 2, 2) == -1, "a frame too small to be a viewfinder is refused");
    check(host_camera_write(HOST_CAMERA_BACK, NULL, W, H) == -1, "a missing frame is refused");

    /* A flush is the app closing the camera: the held picture goes and the pattern comes back. */
    host_camera_flush();
    host_camera_snapshot(&got);
    check(!draws(&got), "a flush forgets the picture");
    host_camera_counts(&pushed, &taken, &replaced, &w, &h);
    check(pushed == 0 && taken == 0 && replaced == 0 && w == 0 && h == 0, "a flush forgets the counters too");

    /* A restart starts clean. */
    host_camera_attach();
    host_camera_snapshot(&got);
    check(!draws(&got), "a fresh device starts with no frame");

    g_free(frame);
    if (failures) {
        printf("%d camera mailbox checks failed\n", failures);
        return 1;
    }
    printf("newest frame wins, drawn once per write, held until flushed, impossible frames refused\n");
    return 0;
}
