/* qemu_irq and the one call the model makes on it. */
#ifndef CAMERA_TEST_IRQ_H
#define CAMERA_TEST_IRQ_H
#include "qemu/osdep.h"

typedef struct IRQState *qemu_irq;
void qemu_set_irq(qemu_irq irq, int level);
#endif
