/* The two stores the camera's device code uses. Little-endian host assumed,
 * which every target the engine builds for is. */
#ifndef CAMERA_TEST_BSWAP_H
#define CAMERA_TEST_BSWAP_H
#include "qemu/osdep.h"

static inline void stw_le_p(void *p, uint16_t v) { memcpy(p, &v, sizeof v); }
static inline void stl_le_p(void *p, uint32_t v) { memcpy(p, &v, sizeof v); }
#endif
