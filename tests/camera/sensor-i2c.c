/* The camera sensor answers when the kext asks it something.
 *
 * The back camera is a MIPI part the driver talks to over the ISP's I2C command
 * (24:b4: slave in b5, register in b6/b7, data in b8, result in b9). Before this
 * the model stored nothing and answered nothing there, so every read came back
 * as 0 -- a chip identity of 0x0000 -- and a driver that identifies its sensor
 * before it configures anything had nothing to accept: the ISP was never
 * programmed, and the camera app showed a buffer nothing had ever filled.
 *
 * What is checked here, against the real isp_command() and the real sensor
 * transaction it calls:
 *
 *   - the identity registers of the back sensor read back the part the A4
 *     boards carry (OV5650: 0x56 0x50 at 0x300A/0x300B)
 *   - a write the driver makes is read back, which is what a driver's "did my
 *     configuration take" check needs
 *   - a register never written reads 0, as a blank part would
 *   - the transfer's result byte is 0 ("it worked"), so the driver's error path
 *     is not taken
 *   - the front sensor keeps behaving as it did, since that camera works today
 *
 * SLICE engine hw/arm/s5l8930_isp.c define SENSOR_
 * SLICE engine hw/arm/s5l8930_isp.c range static const struct { uint8_t cat, reg; } isp_commands | static void isp_sensor_transaction(
 * SLICE engine hw/arm/s5l8930_isp.c fn isp_sensor_transaction
 * SLICE engine hw/arm/s5l8930_isp.c fn isp_command
 * CFLAGS -Wall -Wextra -Werror -Wno-unused-parameter -Wno-unused-function
 */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define HOST_CAMERA_BACK  0
#define HOST_CAMERA_FRONT 1

typedef struct S5L8930ISPState {
    uint8_t file[256][256];
    uint8_t sensor;
    uint8_t shutter;        /* frames still to flag as the still */
    uint8_t strobe;         /* the flagged frame's strobe capture count */
    struct {
        bool taken;
        uint8_t slave;
        uint16_t reg;
        uint8_t value;
    } sensor_regs[16];
    unsigned sensor_next;
} S5L8930ISPState;

static void isp_log(S5L8930ISPState *s, const char *fmt, ...)
{
    (void)s;
    (void)fmt;
}

/* The frontend's camera selection, which is host-camera.c's in the engine. */
static int selected_sensor;
static void host_camera_select(unsigned sensor)
{
    selected_sensor = (int)sensor;
}

static void isp_command(S5L8930ISPState *s, uint8_t cat, uint8_t reg);

/* One I2C transaction as the kext programs it, then the register file it lands in. */
static void i2c(S5L8930ISPState *s, unsigned slave, unsigned reg, unsigned data)
{
    s->file[0x24][0xb5] = slave;
    s->file[0x24][0xb6] = reg >> 8;
    s->file[0x24][0xb7] = reg & 0xff;
    s->file[0x24][0xb8] = data;
    s->file[0x24][0xb4] = 1;
    isp_command(s, 0x24, 0xb4);
    assert(s->file[0x24][0xb9] == 0);            /* the transfer worked */
}

static unsigned answer(const S5L8930ISPState *s)
{
    return s->file[0x24][0xb8];
}

int main(void)
{
    static S5L8930ISPState back;                 /* sensor 0: back, as a reset leaves it */
    static S5L8930ISPState front;

    back.sensor = HOST_CAMERA_BACK;
    front.sensor = HOST_CAMERA_FRONT;

    /* The driver identifies the part first: OV5650's chip ID. */
    i2c(&back, 0x3c, 0x300a, 0);
    assert(answer(&back) == 0x56);
    i2c(&back, 0x3c, 0x300b, 0);
    assert(answer(&back) == 0x50);

    /* A configuration write is read back: that is how a driver checks it took. */
    i2c(&back, 0x3c, 0x3600, 0x42);
    assert(answer(&back) == 0x42);
    i2c(&back, 0x3c, 0x3600, 0x00);              /* reading register 0x3600 again */
    assert(answer(&back) == 0x42);               /* the part still holds what was written */

    /* A register never written reads 0, like a blank part. */
    i2c(&back, 0x3c, 0x1234, 0);
    assert(answer(&back) == 0);

    /* A transaction to nothing at all is not an error the driver can act on. */
    i2c(&back, 0x00, 0x300a, 0);
    assert(back.file[0x24][0xb9] == 0);

    /* The front sensor keeps its old behaviour: the echo of what was written, no identity. */
    i2c(&front, 0x10, 0x0000, 0x7e);
    assert(answer(&front) == 0x7e);
    i2c(&front, 0x10, 0x300a, 0);
    assert(answer(&front) == 0);                 /* not the back sensor's identity */

    /* Sensor recognition still picks the sensor, and still clears the register the kext polls. */
    back.file[0x04][0x00] = 1;
    isp_command(&back, 0x04, 0x00);
    assert(back.sensor == HOST_CAMERA_FRONT);
    assert(selected_sensor == HOST_CAMERA_FRONT);
    assert(back.file[0x04][0x00] == 0);

    printf("ok\n");
    return 0;
}
