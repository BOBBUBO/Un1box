/* The board header's camera-related surface: the ISP's type name, its DART
 * wiring, the DART translator the model writes through, and the object casts
 * and QOM calls its class_init makes. */
#ifndef CAMERA_TEST_S5L8930_H
#define CAMERA_TEST_S5L8930_H
#include "qemu/osdep.h"
#include "hw/core/sysbus.h"

#define TYPE_S5L8930_ISP "s5l8930.isp"

void s5l8930_isp_set_dart(DeviceState *isp, DeviceState *dart);
hwaddr s5l8930_dart_dev_xlate(DeviceState *dart, int sid, hwaddr addr);

/* QOM, as much as ONE device's model touches. */
typedef struct ObjectClass ObjectClass;
typedef struct DeviceClass {
    struct DeviceClass *parent;
    void (*unrealize)(DeviceState *dev);
    void (*legacy_reset)(DeviceState *dev);
    const struct VMStateDescription *vmsd;
    const char *desc;
    bool user_creatable;
    unsigned long categories[1];
} DeviceClass;

typedef struct TypeInfo {
    const char *name;
    const char *parent;
    size_t instance_size;
    void (*instance_init)(Object *obj);
    void (*class_init)(ObjectClass *klass, const void *data);
} TypeInfo;

DeviceClass *DEVICE_CLASS(ObjectClass *klass);
ObjectClass *object_get_class(Object *obj);
void device_class_set_legacy_reset(DeviceClass *dc, void (*reset)(DeviceState *dev));
int type_register_static(const TypeInfo *info);
#define type_init(function) static void __attribute__((constructor)) function##_register(void) { function(); }

#define OBJECT_DECLARE_SIMPLE_TYPE(type, name)                    \
    typedef struct type type;                                     \
    static inline type *name##_OBJ(Object *obj)                   \
    {                                                             \
        return (type *)obj;                                       \
    }                                                             \
    static inline type *name(void *obj);                          \
    static inline type *name(void *obj)                           \
    {                                                             \
        return (type *)obj;                                       \
    }
#endif
