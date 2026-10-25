/* The microphone tests' clock, substituted for glib's g_get_monotonic_time() by
 * stub/qemu/osdep.h so a stale tail can be reached by moving time instead of
 * sleeping through it. */
#include <stdint.h>

int64_t mic_test_now_us = 1000000;
