/*
 * The ISP's host-camera conversion, against the real (pure) header the device build uses.
 *
 * That header is where the phone's NV12 frame becomes the frame the guest's buffer holds, so it is the one place
 * a wrong stride or a wrong scale turns into a garbled picture rather than into an error. Checked here: the
 * layout of both output formats, nearest-neighbour scaling up and down, and that neither writes a byte past the
 * size the ISP allocated.
 */
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "hw/arm/s5l8930-isp-camera.h"

static int failures;

static void check(bool ok, const char *what)
{
    if (!ok) {
        printf("FAIL %s\n", what);
        failures++;
    }
}

/* One byte of slack on both sides: anything the conversion writes outside its frame shows up as a change. */
#define CANARY 0xa5
#define SLACK  64

static const uint8_t nv12_2x2[] = { 16, 32, 48, 64, 128, 192 };   /* four luma, then U/V of one pair */

int main(void)
{
    uint8_t out[SLACK + 4096 + SLACK];

    /* Same size, same format: the phone's frame is the guest's frame. */
    memset(out, CANARY, sizeof(out));
    s5l8930_isp_camera_convert(nv12_2x2, 2, 2, out + SLACK, 2, 2, true);
    check(memcmp(out + SLACK, nv12_2x2, sizeof(nv12_2x2)) == 0, "an NV12 frame of the same size is copied whole");
    check(out[SLACK + sizeof(nv12_2x2)] == CANARY && out[SLACK - 1] == CANARY, "nothing is written outside it");

    /* NV12 to the ISP's 4:2:2 output: Y Cb Y Cr, one chroma sample per pixel. */
    {
        const uint8_t expected[] = { 16, 128, 32, 192, 48, 128, 64, 192 };

        memset(out, CANARY, sizeof(out));
        s5l8930_isp_camera_convert(nv12_2x2, 2, 2, out + SLACK, 2, 2, false);
        check(memcmp(out + SLACK, expected, sizeof(expected)) == 0, "NV12 becomes Y Cb Y Cr with the pair's chroma");
        check(out[SLACK + sizeof(expected)] == CANARY, "4:2:2 writes two bytes a pixel and no more");
    }

    /* Twice the size, nearest neighbour: every source pixel becomes a 2x2 block. */
    {
        const uint8_t expected[] = {
            10, 10, 20, 20,
            10, 10, 20, 20,
            30, 30, 40, 40,
            30, 30, 40, 40,
            50, 60, 50, 60,
            50, 60, 50, 60,
        };
        const uint8_t input[] = { 10, 20, 30, 40, 50, 60 };

        memset(out, CANARY, sizeof(out));
        s5l8930_isp_camera_convert(input, 2, 2, out + SLACK, 4, 4, true);
        check(memcmp(out + SLACK, expected, sizeof(expected)) == 0, "scaling up repeats the nearest source pixel");
        check(out[SLACK + sizeof(expected)] == CANARY, "scaling up writes exactly one NV12 frame");
    }

    /* Half the size: 4x4 in, 2x2 out, and the top-left of each pair of source pixels wins. */
    {
        const uint8_t input[] = {
            1, 2, 3, 4,
            5, 6, 7, 8,
            9, 10, 11, 12,
            13, 14, 15, 16,
            17, 18, 19, 20,
            21, 22, 23, 24,
            25, 26, 27, 28,
            29, 30, 31, 32,
        };

        memset(out, CANARY, sizeof(out));
        s5l8930_isp_camera_convert(input, 4, 4, out + SLACK, 2, 2, true);
        /* Each destination pixel samples the centre of the square it covers: (0,0) covers source 0..1 in
         * both directions, so its centre is (1,1) -- the 6 in the second row. */
        check(out[SLACK + 0] == 6 && out[SLACK + 1] == 8 && out[SLACK + 2] == 14 && out[SLACK + 3] == 16,
              "scaling down picks the source pixel at the centre of each square, row by row");
        check(out[SLACK + 4] == 23 && out[SLACK + 5] == 24,
              "and the chroma pair from the same place");
        check(out[SLACK + 6] == CANARY, "scaling down writes exactly one 2x2 NV12 frame");
    }

    /* The scale helper never returns the far edge: that would read one pixel past the row. */
    check(s5l8930_isp_scale_nearest(2, 2, 0) == 0 && s5l8930_isp_scale_nearest(2, 2, 1) == 1,
          "an unchanged size maps to itself");
    check(s5l8930_isp_scale_nearest(640, 1920, 1919) == 639, "the last destination pixel reads the last source pixel");
    check(s5l8930_isp_scale_nearest(640, 1920, 0) == 0, "the first destination pixel reads the first source pixel");
    check(s5l8930_isp_scale_nearest(1920, 640, 639) == 1918, "scaling down never reads the row past the source");

    if (failures) {
        printf("%d camera conversion checks failed\n", failures);
        return 1;
    }
    printf("NV12 and 4:2:2 both come out right, scaling picks the nearest source pixel, nothing written past the frame\n");
    return 0;
}
