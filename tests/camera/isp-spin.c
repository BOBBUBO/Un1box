/* A guest waiting on a hardware event this model does not produce.
 *
 * The ISP's register block is plain memory: the guest writes it and, for the
 * addresses the model does not drive, nothing ever changes. The back camera's
 * driver polls such a register while it brings its sensor up, and a poll of a
 * value that cannot change is a core at 100%: the picture freezes, and the phone
 * gets hot enough that the battery itself reaches 50 degrees. The front camera's
 * path does not poll that address, which is why only one of the two cameras does
 * this.
 *
 * Nothing in this model can produce the event, so the register file answers it
 * after 200 identical reads -- in the shape each kind of wait expects:
 *
 *   - a register the guest never wrote reads as all ones, which ends a
 *     "wait until the bit is set" loop
 *   - a value the guest wrote itself reads 0, which ends a "wait until it
 *     clears" loop
 *
 * The registers the model does drive are never touched, and any write restarts
 * the count, so a guest that is actually working is never mistaken for a stuck
 * one.
 *
 * SLICE engine hw/arm/s5l8930_isp.c fn isp_register_model_drives isp_read
 * CFLAGS -Wall -Wextra -Werror -Wno-unused-parameter -Wno-unused-function
 */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint64_t hwaddr;    /* qemu/osdep.h's, for a test that includes no QEMU header */

#define ISP_SIZE        0xd6000
#define ISP_INT_STATUS  0xb000c
#define ISP_OUT_ENABLE  0xd06a0
#define ISP_CUR_Y       0xd3654
#define ISP_CUR_C       0xd3658
#define ISP_NEXT_Y      0xd3660
#define ISP_NEXT_C      0xd3664
#define ISP_OUT_FORMAT  0xd3b20

/* hw/arm/s5l8930_isp.c's state, reduced to what isp_read() touches. */
typedef struct S5L8930ISPState {
    uint32_t *isp;
    uint64_t poll_addr;
    uint32_t poll_value, poll_reads;
} S5L8930ISPState;

static void isp_log(S5L8930ISPState *s, const char *fmt, ...)
{
    (void)s;
    (void)fmt;
}

static uint64_t isp_read(void *opaque, hwaddr addr, unsigned size);

/* The driver's wait, as a kext writes it: read the register until the bit it needs appears. */
static unsigned poll_until_set(S5L8930ISPState *s, hwaddr addr, uint32_t bit)
{
    for (unsigned reads = 1; reads <= 400; reads++) {
        if (isp_read(s, addr, 4) & bit) {
            return reads;
        }
    }
    return 0;
}

/* The other shape: read until the value the guest wrote is gone. */
static unsigned poll_until_clear(S5L8930ISPState *s, hwaddr addr)
{
    for (unsigned reads = 1; reads <= 400; reads++) {
        if (isp_read(s, addr, 4) == 0) {
            return reads;
        }
    }
    return 0;
}

int main(void)
{
    static uint32_t registers[ISP_SIZE / 4];
    S5L8930ISPState s = { .isp = registers, .poll_addr = (uint64_t)-1 };

    /* An address the model does not drive, never written, polled for a bit that means "the sensor is up".
     * It has to end: this is the back camera's hang. */
    unsigned reads = poll_until_set(&s, 0xc1234, 0x10);
    assert(reads == 200);                     /* the 200th read answers all ones */
    assert((isp_read(&s, 0xc1234, 4) & 0x10) != 0);

    /* A value the guest wrote itself, polled until it clears: also ends. */
    memset(registers, 0, sizeof(registers));
    registers[0xc2222 / 4] = 0x5;
    s.poll_reads = 0;
    s.poll_addr = (uint64_t)-1;
    reads = poll_until_clear(&s, 0xc2222);
    assert(reads == 200);
    assert(isp_read(&s, 0xc2222, 4) == 0);

    /* A register the model does drive is never answered on the guest's behalf: the interrupt status reads
     * 0 until a frame sets it, and that has to stay 0 however often a guest looks. */
    memset(registers, 0, sizeof(registers));
    s.poll_reads = 0;
    s.poll_addr = (uint64_t)-1;
    for (unsigned i = 0; i < 400; i++) {
        assert(isp_read(&s, ISP_INT_STATUS, 4) == 0);
    }
    assert(s.poll_reads == 0);

    /* A guest that alternates between two registers is doing something, not waiting on one: neither is
     * answered. Without this a driver that checks two status registers in turn would trip the watchdog. */
    memset(registers, 0, sizeof(registers));
    s.poll_reads = 0;
    s.poll_addr = (uint64_t)-1;
    hwaddr first = 0xc3000, second = 0xc3004;
    for (unsigned i = 0; i < 400; i++) {
        assert(isp_read(&s, first, 4) == 0);
        assert(isp_read(&s, second, 4) == 0);
    }
    assert(registers[first / 4] == 0 && registers[second / 4] == 0);

    /* A programmed size register reads back what was written, as before: the watchdog only fires on a
     * poll of the same value, and a guest reads a mode's registers a handful of times. */
    memset(registers, 0, sizeof(registers));
    s.poll_reads = 0;
    s.poll_addr = (uint64_t)-1;
    registers[ISP_OUT_FORMAT / 4] = 0x11;
    assert(isp_read(&s, ISP_OUT_FORMAT, 4) == 0x11);
    assert(isp_read(&s, ISP_OUT_FORMAT, 4) == 0x11);

    printf("ok\n");
    return 0;
}
