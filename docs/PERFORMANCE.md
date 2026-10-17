# Performance log

Every performance change, what it fixed, and how it was measured. Newest first within each
section. Test phones: **Pixel 10** (Tensor G5: 2×A520, 5×A725, 1×X925; GrapheneOS) and
**Poco F3** (Snapdragon 870; LineageOS, Android 16). Guest: iPod touch 2G, iOS 3.1.3
(`n72ap-7E18`).

## How to measure

The harness is in [`tools/perf/`](../tools/perf):

| Tool | Measures |
| --- | --- |
| `inappfps.py` | In-app boot to lock screen, then Home ↔ Spotlight swipes; delivered fps from SurfaceFlinger `--timestats` for the guest SurfaceView (`--idle SECS` adds idle engine CPU). |
| `gamefps.py SECS` | Delivered fps while a game runs on screen. |
| `fcbench.py APK SECS` | Installs an APK, boots, unlocks, starts Flight Control and runs `gamefps.py` (Poco F3 layout). |
| `gameprof.sh` / `inappprof.sh` | `simpleperf` of the `:engine` process during play / swipes: per-thread split, vCPU DSOs and symbols. |
| `abtimer.sh`, `appab.py`, `scrollab.sh` | Interleaved A/B runs. |

"Delivered fps" is frames the guest surface actually presented, from SurfaceFlinger, not a
counter inside the app. Measure in-app (not the standalone engine): the Android scheduler
treats the app's worker process differently, and that turned out to be the largest effect.

## Android

| Change | Commit | Before → after |
| --- | --- | --- |
| Guest timer 4 at the hardware's 6 MHz instead of 10 MHz (Android only) | `e0d81871` | Interleaved A/B ×2 per phone: Poco animation 43.7/45.0 → 50.9/47.2 fps, frames on the 16.7 ms cadence 62% → 75%; Pixel 47.5/50.1 → 50.7/50.1 fps. Idle engine CPU −5 to −13%. Desktop keeps 10 MHz: an older desktop measurement found worse pacing there. |
| Bind the engine service with `BIND_IMPORTANT` | `25d12df7` | The `:engine` worker ran in the *foreground* cgroup: on the Pixel that excludes the X925 prime core and leaves the vCPU's cores near 400 MHz. Now it inherits the activity's *top-app* group. Pixel boot to lock screen 55.8 s → 20.9 s (same as the standalone engine); Poco 42.6 s → 37.4–38.5 s. |
| Deliver frames when the engine publishes them | `09381ce2` | Frames crossed two polling timers (the runtime publisher's 10 ms poll, then a fixed 16 ms sleep *after* each present), so present gaps were never under ~16.7 ms + work and frames were dropped. Now the engine's `qemu_ios_ui_attach` callback wakes the publisher and a new `lt_runtime_set_frame_listener` wakes the presenter. Home ↔ Spotlight: Pixel 29–32 → 50.2–50.6 fps, Poco 36–37 → 44.8–49.2 fps. |
| Keep engine threads on fast cores | `3509e0db` | Android parked the bursty vCPU thread on little cores. Engine threads now get affinity to CPUs with ≥ 50% of the maximum `cpu_capacity`. Standalone boot: Pixel 57–74 s → 19–20 s; Poco 55 s → 36–40 s. |
| Cheaper presenter scaling | `3509e0db` | The Android presenter's per-pixel nearest-neighbour upscale was rewritten to copy runs per source pixel and duplicate rows. |

### Engine (`qemu-ios-android`, branch `dev/perf`)

| Change | Commit | Effect |
| --- | --- | --- |
| Android builds default to API 29 native ELF TLS | `5323ba7cfc` | Removes emulated-TLS calls from every TCG helper that touches thread-locals: ~5–8% faster boot. |
| PKE: keep the Montgomery radix inverse for the loaded modulus | `a41eb8e07b` | The boot's RSA work recomputed it on every operation: ~7% faster boot. |
| Already in the fork before this work (upstream adopted them later) | 909d3ccb62; cdbcff9724, 38642ff7a2, a4749f1ba6 | 4K target pages; learned per-TB I/O instruction limits (no re-translation after mid-block device reads). |

### Measured and rejected

No gain, so not kept: larger or per-ASID TB jump caches (engine branch `exp/asid-jmp-cache`),
a lockless timer counter, the vCPU alone on the prime core, LTO for the Android engine.
The upstream qemu-ios merge brings no speed by itself (its two TCG wins were already here).

## Current state and next targets

| Workload | Pixel 10 | Poco F3 |
| --- | --- | --- |
| Boot to lock screen (in app) | ~21 s | ~37–45 s |
| Springboard Home ↔ Spotlight | ~50 fps | ~47–51 fps |
| Flight Control 1.9.2, in play | — | ~24 fps |

Games are the main gap; see [TODO.md](TODO.md).
