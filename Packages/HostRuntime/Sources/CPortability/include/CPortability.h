// C calls HostRuntime needs on Linux and Android that their Swift C-library modules leave out.
#pragma once
#include <sys/types.h>

/// fallocate(fd, FALLOC_FL_PUNCH_HOLE | FALLOC_FL_KEEP_SIZE, offset, length): 0, or -1 with errno set.
int lt_punch_hole(int fd, off_t offset, off_t length);
