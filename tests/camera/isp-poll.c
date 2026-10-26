/* A guest that polls a register is waiting for the firmware to move it.
 *
 * The mailbox itself already completes instantly, which is fine: the real wait
 * in this block is the host polling a command register until the firmware has
 * taken it. When the register is one the model acts on, isp_command() moves it.
 * When it is one the model does not act on -- a firmware handshake this model
 * never implemented -- nothing can ever move it, and a kext that polls
 * `while (reg == value);` polls for ever: a vCPU at 100%, a phone that gets
 * hot, a picture frozen. That is not a stall the guest can recover from, and
 * the only place it can be ended is the register file.
 *
 * What is checked, against the real isp_mailbox() and isp_command_known():
 *
 *   - a register the model does not act on, holding what the host wrote,
 *     stops holding it after 200 identical reads, so the poll ends
 *   - a command register the model does act on keeps its value however often
 *     it is read: the watchdog cannot take a working handshake away
 *   - a write in between restarts the count, so a host that is doing work is
 *     never mistaken for one that is stuck
 *
 * The mailbox is exercised the way the kext drives it: the status word says
 * write or read and which of the seven slots are live.
 *
 * SLICE engine hw/arm/s5l8930_isp.c define (CPU_MBX|MBX_)
 * SLICE engine hw/arm/s5l8930_isp.c range static const struct { uint8_t cat, reg; } isp_commands | static void isp_command(
 * SLICE engine hw/arm/s5l8930_isp.c fn isp_command_known isp_mailbox
 * CFLAGS -Wall -Wextra -Werror -Wno-unused-parameter -Wno-unused-function
 */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))

/* hw/arm/s5l8930_isp.c's state, reduced to what the mailbox touches: the
 * register file, the mailbox words, and the two run-length counters (one for
 * writes that are never taken, one for reads of a value nothing can move). */
typedef struct S5L8930ISPState {
    uint32_t cpu[0x4000 / 4];
    uint8_t file[0x100][0x100];
    uint32_t stuck_cat, stuck_reg, stuck_val, stuck_count, stuck_cleared;
    uint32_t spin_cat, spin_reg, spin_val, spin_reads;
} S5L8930ISPState;

/* Stands in for the firmware: here it takes nothing, which is the case that
 * used to hang. isp_command_known() is the model's, and still decides what is
 * a command register. */
static void isp_command(S5L8930ISPState *s, uint8_t cat, uint8_t reg)
{
    (void)s;
    (void)cat;
    (void)reg;
}

static void isp_log(S5L8930ISPState *s, const char *fmt, ...)
{
    (void)s;
    (void)fmt;
}

/* The sliced functions follow this file's setup, so the driver above them needs
 * their signatures. */
static void isp_mailbox(S5L8930ISPState *s);

/* One mailbox transaction of up to seven (category, register, value) words, as
 * AppleH3CamIn sends it. */
static void txn(S5L8930ISPState *s, bool write, unsigned n, const uint32_t *words, uint32_t *out)
{
    s->cpu[CPU_MBX_STATUS / 4] = (write ? MBX_WRITE : 0) | ((1u << n) - 1);
    for (unsigned i = 0; i < n; i++) {
        s->cpu[CPU_MBX0 / 4 + i] = words[i];
    }
    isp_mailbox(s);
    assert(s->cpu[CPU_MBX_STATUS / 4] & MBX_DONE);
    assert(!(s->cpu[CPU_MBX_STATUS / 4] & MBX_WRITE));
    for (unsigned i = 0; out && i < n; i++) {
        out[i] = s->cpu[CPU_MBX0 / 4 + i] & 0xff;
    }
}

static void wr(S5L8930ISPState *s, unsigned cat, unsigned reg, unsigned val)
{
    uint32_t w = cat << 16 | reg << 8 | val;

    txn(s, true, 1, &w, NULL);
}

static unsigned rd(S5L8930ISPState *s, unsigned cat, unsigned reg)
{
    uint32_t w = cat << 16 | reg << 8, v = 0xdeadbeef;

    txn(s, false, 1, &w, &v);
    return v;
}

/* The kext's poll: read until the register stops being the value it wrote. */
static unsigned poll_reads(S5L8930ISPState *s, unsigned cat, unsigned reg, unsigned value)
{
    for (unsigned reads = 1; reads <= 400; reads++) {
        if (rd(s, cat, reg) != value) {
            return reads;
        }
    }
    return 0;
}

int main(void)
{
    static S5L8930ISPState s;

    /* A register the model does not act on, polled the way a kext polls a
     * handshake: the write sticks (nothing takes it), and the reads have to
     * end somewhere. 200 reads, then the register reads 0 and the poll falls
     * through into the code path that was already written for it. */
    wr(&s, 0x20, 0x99, 0x55);
    assert(rd(&s, 0x20, 0x99) == 0x55);      /* the write stuck: nothing took it */
    unsigned reads = poll_reads(&s, 0x20, 0x99, 0x55);
    assert(reads == 200);                    /* 199 more read the value, the 200th reads 0 */
    assert(rd(&s, 0x20, 0x99) == 0);
    assert(s.stuck_cleared == 1);
    /* And it stays cleared: nothing re-arms the poll. */
    for (unsigned i = 0; i < 400; i++) {
        assert(rd(&s, 0x20, 0x99) == 0);
    }

    /* A register the model does act on is never touched by the watchdog, however
     * often it is polled: 24:0c holds a value until the firmware takes it, and
     * taking a working handshake away would break the cameras that work. */
    static S5L8930ISPState known;
    wr(&known, 0x24, 0x0c, 5);
    for (unsigned i = 0; i < 500; i++) {
        assert(rd(&known, 0x24, 0x0c) == 5);
    }
    assert(known.stuck_cleared == 0);

    /* A host that writes between reads is working, not stuck: the count restarts,
     * so 150 reads, a write, 150 reads leaves the register alone. */
    static S5L8930ISPState busy;
    wr(&busy, 0x20, 0x99, 0x55);
    for (unsigned i = 0; i < 150; i++) {
        assert(rd(&busy, 0x20, 0x99) == 0x55);
    }
    wr(&busy, 0x20, 0x99, 0x55);
    for (unsigned i = 0; i < 150; i++) {
        assert(rd(&busy, 0x20, 0x99) == 0x55);
    }
    assert(busy.stuck_cleared == 0);

    /* A register the host asks about but never wrote is just 0; the watchdog has
     * nothing to fire on and nothing to count. */
    static S5L8930ISPState cold;
    for (unsigned i = 0; i < 400; i++) {
        assert(rd(&cold, 0x21, 0x77) == 0);
    }
    assert(cold.stuck_cleared == 0 && cold.spin_reads == 0);

    /* Several slots in one transaction keep their own values, and the status word
     * still reports a completed transaction for all of them. */
    static S5L8930ISPState many;
    uint32_t words[3] = { 0x200181, 0x200207, 0x2003a5 }, back[3];
    txn(&many, true, 3, words, NULL);
    words[0] &= ~0xffu;
    words[1] &= ~0xffu;
    words[2] &= ~0xffu;
    txn(&many, false, 3, words, back);
    assert(back[0] == 0x81 && back[1] == 0x07 && back[2] == 0xa5);

    printf("ok\n");
    return 0;
}
