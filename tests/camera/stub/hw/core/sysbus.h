/* The SysBusDevice surface the model's init uses: two MMIO regions, two IRQs,
 * and the object casts. */
#ifndef CAMERA_TEST_SYSBUS_H
#define CAMERA_TEST_SYSBUS_H
#include "qemu/osdep.h"
#include "hw/core/irq.h"

#define TYPE_SYS_BUS_DEVICE "sys-bus-device"

typedef struct SysBusDevice {
    DeviceState *parent_obj;
} SysBusDevice;

SysBusDevice *SYS_BUS_DEVICE(Object *obj);
void sysbus_init_mmio(SysBusDevice *dev, MemoryRegion *memory);
void sysbus_init_irq(SysBusDevice *dev, qemu_irq *p);
#endif
