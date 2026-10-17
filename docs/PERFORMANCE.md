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
| `fcbench.py APK SECS [GAME]` | Installs an APK, boots, unlocks, starts a game from Home page 2; presets follow each phone's layout (Poco: `flightcontrol`; Pixel: `doodlejump`, `jellycar`; check with `FC_SHOT=file.png`) and runs `gamefps.py`. Debug builds read `files/debug-env` (`KEY=VALUE`), e.g. `LT_CROSS_PAGE_CHAIN=off` or `LT_ADPF=off`, for same-APK A/B runs. |
| `gameprof.sh` / `inappprof.sh` | `simpleperf` of the `:engine` process during play / swipes: per-thread split, vCPU DSOs and symbols. |
| `abtimer.sh`, `appab.py`, `scrollab.sh` | Interleaved A/B runs. |

"Delivered fps" is frames the guest surface actually presented, from SurfaceFlinger, not a
counter inside the app. It counts guest screen updates, not a fixed refresh: a static screen
publishes almost nothing.

**What full speed is.** The iPod touch 2G panel refreshes at 60 Hz, and a game's own cap is
60 or 30 fps. Find a game's cap on a host fast enough to reach it: the Linux frontend runs the
phone's record headless (`lighttouch-linux --library DIR --record ID --engine libqemu-arm.so
--headless --seconds N --no-audio --script FILE`, with timed `touch`/`button`/`screenshot`
lines) and its `status` lines carry the produced frame count. On a Ryzen 7 3700X, Flight
Control 1.9.2 holds **60 fps** throughout play, so it targets 60: the phones' ~18–25 fps are
30–40% of full speed.

Measure in-app (not the standalone engine): the Android scheduler
treats the app's worker process differently, and that turned out to be the largest effect.

## Android

| Change | Commit | Before → after |
| --- | --- | --- |
| Ask for vCPU performance through ADPF (`APerformanceHint`, API 33+): each published frame reports the vCPU threads' CPU time against a 16.67 ms target | `44cfb147` | Pixel 10, JellyCar's live physics menu, same APK with ADPF switched off by `LT_ADPF=off` (three interleaved rounds): 5.7/5.5/6.0 → 13.2/13.4/13.3 fps (2.3×); screenshots confirm the same scene. No effect on the Poco: its LineageOS power HAL has no hint sessions (`dumpsys performance_hint`: `Hint Session Support: false`), and its 4.19 kernel has no uclamp, so an app has no portable substitute there. |
| Guest timer 4 at the hardware's 6 MHz instead of 10 MHz (Android only) | `e0d81871` | Interleaved A/B ×2 per phone: Poco animation 43.7/45.0 → 50.9/47.2 fps, frames on the 16.7 ms cadence 62% → 75%; Pixel 47.5/50.1 → 50.7/50.1 fps. Idle engine CPU −5 to −13%. Desktop keeps 10 MHz: an older desktop measurement found worse pacing there. |
| Bind the engine service with `BIND_IMPORTANT` | `25d12df7` | The `:engine` worker ran in the *foreground* cgroup: on the Pixel that excludes the X925 prime core and leaves the vCPU's cores near 400 MHz. Now it inherits the activity's *top-app* group. Pixel boot to lock screen 55.8 s → 20.9 s (same as the standalone engine); Poco 42.6 s → 37.4–38.5 s. |
| Deliver frames when the engine publishes them | `09381ce2` | Frames crossed two polling timers (the runtime publisher's 10 ms poll, then a fixed 16 ms sleep *after* each present), so present gaps were never under ~16.7 ms + work and frames were dropped. Now the engine's `qemu_ios_ui_attach` callback wakes the publisher and a new `lt_runtime_set_frame_listener` wakes the presenter. Home ↔ Spotlight: Pixel 29–32 → 50.2–50.6 fps, Poco 36–37 → 44.8–49.2 fps. |
| Keep engine threads on fast cores | `3509e0db` | Android parked the bursty vCPU thread on little cores. Engine threads now get affinity to CPUs with ≥ 50% of the maximum `cpu_capacity`. Standalone boot: Pixel 57–74 s → 19–20 s; Poco 55 s → 36–40 s. |
| Cheaper presenter scaling | `3509e0db` | The Android presenter's per-pixel nearest-neighbour upscale was rewritten to copy runs per source pixel and duplicate rows. |

### Engine (`qemu-ios-android`, branch `dev/perf`)

| Change | Commit | Effect |
| --- | --- | --- |
| **Get the vCPU off the BQL** (four commits, below). The vCPU waited for QEMU's big lock ~26% of wall time in Flight Control on the Poco, ~80% of it in waits over 100 µs behind main-loop holds; it took the lock ~35K times/s. After: ~5K/s, 9% of wall. | engine `80c5b311f9`, `6622f7ab69`, `76ace3a65b`, `974c213352` | Same engine before/after (`256d81f9fb` vs `974c213352`), interleaved 30 s rounds. **Poco, Flight Control: 24.0/24.0/25.4/27.1/26.2 → 34.7/36.1/35.3/34.8/36.0 fps (25.3 → 35.4, +40%), 5/5 pairs. Pixel, Doodle Jump menu: 17.3/19.5/17.6 → 30.7/31.9/34.1 (18.1 → 32.2, +78%), 3/3.** **Pixel, JellyCar menu: 14.8/14.7/14.7 → 23.6/23.3/23.1 (14.7 → 23.3, +58%), 3/3** (its full speed is 30). Desktop full-speed runs still hold 60/60/30 fps (Flight Control/Doodle Jump/JellyCar) with no GL refusals. Per-fix split: one APK per commit, Poco Flight Control, two interleaved rounds: before 25.3 (the five rounds above) → exceptions 27.1/26.4 (+1.5) → tick counter 25.6/25.9 (±0, within noise; kept, as it removes ~18K lock takes/s) → GL calls 33.0/32.8 (+7.1, the big one) → audio clock 34.3/36.7 (+2.6). |
| ↳ Exceptions and exception returns without the BQL | `80c5b311f9` | iOS takes an exception per syscall, page fault and IRQ. `cpsr_write_eret` locked twice per return for EL-change hooks the ARM1176 does not have; `cpu_handle_exception` locked for `do_interrupt`, which is CPU-local without hooks (new `TCGCPUOps.do_interrupt_needs_bql`); `cpu_handle_interrupt` locked only to clear the EXITTB every exception entry sets (now an atomic clear). |
| ↳ Tick counter reads without the BQL | `6622f7ab69` | cputlb now honours `lockless_io` like physmem; timer1 opts in and only its TICKSHIGH/LOW reads (~18K/s) skip the lock. |
| ↳ Guest GL calls without the BQL | `76ace3a65b` | Every `gl*` call (`QC_GLES` via the `QEMU_CALL` cp15 register, ~5K/s) was `ARM_CP_IO`: the vCPU waited for the lock per call, then held it through the host GL work, delaying the main loop's timers. Only plain GL calls skip it; the bridge state other threads read got its own small locks. |
| ↳ No clock read per audio FIFO element | `974c213352` | The I2S model read the virtual clock for each 4-byte element the PL080 DMA pushed (~44K/s), for a stall-trace timestamp: a fifth of the main loop's CPU, all under the BQL, and the main loop's long holds are what the vCPU waited behind. |
| Chain direct jumps across guest pages (`-accel tcg,cross-page-chain=on`, passed by every port) | engine `53744be61d` | System-mode QEMU never chains a jump to another guest page, because the page could be remapped to other code while the TB found there stays valid. iOS 3 never does that (static kernel text, code-signed user code at fixed addresses, no JIT/ASLR/aliases; a reused page is written first, which invalidates its TBs). A third of TB lookups in Flight Control were such jumps (0.66M of 2M/s). Flight Control, interleaved: Poco 17.3/21.3/19.3/19.0/19.4/22.2 → 27.5/23.9/21.4/25.3/23.7/25.2 fps (means 19.8 → 24.5, +24%); Pixel (Doodle Jump's animated menu; the Pixel's page 2 differs) 17.6/14.0/14.2 → 18.8/17.4/17.8 (+18%); won all 9 pairs. Kernel-only chaining gave about half of it. JellyCar's menu on the Pixel did not move (13.7 → 14.0), so that scene is bound elsewhere. |
| Hash cs_base into the jump cache; TLBIASID empties only that ASID's jump cache (and the TLB only if it is current) | `68d4df35e4` | Jump-cache misses in Flight Control 20.1% → 10.2% of lookups; whole-cache flushes ~20/s → 0. Poco, three interleaved rounds: 19.3/16.6/20.0 → 20.8/18.8/20.5 fps (+7%). A third of the misses were one PC with two TBs (iOS lazy VFP flips FPEXC.EN, which is in cs_base) evicting each other. |
| Look up the accelerometer/multitouch devices once instead of walking the QOM tree per event | `15c120f16f` | Each 100 Hz tilt sample ran `object_resolve_path_type` under the BQL: about a quarter of the main loop's CPU in Flight Control. |
| 16K-entry TB jump cache (128 page groups) | `0584dbc9cc` | Flight Control, Poco, three interleaved rounds: 4K 18.8/19.5/20.7 → 16K 22.0/20.9/21.9 fps (+10%); 8K was in between. |
| One TB jump cache per ARM ASID (16 per CPU, lazy epoch clearing) instead of emptying the only one on every context switch | `f9fe5bfd74` | Flight Control play on the Poco (`fcbench.py`, three interleaved 30 s rounds): 18.1/18.1 → 20.1/19.7/20.2 fps, +10%. No change to boot time, which is why it was first parked. A 64K-entry geometry was worse in play (16.8/16.8/18.0). |
| Android builds default to API 29 native ELF TLS | `5323ba7cfc` | Removes emulated-TLS calls from every TCG helper that touches thread-locals: ~5–8% faster boot. |
| PKE: keep the Montgomery radix inverse for the loaded modulus | `a41eb8e07b` | The boot's RSA work recomputed it on every operation: ~7% faster boot. |
| Already in the fork before this work (upstream adopted them later) | 909d3ccb62; cdbcff9724, 38642ff7a2, a4749f1ba6 | 4K target pages; learned per-TB I/O instruction limits (no re-translation after mid-block device reads). |

### Measured and rejected

No gain, so not kept: a 64K-entry TB jump cache (slower than 4K in play), a lockless timer counter,
LTO for the Android engine, and the vCPU alone on the prime core. Unpinned, the vCPU spends only ~29%
of a game on the Poco's 3.19 GHz prime core, which looks like a win to take, but pinned there
(re-pinned each second, because core_ctl drops single-core masks) schedutil held the prime core
at 1.6–1.9 GHz and Flight Control fell to 9 fps (Pixel animation was also worse earlier).
The upstream qemu-ios merge brings no speed by itself (its two TCG wins were already here).
A 2-way set-associative jump cache halved jump-cache misses in Flight Control (10.6% → 5.4% of
lookups) but did not move frame rate: Poco 23.3/22.3/22.5 → 25.1/21.0/19.6 fps, Pixel JellyCar
14.0/13.8/14.9 → 13.7/14.3/13.0. Misses are no longer where lookup time goes: at ~2M lookups/s
the *hit* path (`tb_lookup` 12%, `helper_lookup_tb_ptr` 6%) costs more than the remaining qht
lookups (5.6%), mostly cache misses on the jump-cache entry and the TB it points to.

### Where game time goes

Debug builds can log the vCPU's time split: `adb shell run-as org.lighttouch.android touch
files/debug-vcpu-accounting`, start the guest, and read the `[acct]` lines in the record's
`state/engine-PID.log`. The `run`/`halt`/`io (bql …)` split on each line understates lock time: its "bql" covers only
the halt path. The `vcpu-bql`, `bql-by-mr`, `bql-wait-hist`, `bql-holder` and `bql-hold-hist` lines
(engine `256d81f9fb`) time every vCPU acquisition by MMIO region or call site, and the other threads'
holds by taker; `tools/perf/bqlsites.py LOG T0 T1` turns two dumps into per-site rates. They showed
Flight Control's vCPU waiting for the BQL ~26% of wall time before the BQL work above, ~9% after. The host GL calls are
~2% of the vCPU. With 4K jump caches, TB lookup was ~26% of vCPU time (`qht_lookup_custom`
12.6%, `helper_lookup_tb_ptr` 9.2%, `tb_lookup_cmp` 4.1%).
The `[acct]` lines also count jump-cache lookups and misses, split into empty slots, another PC in
the slot, and the same PC with different `cs_base`/flags (with the differing bits), plus whole-cache
flushes; `68d4df35e4` was found this way. Guest-side attribution: put `QEMU_PERFMAP_DIR=/data/data/org.lighttouch.android/files`
in `files/debug-env` (debug builds read `KEY=VALUE` lines into the engine's environment), profile
with `tools/perf/gameprof.sh`, and map JIT samples to guest PCs with the perf map. In Flight Control
the guest's time is ~50% kernel (IPC, scheduling, timers), ~41% shared cache, ~8.5% the game itself.

## Current state and next targets

Game frame rates, from the measurements above (full speed from the desktop runs; "—" was not
measured at that point):

| Game (scene) | Phone | Full speed | v0.0.2 | Before cross-page chaining | Before the BQL work | Now (engine `974c213352`) |
| --- | --- | --- | --- | --- | --- | --- |
| Flight Control 1.9.2 (first 30 s of play) | Poco F3 | 60 | ~18 | 19.8 | 25.3 | **35.4** |
| Doodle Jump (animated main menu) | Pixel 10 | 60 | — | 15.3 | 18.1 | **32.2** |
| JellyCar (live-physics main menu) | Pixel 10 | 30 | — (5.7 before ADPF) | 13.7 | 14.7 | **23.3** |

| Workload | Pixel 10 | Poco F3 |
| --- | --- | --- |
| Boot to lock screen (in app) | ~21 s | ~37–45 s |
| Springboard Home ↔ Spotlight | ~50 fps | ~47–51 fps |

The app shows the guest's fps next to the frame counter, and the Performance panel graphs the last
60 s (`b11ef28b`); `fcbench.py` with `FC_SERIES=1` prints fps per second.

Games are the main gap; see [TODO.md](TODO.md).
