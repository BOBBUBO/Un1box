#ifndef LIGHTTOUCH_H
#define LIGHTTOUCH_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
#define LT_ABI_VERSION 1u
#define LT_MAX_CONTACTS 8u
#define LT_FRAME_CAPACITY (4096u * 4096u * 4u)
#define LT_INIT(type) {sizeof(type), LT_ABI_VERSION}
typedef enum {
    LT_OK = 0,
    LT_INVALID = 1,
    LT_IO = 2,
    LT_BUSY = 3,
    LT_UNSUPPORTED = 4,
    LT_ENGINE = 5,
    LT_DIRTY = 6,
    LT_CANCELLED = 7,
    LT_AGAIN = 8
} lt_status;
typedef struct {
    uint32_t size, version;
    int32_t code;
    char message[512];
} lt_error;
typedef struct lt_device lt_device;
typedef struct lt_runtime lt_runtime;
/* All paths UTF-8, absolute, private app-owned directories. Destination must not
 * exist. Imports stage beside destination and atomically publish after validation.
 * expected_sha256 is mandatory for archives; folder import verifies lock outputs.
 * Progress callback may cancel by returning nonzero, called on importing thread. */
typedef int (*lt_progress_fn)(void *, uint64_t, uint64_t);
typedef struct {
    uint32_t size, version;
    const char *source, *destination, *expected_sha256;
    uint64_t max_bytes;
    lt_progress_fn progress;
    void *opaque;
} lt_import_config;
lt_status lt_import_archive(const lt_import_config *, lt_error *);
lt_status lt_import_directory(const lt_import_config *, lt_error *);
lt_status lt_sha256_file(const char *path, char hex[65], lt_error *);
/* Device handle holds an exclusive flock lease until close. Base must be separate
 * from state. State is pinned to base lock checksum. Dirty state is never erased:
 * allow_dirty permits cold recovery with existing overlay, otherwise LT_DIRTY. */
typedef struct {
    uint32_t size, version;
    const char *base_path, *state_path;
    uint32_t allow_dirty;
} lt_device_config;
typedef struct {
    uint32_t size, version;
    char board[32], build[32], boot_strategy[32], base_sha256[65];
    uint32_t dirty;
} lt_device_info;
lt_status lt_device_open(const lt_device_config *, lt_device **, lt_error *);
/* Engine-owned hardware facts. Call before boot; machine is selected by board
 * from engine discovery when boot.machine is null. Cap bits: 1 accel,2 guest
 * gyro,4 multi-touch,8 frame copy. Host sensor presence is reported separately. */
typedef struct {
    uint32_t size, version;
    char machine[32], board[16];
    uint32_t width, height, scale, orientation, max_contacts;
    uint64_t capabilities;
} lt_board_info;
lt_status lt_engine_board(const char *engine_library, const char *board, lt_board_info *, lt_error *);
lt_status lt_device_describe(lt_device *, lt_device_info *, lt_error *);
void lt_device_close(lt_device *);
/* Engine facts select the machine; product never invents a machine name. ROM and
 * package remain caller supplied private assets. Shared domain assembles argv. */
typedef struct {
    uint32_t size, version;
    const char *machine, *bootrom_path, *guest_package_path;
    const char *serial_path;
    uint32_t audio_enabled;
} lt_boot_config;
lt_status lt_device_boot_argv(lt_device *, const lt_boot_config *, int *argc, const char *const **argv, lt_error *);
/* Owned argv remains valid until device close or next boot_argv call. */
typedef enum {
    LT_STOPPED = 0,
    LT_STARTING = 1,
    LT_RUNNING = 2,
    LT_PAUSED = 3,
    LT_STOPPING = 4,
    LT_FAILED = 5
} lt_state;
typedef struct {
    uint32_t size, version;
    uint32_t state, guest_shutdown_confirmed, storage_failed, dirty;
    int32_t engine_exit;
    uint64_t frames;
} lt_runtime_status;
typedef struct {
    uint32_t size, version;
    const char *engine_library;
    lt_device *device;
    lt_boot_config boot;
} lt_runtime_config;
/* One start per OS process; create negotiates engine ABI before any start.
 * Engine runs on owned background thread. A stopped runtime cannot restart:
 * launch a fresh service/worker process. device outlives runtime. */
lt_status lt_runtime_create(const lt_runtime_config *, lt_runtime **, lt_error *);
lt_status lt_runtime_start(lt_runtime *, lt_error *);
lt_status lt_runtime_poll(lt_runtime *, lt_runtime_status *, lt_error *);
lt_status lt_runtime_pause(lt_runtime *, uint32_t paused, lt_error *);
/* Stop requests semantic guest shutdown; poll confirmation. Timeout/kill leaves
 * dirty marker. destroy only after terminal state; returns LT_BUSY otherwise. */
