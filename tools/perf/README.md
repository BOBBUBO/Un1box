# Performance harness

Headless benchmarks for the qemu-ios engine on desktop Linux and on Android
phones over adb. Measurements behind the 2026-10-16 `dev/perf` changes.

- `ltbench.py` boots a standalone `qemu-system-arm` (n72ap-7E18) locally or on a
  phone (`adb shell run-as org.lighttouch.android`, using the installed app's
  prepared base). QMP over stdio; screendumps detect the lock screen.
  Workloads: `idle` (per-thread CPU and context switches at the home screen),
  `apps` (open/close latency), `scroll` with `--fling` (frames/s from the
  `ipod_touch_lcd_frame_log` trace while flicking Settings). `--wrap` prefixes
  the engine (e.g. `taskset f0`, `perf stat ...`); `--vcpu-mask` pins only the
  vCPU thread. Set `ANDROID_SERIAL` when several phones are attached.
- `devbench.sh SERIAL RECORD tag:binary:mask[:vcpumask] ...` runs boot + fling
  per spec on one phone; engines go in the app's `files/bench/`.
- `appab.py label=apk ...` installs APKs in turn and times an in-app boot to
  the lock screen (buttons found by text via uiautomator).
- `build-apk.sh ENGINE_DIR NAME` builds an arm64-only debug APK from this
  working tree. Paths are this workstation's; adjust before reuse.

## Results (n72ap-7E18, boot to lock screen / Settings fling fps)

| Host | Original engine, unpinned | dev/perf, big cores |
| --- | --- | --- |
| Poco F3 (SD870) | 55 s / 18-22 | 36-40 s / 25-28 |
| Pixel 10 (Tensor G5) | 57-74 s / 15-24 | 19-20 s / 27-29 |
| Ryzen 3700X | 23-24 s / 40-42 | 21 s (19-20 s with LTO) |

In-app on the Poco: v0.0.1 50-52 s, big-core build 39-41 s.

Measured and rejected: a 16K jump cache; per-ASID jump caches (wipes 20.9K ->
3.2K per boot, no time change; branch `exp/asid-jmp-cache` in the engine);
lock-free timer counter reads; isolating the vCPU on the prime core (worse
animation on the Pixel); LTO on Android (no measurable phone gain).
