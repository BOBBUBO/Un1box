/* The one lock the camera slot uses, without QEMU's own thread machinery.
 *
 * The real qemu/thread.h defines QemuMutex in terms of QEMU's futex-based lock
 * and needs config-host.h; the model only needs a mutex that works, so this is
 * pthreads with QEMU's names. */
#ifndef CAMERA_TEST_THREAD_H
#define CAMERA_TEST_THREAD_H
#include "qemu/osdep.h"
#include <pthread.h>

typedef pthread_mutex_t QemuMutex;

static inline void qemu_mutex_init(QemuMutex *mutex)
{
    pthread_mutex_init(mutex, NULL);
}

static inline void qemu_mutex_lock(QemuMutex *mutex)
{
    pthread_mutex_lock(mutex);
}

static inline void qemu_mutex_unlock(QemuMutex *mutex)
{
    pthread_mutex_unlock(mutex);
}

#endif
