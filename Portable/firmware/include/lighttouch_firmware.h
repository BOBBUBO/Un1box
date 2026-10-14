#ifndef LIGHTTOUCH_FIRMWARE_H
#define LIGHTTOUCH_FIRMWARE_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
#define LT_FIRMWARE_ABI 1u
typedef enum {
    LT_FW_OK = 0,
    LT_FW_ARGUMENT = 1,
    LT_FW_UNSUPPORTED = 2,
    LT_FW_CHECKSUM = 3,
    LT_FW_FORMAT = 4,
    LT_FW_IO = 5,
    LT_FW_CANCELLED = 6,
    LT_FW_BUSY = 7,
    LT_FW_RESTART_REQUIRED = 8
} lt_fw_status;
/* Called synchronously on caller's thread. Return nonzero to cancel. No retained
 * pointers. Invoke preparation on a worker thread; one preparation per process.
 * For parser crash isolation put this library in a dedicated same-UID service. */
typedef int (*lt_fw_progress)(void *user, const char *stage, uint64_t done, uint64_t total);
typedef struct {
    uint32_t size, version;
    const char *catalog_path, *firmware_id, *ipsw_path, *guest_tools_path, *destination;
    /* Required verified guest export manifest, never downloaded by this API. */
    const char *guest_manifest_path;
    lt_fw_progress progress;
    void *user;
} lt_fw_request;
typedef struct {
    uint32_t size, version;
    int32_t status;
    char message[512];
} lt_fw_error;
uint32_t lt_firmware_api_version(void);
/* Blocking userspace implementation; never launches executables or mounts disks.
 * Destination must be absent. Stages in private sibling directory, fsyncs files
 * and directories, promotes with rename-no-replace, then fsyncs parent. Failure
 * removes only the staging tree. Existing outputs and all sources are read-only.
 * Interrupted staging trees are intentionally identifiable and never admitted. */
lt_fw_status lt_firmware_prepare(const lt_fw_request *, lt_fw_error *);
#ifdef __cplusplus
}
#endif
#endif
