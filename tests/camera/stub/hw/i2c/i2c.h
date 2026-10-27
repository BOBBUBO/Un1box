/* The I2C slave interface, as hw/arm/camera-sensor.c uses it: the event hook, send and recv, and the QOM
 * boilerplate that declares a device on the bus. Declarations only, like the rest of this directory. */
#ifndef CAMERA_TEST_I2C_H
#define CAMERA_TEST_I2C_H
#include "qemu/osdep.h"
#include "hw/arm/s5l8930.h"

typedef struct I2CSlave {
    void *parent_obj;           /* Object in the engine */
} I2CSlave;

enum i2c_event {
    I2C_START_RECV,
    I2C_START_SEND,
    I2C_NACK,
    I2C_FINISH,
};

typedef struct I2CSlaveClass {
    const void *parent_class;
    int (*event)(I2CSlave *i2c, enum i2c_event event);
    int (*send)(I2CSlave *i2c, uint8_t data);
    uint8_t (*recv)(I2CSlave *i2c);
} I2CSlaveClass;

#define TYPE_I2C_SLAVE "i2c-slave"
#define I2C_SLAVE(obj) ((I2CSlave *)(obj))
#define I2C_SLAVE_CLASS(klass) ((I2CSlaveClass *)(klass))

#endif
