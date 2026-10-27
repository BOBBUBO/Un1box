/*
 * The camera slots, against the real hw/arm/host-camera.c.
 *
 * What matters here is the contract the ISP depends on: the newest frame wins, an unread frame is counted as
 * replaced rather than queued, a take hands the frame over once, the two sensors never see each other's frames,
 * and a frame that is not a picture (odd, empty, absurd size) is refused instead of drawn.
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

int main(void)
{
    const unsigned W = 64, H = 48;
    const size_t need = (size_t)W * H * 3 / 2;
    uint8_t *frame = g_malloc(need);
    uint64_t pushed, taken, replaced;
    unsigned w = 0, h = 0;

    host_camera_reset();
    host_camera_counts(HOST_CAMERA_BACK, &pushed, &taken, &replaced);
    check(pushed == 0 && taken == 0 && replaced == 0, "a fresh device starts with no frames");
    check(host_camera_take(HOST_CAMERA_BACK, &w, &h) == NULL, "there is no frame before the phone sends one");
    check(host_camera_active() == HOST_CAMERA_BACK, "the guest starts on the back sensor");

    /* A first frame arrives and is taken exactly once. */
    fill(frame, need, 0x11);
    check(host_camera_push(HOST_CAMERA_BACK, frame, W, H), "a 64x48 frame is accepted");
    const uint8_t *got = host_camera_take(HOST_CAMERA_BACK, &w, &h);
    check(got != NULL, "the frame is there to be drawn");
    check(w == W && h == H, "its size comes back with it");
    check(got && got[0] == 0x11 && got[need - 1] == 0x11, "the whole frame is copied, not just the start");
    check(host_camera_take(HOST_CAMERA_BACK, &w, &h) == NULL, "a frame is drawn once, not every tick");

    /* The newest frame wins: what nobody drew is replaced, and said so. */
    fill(frame, need, 0x22);
    check(host_camera_push(HOST_CAMERA_BACK, frame, W, H), "a second frame is accepted");
    fill(frame, need, 0x33);
    check(host_camera_push(HOST_CAMERA_BACK, frame, W, H), "a third frame is accepted");
    got = host_camera_take(HOST_CAMERA_BACK, &w, &h);
    check(got && got[0] == 0x33, "the newest frame is the one drawn, not the oldest");
    host_camera_counts(HOST_CAMERA_BACK, &pushed, &taken, &replaced);
    check(pushed == 3 && taken == 2 && replaced == 1, "the counters say what happened: 3 sent, 2 drawn, 1 replaced");

    /* The sensors keep their own frames. */
    fill(frame, need, 0x44);
    check(host_camera_push(HOST_CAMERA_FRONT, frame, W, H), "the front sensor takes a frame too");
    check(host_camera_take(HOST_CAMERA_BACK, &w, &h) == NULL, "the back sensor does not get the front's frame");
    got = host_camera_take(HOST_CAMERA_FRONT, &w, &h);
    check(got && got[0] == 0x44, "the front sensor gets its own frame");

    /* The guest's switch. */
    host_camera_select(HOST_CAMERA_FRONT);
    check(host_camera_active() == HOST_CAMERA_FRONT, "the guest's switch moves the active sensor");
    host_camera_select(7);
    check(host_camera_active() == HOST_CAMERA_FRONT, "a sensor that does not exist does not become active");

    /* Frames that are not pictures. */
    check(!host_camera_push(HOST_CAMERA_BACK, frame, W - 1, H), "an odd width is refused");
    check(!host_camera_push(HOST_CAMERA_BACK, frame, W + 1, H), "an odd width is refused there too");
    check(!host_camera_push(HOST_CAMERA_BACK, frame, W, 0), "an empty frame is refused");
    check(!host_camera_push(HOST_CAMERA_BACK, frame, 4097, 4097), "an impossible size is refused");
    check(!host_camera_push(9, frame, W, H), "a sensor that does not exist is refused");
    check(!host_camera_push(HOST_CAMERA_BACK, NULL, W, H), "a missing frame is refused");

    /* A restart starts clean. */
    host_camera_reset();
    host_camera_counts(HOST_CAMERA_BACK, &pushed, &taken, &replaced);
    check(pushed == 0 && taken == 0 && replaced == 0, "a reset forgets the counters");
    check(host_camera_take(HOST_CAMERA_BACK, &w, &h) == NULL, "a reset forgets the frames");

    g_free(frame);
    if (failures) {
        printf("%d camera slot checks failed\n", failures);
        return 1;
    }
    printf("newest frame wins, one take per frame, sensors apart, impossible frames refused\n");
    return 0;
}
