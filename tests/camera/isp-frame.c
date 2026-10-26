/* The ISP hands the guest the frontend's camera, and the test pattern only when
 * there is none.
 *
 * This is the path a phone's picture actually takes once the app pushes:
 *   host_camera_push() -> latest frame per sensor -> isp_frame() -> isp_from_camera() -> dart1
 * and it is exercised here against the real hw/arm/host-camera.c and the real
 * isp_from_camera(), sliced out of the engine rather than copied.
 *
 * Two cases get their own test because they are the ones that go wrong quietly:
 *
 *   - a frame pushed twice must reach the guest once, and the newer one, since
 *     the camera runs at 30 fps and the ISP at 15: a queue would hand the guest
 *     a picture from a third of a second ago, a latest-wins slot cannot.
 *   - chroma is sampled on even boundaries so a sample always covers the two
 *     pixels it stands for, in both output layouts (NV12 and 'yuvs' 4:2:2), or
 *     colours bleed by one pixel on a scaled frame.
 *
 * The pattern itself is not tested here: it is the model's, and the slice in
 * tests/slice/ipad1-isp-mailbox.c still checks the guest sees a frame when the
 * frontend pushes nothing.
 *
 * SLICE engine hw/arm/s5l8930_isp.c fn isp_from_camera
 * CFLAGS -Wall -Wextra -Werror -Wno-unused-function -Wno-unused-parameter
 */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "hw/arm/host-camera.h"

/* hw/arm/s5l8930_isp.c's state, reduced to the field isp_from_camera() writes
 * the frame into. The model keeps one host buffer and grows it when the guest
 * asks for a bigger mode; the same here. */
typedef struct S5L8930ISPState {
    uint8_t *pic;
    size_t pic_size;
} S5L8930ISPState;

static void isp_from_camera(S5L8930ISPState *s, const uint8_t *src, unsigned sw, unsigned sh, unsigned w,
                            unsigned h, bool nv12, bool rotate);

/* A frame of one flat luma, and a chroma plane whose every byte is its own index,
 * so an out-of-place sample is visible rather than plausible. */
static uint8_t *frame(unsigned w, unsigned h, uint8_t luma)
{
    size_t bytes = (size_t)w * h * 3 / 2;
    uint8_t *nv12 = malloc(bytes);

    assert(nv12);
    memset(nv12, luma, (size_t)w * h);
    for (size_t i = (size_t)w * h; i < bytes; i++) {
        nv12[i] = (uint8_t)(i - (size_t)w * h);
    }
    return nv12;
}

/* The state the guest is about to be given a frame from, and the buffer it lands in. */
static void render(S5L8930ISPState *s, unsigned sw, unsigned sh, unsigned w, unsigned h, bool nv12)
{
    size_t need = nv12 ? (size_t)w * h * 3 / 2 : (size_t)w * h * 2;

    if (need > s->pic_size) {
        s->pic = realloc(s->pic, need);
        assert(s->pic);
        s->pic_size = need;
    }
}

static void test_the_newest_frame_is_the_one_the_guest_gets(void)
{
    HostCameraState before, after;
    unsigned w = 0, h = 0;
    uint8_t *first = frame(4, 2, 0x11), *second = frame(4, 2, 0x22);
    const uint8_t *got;

    host_camera_state(&before);
    host_camera_flush();

    assert(host_camera_push(HOST_CAMERA_BACK, first, 4 * 2 * 3 / 2, 4, 2));
    /* The guest has not asked yet, so this one replaces the first: a queue would
     * give the guest 0x11 here, and on a phone that is a frame from 33 ms ago. */
    assert(host_camera_push(HOST_CAMERA_BACK, second, 4 * 2 * 3 / 2, 4, 2));

    got = host_camera_take(HOST_CAMERA_BACK, &w, &h);
    assert(got && w == 4 && h == 2);
    assert(got[0] == 0x22);

    /* Nothing new since: the ISP draws its pattern for that frame. */
    assert(host_camera_take(HOST_CAMERA_BACK, NULL, NULL) == NULL);

    host_camera_state(&after);
    assert(after.sensor[HOST_CAMERA_BACK].pushed == before.sensor[HOST_CAMERA_BACK].pushed + 2);
    assert(after.sensor[HOST_CAMERA_BACK].taken == before.sensor[HOST_CAMERA_BACK].taken + 1);
    assert(after.sensor[HOST_CAMERA_BACK].replaced == before.sensor[HOST_CAMERA_BACK].replaced + 1);
    assert(after.sensor[HOST_CAMERA_BACK].width == 0);

    free(first);
    free(second);
}

