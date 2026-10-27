#define _GNU_SOURCE
#include "CPortability.h"
#include <errno.h>
#include <fcntl.h>

int lt_punch_hole(int fd, off_t offset, off_t length) {
#if defined(__linux__)
    return fallocate(fd, FALLOC_FL_PUNCH_HOLE | FALLOC_FL_KEEP_SIZE, offset, length);
#else
    (void)fd; (void)offset; (void)length;
    errno = ENOTSUP;
    return -1;
#endif
}
