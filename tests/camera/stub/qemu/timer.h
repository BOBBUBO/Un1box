/* The timer API the model uses, as declarations: this stub is for the compiler,
 * not for running. QEMU_CLOCK_VIRTUAL is the only clock it asks for. */
#ifndef CAMERA_TEST_TIMER_H
#define CAMERA_TEST_TIMER_H
#include "qemu/osdep.h"

#define QEMU_CLOCK_VIRTUAL 1
#define NANOSECONDS_PER_SECOND 1000000000LL

QEMUTimer *timer_new_ns(int clock, void (*cb)(void *), void *opaque);
void timer_mod(QEMUTimer *ts, int64_t expire_time);
void timer_del(QEMUTimer *ts);
int64_t qemu_clock_get_ns(int clock);
int64_t qemu_clock_get_ms(int clock);
#endif