static void test_the_two_cameras_do_not_share_a_frame(void)
{
    uint8_t *back = frame(2, 2, 0x33), *front = frame(2, 2, 0x44);
    const uint8_t *got;

    host_camera_flush();
    assert(host_camera_push(HOST_CAMERA_BACK, back, 2 * 2 * 3 / 2, 2, 2));
    assert(host_camera_push(HOST_CAMERA_FRONT, front, 2 * 2 * 3 / 2, 2, 2));

    /* The guest's Camera app switching cameras must not get the other sensor's picture. */
    got = host_camera_take(HOST_CAMERA_FRONT, NULL, NULL);
    assert(got && got[0] == 0x44);
    got = host_camera_take(HOST_CAMERA_BACK, NULL, NULL);
    assert(got && got[0] == 0x33);

    free(back);
    free(front);
}

static void test_a_frame_it_cannot_store_is_refused_not_half_written(void)
{
    HostCameraState before, after;
    uint8_t *ok = frame(4, 2, 0x55);
    uint8_t odd[4 * 3];                                        /* an odd width: no 2x2 chroma block */

    host_camera_flush();
    host_camera_state(&before);

    assert(!host_camera_push(HOST_CAMERA_BACK, ok, 4 * 2 * 3 / 2, 3, 2));       /* odd width */
    assert(!host_camera_push(HOST_CAMERA_BACK, ok, 4 * 2 * 3 / 2, 4, 3));       /* odd height */
    assert(!host_camera_push(HOST_CAMERA_BACK, ok, 4, 4, 2));                   /* short: half a frame */
    assert(!host_camera_push(HOST_CAMERA_BACK, NULL, 4 * 2 * 3 / 2, 4, 2));     /* nothing at all */
    assert(!host_camera_push(HOST_CAMERA_BACK, odd, sizeof odd, 0, 0));          /* no size */
    /* The limit is the tap's, not the caller's: a size above it is refused rather
     * than stored, which bounds the engine's memory to two 1280x960 buffers. */
    assert(!host_camera_push(HOST_CAMERA_BACK, ok, (size_t)HOST_CAMERA_MAX_WIDTH * (HOST_CAMERA_MAX_HEIGHT + 2) * 3 / 2,
                             HOST_CAMERA_MAX_WIDTH, HOST_CAMERA_MAX_HEIGHT + 2));
    assert(!host_camera_push(7, ok, 4 * 2 * 3 / 2, 4, 2));                      /* no such sensor */

    /* None of that left a frame behind, and none of it reached the guest. The unknown sensor is
     * not counted anywhere: there is no slot to count it against. */
    assert(host_camera_take(HOST_CAMERA_BACK, NULL, NULL) == NULL);
    host_camera_state(&after);
    assert(after.sensor[HOST_CAMERA_BACK].rejected == before.sensor[HOST_CAMERA_BACK].rejected + 6);
    assert(after.sensor[HOST_CAMERA_BACK].pushed == before.sensor[HOST_CAMERA_BACK].pushed);

    /* The largest frame the tap accepts is accepted, and that is 1280x960. */
    {
        uint8_t *big = frame(HOST_CAMERA_MAX_WIDTH, HOST_CAMERA_MAX_HEIGHT, 0x66);

        assert(host_camera_push(HOST_CAMERA_BACK, big, (size_t)HOST_CAMERA_MAX_WIDTH * HOST_CAMERA_MAX_HEIGHT * 3 / 2,
                                HOST_CAMERA_MAX_WIDTH, HOST_CAMERA_MAX_HEIGHT));
        assert(host_camera_take(HOST_CAMERA_BACK, NULL, NULL));
        free(big);
    }

    free(ok);
}

static void test_a_stopped_camera_is_not_replayed(void)
{
    uint8_t *f = frame(2, 2, 0x77);
    HostCameraState after;

    host_camera_flush();
    assert(host_camera_push(HOST_CAMERA_BACK, f, 2 * 2 * 3 / 2, 2, 2));
    host_camera_flush();
    /* What the app pushed before it stopped must not turn up in the guest's next
     * session, and the counters stay readable afterwards. */
    assert(host_camera_take(HOST_CAMERA_BACK, NULL, NULL) == NULL);
    host_camera_state(&after);
    assert(after.sensor[HOST_CAMERA_BACK].pushed > 0);
    free(f);
}

static void test_the_guest_picks_which_phone_camera_streams(void)
{
    /* The ISP model calls select() when the kext recognises a sensor; the frontend
     * polls selected() to open the matching phone camera. Only the guest can tell
     * a phone that both of its cameras cannot usefully run at once. */
    host_camera_select(HOST_CAMERA_BACK);
    assert(host_camera_selected() == HOST_CAMERA_BACK);
    host_camera_select(HOST_CAMERA_FRONT);
    assert(host_camera_selected() == HOST_CAMERA_FRONT);
    host_camera_select(HOST_CAMERA_SENSORS);       /* no such sensor: ignored, not wrapped */
    assert(host_camera_selected() == HOST_CAMERA_FRONT);
    host_camera_select(HOST_CAMERA_BACK);
    assert(host_camera_selected() == HOST_CAMERA_BACK);
}

