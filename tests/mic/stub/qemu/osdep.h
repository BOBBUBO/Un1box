/* The piece of qemu/osdep.h hw/arm/host-mic.c uses: a monotonic clock.
 * The real header brings glib; here the clock is the test's, so staleness is
 * exercised by moving it rather than by sleeping. */
#ifndef MIC_TEST_OSDEP_H
#define MIC_TEST_OSDEP_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
extern int64_t mic_test_now_us;
#define g_get_monotonic_time() mic_test_now_us
#endif
