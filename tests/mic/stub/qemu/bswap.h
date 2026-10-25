/* The one store hw/arm/host-mic.c uses from qemu/bswap.h. Little-endian host
 * assumed, which every target the engine builds for is. */
#ifndef MIC_TEST_BSWAP_H
#define MIC_TEST_BSWAP_H
#include <stdint.h>
#include <string.h>
static inline void stw_le_p(void *p, uint16_t v) { memcpy(p, &v, sizeof v); }
#endif