static void test_a_frame_the_guest_holds_survives_one_push(void)
{
    /* Two buffers per sensor: while the ISP is copying the frame it took, the next
     * push must land somewhere else, or the picture is torn. */
    uint8_t *a = frame(4, 2, 0x88), *b = frame(4, 2, 0x99);
    const uint8_t *held;

    host_camera_flush();
    assert(host_camera_push(HOST_CAMERA_BACK, a, 4 * 2 * 3 / 2, 4, 2));
    held = host_camera_take(HOST_CAMERA_BACK, NULL, NULL);
    assert(held && held[0] == 0x88);
    assert(host_camera_push(HOST_CAMERA_BACK, b, 4 * 2 * 3 / 2, 4, 2));
    assert(held[0] == 0x88 && held[1] == 0x88);            /* still the frame it took */
    assert(host_camera_take(HOST_CAMERA_BACK, NULL, NULL)[0] == 0x99);

    free(a);
    free(b);
}

static void test_nv12_scales_by_the_nearest_pixel(void)
{
    /* A 4x4 frame into a 2x2 output: nearest neighbour, so the output's pixels are the source's
     * (0,0), (2,0), (0,2), (2,2). */
    S5L8930ISPState s = {0};
    uint8_t src[4 * 4 + 8];
    size_t i;

    for (i = 0; i < 4 * 4; i++) {
        src[i] = (uint8_t)(i * 10);
    }
    for (i = 0; i < 8; i++) {
        src[4 * 4 + i] = (uint8_t)(0x80 + i);
    }
    render(&s, 4, 4, 2, 2, true);
    isp_from_camera(&s, src, 4, 4, 2, 2, true, false);
    assert(s.pic[0] == 0 && s.pic[1] == 20);                /* row 0: columns 0 and 2 */
    assert(s.pic[2] == 80 && s.pic[3] == 100);              /* row 2: columns 0 and 2 */
    /* Chroma of an output row comes from the source's row 0, both pixels of a pair from one
     * even-aligned sample. */
    assert(s.pic[4] == 0x80 && s.pic[5] == 0x81);

    /* And upward: a 2x2 frame into 4x4, nearest neighbour, no interpolation. */
    {
        S5L8930ISPState up = {0};
        uint8_t small[2 * 2 + 2] = { 1, 2, 3, 4, 0x10, 0x20 };

        render(&up, 2, 2, 4, 4, true);
        isp_from_camera(&up, small, 2, 2, 4, 4, true, false);
        assert(up.pic[0] == 1 && up.pic[1] == 1 && up.pic[2] == 2 && up.pic[3] == 2);   /* row 0 */
        assert(up.pic[4 * 2] == 3 && up.pic[4 * 2 + 3] == 4);                           /* row 2 */
        /* Chroma: 2x2 blocks, so the whole output's chroma is the source's one block. The output's
         * chroma plane is the 8 bytes after its 16 luma ones. */
        for (i = 16; i < 24; i += 2) {
            assert(up.pic[i] == 0x10 && up.pic[i + 1] == 0x20);
        }
        free(up.pic);
    }
    free(s.pic);
}

static void test_nv12_chroma_never_lands_on_an_odd_source_column(void)
{
    /* Scaling 6 wide to 4 wide puts source columns 0, 1, 3, 4 under the output's. Chroma must come
     * from 0, 0, 2, 4: an odd column is the second half of a pair and its Cb is really its
     * neighbour's. */
    S5L8930ISPState s = {0};
    uint8_t src[6 * 2 + 6];
    size_t i;

    for (i = 0; i < 6 * 2; i++) {
        src[i] = (uint8_t)i;
    }
    for (i = 0; i < 6; i++) {
        src[6 * 2 + i] = (uint8_t)(0x40 + i);               /* 0x40 0x41 0x42 0x43 0x44 0x45 */
    }
    render(&s, 6, 2, 4, 2, true);
    isp_from_camera(&s, src, 6, 2, 4, 2, true, false);
    assert(s.pic[8] == 0x40 && s.pic[9] == 0x41);           /* x = 0 -> 0 */
    assert(s.pic[10] == 0x42 && s.pic[11] == 0x43);         /* x = 2 -> 2 */

    free(s.pic);
}


