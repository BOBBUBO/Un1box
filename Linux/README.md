The SDL frontend runs a fresh native worker for each session and consumes the
Portable C ABI. It does not assemble machine arguments or duplicate firmware logic.
Control/status use a private Unix socket; frames use a readonly shared memfd and PCM
uses a separate bounded socket. Worker crash and graceful shutdown are distinct.

```sh
cmake -S Linux -B /private/build/linux \
  -DLIGHTTOUCH_QEMU_SOURCE=/path/to/portable-qemu-ios
cmake --build /private/build/linux -j2
mkdir -p /private/devices/n72
/private/build/linux/lighttouch-linux --import /private/n72ap-7E18.itbase \
  --sha256 EXPECTED_HASH --base /private/devices/n72/base
/private/build/linux/lighttouch-linux --base /private/devices/n72/base \
  --state /private/devices/n72/state --engine /private/libqemu-arm.so \
  --rom /private/bootrom_240_4 --package /private/armv6.itpack
```

The n81ap kboot route omits `--rom` and uses the matching armv7 package. `--recover`
permits cold reboot of an interrupted state without deleting it. Without this flag,
dirty state produces an actionable error. Exit code zero requires confirmed guest
shutdown. Escape/window-close requests guest shutdown and waits 30 seconds in the
worker; timeout kills the worker and preserves `.dirty`. Starting again always execs
a fresh worker, never reinitializes QEMU in the SDL process.

Left/right mouse buttons own stable contacts 1/2. Native SDL touch IDs map to stable
per-contact IDs; synthesized touch mouse events are ignored. H/P/+/- control Home,
Power and volume; Space pauses and R rotates the displayed guest. Focus loss and
display rotation cancel all input; native mapping uses the same rotation/letterbox
transform as Android. F6 schedules a clearly
labeled two-contact pinch, not guest acceptance evidence. Arrow keys replay labeled
acceleration only. `--script FILE` runs deterministic production input commands:

```text
# seconds kind arguments
30 touch 1 0 0.3 0.5
30 touch 2 0 0.7 0.5
30.1 touch 1 1 0.2 0.5
30.1 touch 2 1 0.8 0.5
30.2 touch 1 2 0.2 0.5
30.3 touch 2 2 0.8 0.5
31 button 0 1
31.1 button 0 0
32 motion 1 0 9.80665 0 0 0 0
60 stop
```

Touch phases: down=0, move=1, up=2, cancel=3. Motion arguments: field bits (1 accel,
2 gyro), ax/ay/az in m/s², gx/gy/gz in rad/s. Sensor replay is not physical hardware
evidence. Observe actual guest responses to qualify gestures/gyro. No guest gyro
capability is fabricated from host hardware.

`--headless --seconds N --screenshot /private/frame.bmp` exercises the same worker,
input and frame transport without a window. Screenshots require an actual published
guest frame; there is no fallback image. Use `--no-audio` only when testing unrelated
headless behavior; default audio uses the real mixer/SDL path. Keep screenshots,
logs and firmware outside Git. The CLI implements device selection/import/errors;
SDL provides display/input/audio and state in its title, not a full desktop library UI.
