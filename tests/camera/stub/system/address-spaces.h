/* The one address-space write the model does, through dart1. */
#ifndef CAMERA_TEST_ADDRESS_SPACES_H
#define CAMERA_TEST_ADDRESS_SPACES_H
#include "qemu/osdep.h"

typedef struct {
    int dummy;
} MemTxAttrs;
#define MEMTXATTRS_UNSPECIFIED ((MemTxAttrs){ 0 })

typedef struct AddressSpace AddressSpace;
extern AddressSpace address_space_memory;

void address_space_write(AddressSpace *as, hwaddr addr, MemTxAttrs attrs, const void *buf, hwaddr len);
#endif
