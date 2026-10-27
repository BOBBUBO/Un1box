/* The vmstate macros the model lists, as no-ops: the stub only has to type-check
 * the initialiser and prove the state's fields have real types. */
#ifndef CAMERA_TEST_VMSTATE_H
#define CAMERA_TEST_VMSTATE_H
#include "qemu/osdep.h"

typedef struct VMStateField {
    const char *name;
    size_t offset;
    size_t size;
    int elements;
    int type;
} VMStateField;

typedef struct VMStateDescription {
    const char *name;
    int version_id;
    int minimum_version_id;
    const VMStateField *fields;
} VMStateDescription;

#define VMSTATE_END_OF_LIST() { .name = NULL }
#define VMSTATE_UINT32(field, state) { .name = #field, .size = 4 }
#define VMSTATE_UINT8(field, state) { .name = #field, .size = 1 }
#define VMSTATE_UINT32_ARRAY(field, state, num) { .name = #field, .elements = (num) }
#define VMSTATE_BUFFER_UNSAFE(field, state, start, size) { .name = #field, .elements = (size) }
#define VMSTATE_BUFFER_POINTER_UNSAFE(field, state, start, size) { .name = #field, .elements = (size) }
#define VMSTATE_TIMER_PTR(field, state) { .name = #field }
#define VMSTATE_UINT16(field, state) { .name = #field, .size = 2 }
#define VMSTATE_BOOL(field, state) { .name = #field, .size = 1 }
#endif