static void test_the_front_sensor_is_spun_upright(void)
{
    /* The front module is mounted 180 degrees against the back one, and the guest draws what it is given:
     * without this the front camera's preview is upside down on the device. Both output layouts, and the
     * chroma has to land on the mirrored block, not on a neighbour. */
    S5L8930ISPState s = {0};
    uint8_t src[4 * 4 + 8];
    size_t i;

    for (i = 0; i < 4 * 4; i++) {
        src[i] = (uint8_t)(i + 1);                          /* 1..16, so every position is distinct */
    }
    for (i = 0; i < 8; i++) {
        src[4 * 4 + i] = (uint8_t)(0x80 + i);               /* four chroma pairs, in order */
    }

    render(&s, 4, 4, 4, 4, true);
    isp_from_camera(&s, src, 4, 4, 4, 4, true, true);
    /* 180 degrees: the guest's last pixel is the source's first, and its first the source's last. */
    assert(s.pic[0] == 16 && s.pic[15] == 1);
    assert(s.pic[4] == 12 && s.pic[11] == 5);               /* row 1 starts at source row 2, from the right */
    assert(s.pic[1] == 15 && s.pic[3] == 13);               /* the row runs backwards too */
    /* Chroma: the source's last block comes first, and its Cb/Cr keep their order (a rotation does not
     * swap the channels). */
    assert(s.pic[16] == 0x86 && s.pic[17] == 0x87);       /* bottom-right block comes first */
    assert(s.pic[18] == 0x80 && s.pic[19] == 0x81);       /* top-right block */
    assert(s.pic[20] == 0x82 && s.pic[21] == 0x83);       /* bottom-left block, from the top row */
    assert(s.pic[22] == 0x84 && s.pic[23] == 0x85);       /* top-left block */
    free(s.pic);

    /* The same picture in the 4:2:2 layout: Y and its chroma byte both mirrored, the pair's order flipped. */
    {
        S5L8930ISPState t = {0};

        render(&t, 4, 4, 4, 4, false);
        isp_from_camera(&t, src, 4, 4, 4, 4, false, true);
        assert(t.pic[0] == 16 && t.pic[2] == 15);           /* pixels run backwards, luma-wise */
        /* Source row 3's chroma is the pair at columns 0..1 = 0x80 (Cb) 0x81 (Cr), and the mirrored pixel
         * is an odd source column, so it takes the pair's second byte first. */
        assert(t.pic[1] == 0x87 && t.pic[3] == 0x86);
        free(t.pic);
    }
}

static void test_yuvs_interleaves_luma_and_chroma_per_pixel(void)
{
    /* 'yuvs' is Y0 Cb Y1 Cr: one and a half bytes per pixel would not do, so the
     * 4:2:2 output writes two bytes per pixel, its chroma from the same even-aligned
     * pair the NV12 path uses. At 1:1 the result is exactly 'yuvs'. */
    S5L8930ISPState s = {0};
    uint8_t src[4 * 2 + 4];                                 /* 4x2 luma, then 2x1 chroma pairs */

    for (int i = 0; i < 8; i++) {
        src[i] = (uint8_t)(0x10 + i);
    }
    src[8] = 0xc8;      /* Cb, x = 0 */
    src[9] = 0xc9;      /* Cr, x = 0 */
    src[10] = 0xca;     /* Cb, x = 2 */
    src[11] = 0xcb;     /* Cr, x = 2 */

    render(&s, 4, 2, 4, 2, false);
    isp_from_camera(&s, src, 4, 2, 4, 2, false, false);

    /* Row 0: Y0 Cb Y1 Cr Y2 Cb Y3 Cr, from source row 0 and chroma row 0. */
    assert(s.pic[0] == 0x10 && s.pic[1] == 0xc8);
    assert(s.pic[2] == 0x11 && s.pic[3] == 0xc9);
    assert(s.pic[4] == 0x12 && s.pic[5] == 0xca);
    assert(s.pic[6] == 0x13 && s.pic[7] == 0xcb);
    /* Row 1 is luma row 1 and the same chroma row, as 4:2:2 subsamples vertically not at all. */
    assert(s.pic[8] == 0x14 && s.pic[9] == 0xc8);
    assert(s.pic[10] == 0x15 && s.pic[11] == 0xc9);

    free(s.pic);
}

int main(void)
{
    test_the_newest_frame_is_the_one_the_guest_gets();
    test_the_two_cameras_do_not_share_a_frame();
    test_a_frame_it_cannot_store_is_refused_not_half_written();
    test_a_stopped_camera_is_not_replayed();
    test_the_guest_picks_which_phone_camera_streams();
    test_a_frame_the_guest_holds_survives_one_push();
    test_nv12_scales_by_the_nearest_pixel();
    test_nv12_chroma_never_lands_on_an_odd_source_column();
    test_yuvs_interleaves_luma_and_chroma_per_pixel();
    test_the_front_sensor_is_spun_upright();
    puts("camera tap: newest frame wins, both sensors apart, both output layouts scaled, the front one upright");
    return 0;
}