lt_status lt_runtime_request_shutdown(lt_runtime *, lt_error *);
lt_status lt_runtime_destroy(lt_runtime *, lt_error *);
/* Latest frame wins. copy_frame copies under engine mutex to caller storage.
 * BGRA8888, top-left, y down, stride >= width*4. No borrowed pointer crosses ABI.
 * since_sequence in/out; LT_AGAIN when unchanged. Any caller thread, serialized. */
typedef struct {
    uint32_t size, version;
    uint32_t width, height, stride, format;
    uint64_t sequence, timestamp_ns, bytes;
} lt_frame_info;
lt_status lt_runtime_copy_frame(lt_runtime *, void *pixels, uint64_t capacity, uint64_t *since_sequence,
                                lt_frame_info *, lt_error *);
/* Export a private bounded shared-frame FD. Caller owns fd and closes it.
 * mmap read-only; header followed by capacity bytes; generation seqlock must be
 * equal/even before and after copying. Producer never waits for consumer. */
typedef struct {
    uint32_t size, version;
    uint64_t generation;
    lt_frame_info frame;
    uint64_t capacity;
} lt_shared_frame;
lt_status lt_runtime_frame_fd(lt_runtime *, int *fd, uint64_t *mapping_bytes, lt_error *);
/* Stable IDs, whole batch admission, normalized screen coords y down. One
 * CLOCK_MONOTONIC timestamp per batch. Phases 0 down,1 move,2 up,3 cancel. */
typedef struct {
    uint32_t size, version;
    uint32_t id, phase;
    double x, y;
} lt_contact;
typedef struct {
    uint32_t size, version;
    uint64_t timestamp_ns;
    uint32_t count, reserved;
    lt_contact contacts[LT_MAX_CONTACTS];
} lt_touch_batch;
lt_status lt_runtime_touch(lt_runtime *, const lt_touch_batch *, lt_error *);
/* Touch call returns queued admission only. Poll newest batch result: state
 * 0 unknown,1 queued,2 delivered,3 rejected. No guest receipt claim from queue. */
typedef struct {
    uint32_t size, version;
    uint64_t request_id;
    uint32_t state;
    int32_t engine_result;
} lt_input_status;
lt_status lt_runtime_input_status(lt_runtime *, lt_input_status *, lt_error *);
lt_status lt_runtime_cancel_input(lt_runtime *, lt_error *);
lt_status lt_runtime_button(lt_runtime *, uint32_t button, uint32_t down, lt_error *);
/* Guest portrait axes: x right, y up, z toward viewer. Acceleration m/s^2,
 * angular velocity rad/s. Kind bits 1 acceleration,2 gyro. Samples are live,
 * timestamps CLOCK_MONOTONIC (Android adapter converts BOOTTIME using current
 * clock offset), max 200 Hz; discarded while paused/stale (>250ms), never persisted. */
typedef struct {
    uint32_t size, version;
    uint64_t timestamp_ns;
    uint32_t kinds, reserved;
    double acceleration[3], angular_velocity[3];
} lt_motion;
lt_status lt_runtime_motion(lt_runtime *, const lt_motion *, lt_error *);
/* Display mapping excludes letterboxes; rotation 0/90/180/270 clockwise. */
/* Sensor frame conversion and clock-domain conversion live in native domain.
 * source_timestamp_ns and source_now_ns use the same adapter clock; monotonic
 * now is sampled here, preserving age across Android suspend offsets. */
lt_status lt_map_motion(uint32_t rotation, uint64_t source_timestamp_ns, uint64_t source_now_ns, const lt_motion *raw,
                        lt_motion *portrait, lt_error *);
lt_status lt_map_contact(double px, double py, double left, double top, double width, double height, uint32_t rotation,
                         double *x, double *y);
/* Bounded PCM pull. Interleaved signed little-endian 16-bit; caller owns buffer.
 * LT_UNSUPPORTED until engine advertises PCM callback; never synthesizes audio. */
typedef struct {
    uint32_t size, version;
    uint32_t sample_rate, channels, frames, reserved;
    uint64_t timestamp_ns, dropped_frames;
} lt_pcm_info;
lt_status lt_runtime_pcm(lt_runtime *, int16_t *, uint32_t capacity_frames, lt_pcm_info *, lt_error *);
uint32_t lt_abi_version(void);
#ifdef __cplusplus
}
#endif
#endif
