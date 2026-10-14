The portable product domain and runtime expose ABI 1 through `include/lighttouch.h`.
The runtime negotiates upstream QEMU API major 2 and portable engine extension 1.
It includes only exported engine headers; QEMU/platform internals stay out of the
public product ABI. Kotlin/JNI and Linux SDL consume the same native implementation.

Prepared imports validate the expected archive SHA256, strict JSON index, relative
paths, duplicates, prefix conflicts, zlib checksum and exact compressed/uncompressed
lengths. Every recorded output checksum, identity hash, NAND page count and full
`path sha256\n` NAND listing is checked before atomic promotion. Files/directories
are owner-readable and not writable. Import destination's private parent must exist.
Directory imports copy only regular files and reject symlinks. Sources are never
modified. Cancellation removes unpublished staging; maximum declared data is 64 GiB
(or a smaller caller bound). Index maximum is 64 MiB and one million entries.

A native library stores stable random record IDs, names, board/build/base integrity,
immutable copied ROM/package assets and independent base/state directories. Record
imports publish atomically; cross-process library mutations use a nonblocking flock.
Listing and selected ID survive process restart; selection is a native atomic file.
One-time legacy adoption holds the old device lease and copies retained state,
including dirty/pin/overlay, into an empty library without deleting originals. It
never replaces the baseline when another board is imported. Asset hashes are pinned
and checked when a library device opens. Host/guest capabilities remain engine facts
queried per record, not inferred from firmware metadata.

A device owns one nonblocking exclusive `flock` on its private writable state. Its
base lock checksum pins the state; a mismatched or unidentified state is refused.
Each open verifies immutable permissions and checksums again. NOR is copied into
state atomically, NAND writes go to the overlay, and `.dirty` is synced before engine
start. Only confirmed guest power-off, successful engine exit and no storage fault
clear the marker. Cold recovery is explicit and preserves all existing state; it
makes no filesystem repair promise. A failed recovery stays dirty and reports its
error. Device close is valid only after runtime disposal (callers serialize handle
lifetime operations).

Native boot assembly supports n72ap iboot/bootrom and n81ap kboot. Machine names,
display dimensions, contact limits and sensor capabilities come from engine facts.
n81ap uses prepared kboot.bin/nand-overlay/nor-rw and identity die-id; no hardware
ROM or universal RAM override is imposed. Other preparation routes return unsupported.
`IT_TVOUT_READY=1` matches the original production n72 `BootRecipe.iPod` environment.
Private .itpack archives are bounded to 64 MiB, checksum-validated against their
manifest, selected by board/build/host protocol and composed into ephemeral state
work/guest-offer with the prepared lock's hook policy. Host executables are never
launched from guest package content.

One engine initialization is allowed in each process, including failed starts.
Reboot is a fresh service/worker process. Android's service owns JNI/runtime handles;
Linux's parent execs a native worker with private inherited UID-checked Unix sockets.
Queued control requests are distinct from confirmed guest shutdown and touch batch
execution. Pause state is polled from the engine. No snapshots are advertised.
Shutdown timeout leaves dirty data; callers terminate the worker process.

Frame copies hold the engine mutex. A supervisor publishes latest BGRA frames to a
size-sealed private memfd; consumers get a readonly FD, close it after mapping and
use the even/equal generation check around copies. Slow consumers lose old frames.
The FD mapping and all handles have explicit ownership in the header. PCM capture
is the existing real QEMU 44100Hz stereo S16LE mixer capture, drained into sixteen
bounded packets; overflow drops oldest host packets and increments dropped_frames.
Linux sends PCM on a separate bounded socket and SDL queues at most approximately
100ms. Silence/host queues do not prove audible guest output; real acceptance is
required. Frame/PCM transport does not use JSON or Binder frame parcels.

Touch preserves stable IDs, phased whole batches and monotonic timestamps. Native
mapping rejects letterboxes and applies quarter-turn rotation. Motion includes
gravity in m/s² and angular velocity rad/s in guest portrait axes. Android samples
use BOOTTIME and native mapping converts sample age to CLOCK_MONOTONIC using the
current source clock. Samples older than 250ms or above 200Hz are rejected. Acceleration and gyro have
independent admission clocks, including equal interleaved timestamps at exactly
200Hz; one stream cannot starve the other. Host
sensor availability is separate from engine guest capability. Held input/live motion
is cancelled on pause/focus loss; none is saved across process restarts.

Build and synthetic checks (these are not guest acceptance):

```sh
cmake -S Portable -B /private/build/portable \
  -DLIGHTTOUCH_QEMU_SOURCE=/path/to/qemu-ios -DLIGHTTOUCH_BUILD_TESTS=ON
cmake --build /private/build/portable -j2
ctest --test-dir /private/build/portable --output-on-failure
```

The shared code requires C++17 and zlib (NDK supplies zlib); hashing/JSON have no
platform dependency. The decoder is a native port of the original PreparedBase,
GuestPack and BootRecipe contracts and retains their project licensing. Do not
link GPLv3 firmware dependencies into the GPLv2-only engine process; firmware
preparation runs in a separately owned service/process.

Typed guest operations use the existing agent RPC with bounded UTF-8 arguments and
owned results: ping, frontmost, launch a builtin, type text, sync, and launch an
explicitly authorized installed gyro workload. Requests are asynchronous and at
most one is pending; cancellation/timeout never retries a possibly applied mutation.
Gyro workload launch requires actual engine guest gyro capability. Absent historical
SDKs/probe assets stay blocked, not fabricated. Prepared outputs.guest_package may
name a checksum-proven raw guest.itpack; library import and boot assembly select it
natively when no explicit package is supplied, so firmware UI does not choose an
architecture or recipe.
