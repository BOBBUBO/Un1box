# Performance log

Every performance change, what it fixed, and how it was measured. Newest first within each
section. Test phones: **Pixel 10** (Tensor G5: 2×A520, 5×A725, 1×X925; GrapheneOS) and
**Poco F3** (Snapdragon 865-class; LineageOS, Android 16), the low-end target. Guest: iPod touch 2G, iOS 3.1.3
(`n72ap-7E18`).

## How to measure

The harness is in [`tools/perf/`](../tools/perf):

| Tool | Measures |
| --- | --- |
| `inappfps.py` | In-app boot to lock screen, then Home ↔ Spotlight swipes; delivered fps from SurfaceFlinger `--timestats` for the guest SurfaceView (`--idle SECS` adds idle engine CPU). |
| `gamefps.py SECS` | Delivered fps while a game runs on screen. |
| `fcbench.py APK SECS [GAME]` | Installs an APK, boots, unlocks, starts a game from Home page 2; presets follow each phone's layout (Poco: `jetpack`, `flightcontrol`; Pixel: `jellycar`, `doodlejump`; a game that holds a steady 60 on both phones leaves the benchmark set, so Flight Control, Doodle Jump and JellyCar are no longer run; check with `FC_SHOT=file.png`) and runs `gamefps.py`. Debug builds read `files/debug-env` (`KEY=VALUE`), e.g. `LT_CROSS_PAGE_CHAIN=off` or `LT_ADPF=off`, for same-APK A/B runs. |
| `framestats.py` | Frame pacing from a per-frame log: the **ceiling** (95th percentile of 500 ms windows: what the game sustains when nothing gets in the way), median, **low** (5th percentile: how deep the dips go), 1% low, **hitches** (frames over 50 ms), share of time at ≥55 / <30 fps, and a chart. `fcbench.py` with `FC_FRAMES=chart.png` turns on the runtime's `LT_FRAME_LOG` and reports it for the same window. Given the engine's `IT_FRAME_TRACE` (with `IT_VCPU_ACCT` and `IT_GLES_PROF`) it also splits each class of frame (on time / late / hitch) into guest code, GL, BQL wait, idle and off-CPU time. |
| `guestsyslog.py` | Desktop: the guest's syslog during a headless run (through the app's USB bridge), counted by sender and text. Every line an app writes to stderr becomes a syslogd message the guest has to relay and parse. |
| `androidsyslog.py` | Android: bounded guest `syslog_relay` capture through the private USB bridge (`--seconds 1..600`). Preserves raw bytes/NULs, receive bounds in Android monotonic time, target/worker identity and completion counts. Message arrival can lag execution or include backlog; receive/frame overlap alone does not establish causation. |
| `tallypath.py` | Direct `shim-tally` markers within a complete sparse frame-path capture: compares periodic tally post-present intervals with other intervals and lists hitch overlaps. These intervals include guest work and scheduling; they do not time stderr writes alone. |
| `guestprocs.py` | LLDB snapshot of guest process/task/pmap pointers from the matching kernel, detached after capture. Optional repeatable `--thread 0xADDRESS` reads a selected thread's task and cross-checks its BSD process owner. This optional ownership lookup is prepared but its first phone attachment remains pending; see PROFILING.md for interpretation and lifetime limits. No performance result is attributed to it. |
| `gameprof.sh` / `inappprof.sh` | `simpleperf` of the `:engine` process during play / swipes: per-thread split, vCPU DSOs and symbols. |
| `abtimer.sh`, `appab.py`, `scrollab.sh` | Interleaved A/B runs. |

"Delivered fps" is frames the guest surface actually presented, from SurfaceFlinger, not a
counter inside the app. It counts guest screen updates, not a fixed refresh: a static screen
publishes almost nothing.

Keep the measured frame samples with `framestats.py --device 30 --save frames.log`; the saved
window can be analysed again with `framestats.py frames.log --secs 30`. Use `fcbench.py APK 0 ltbench`
to launch LT Bench without sampling fps on its static results screen.

**What full speed is.** The iPod touch 2G panel refreshes at 60 Hz, and a game's own cap is
60 or 30 fps. Find a game's cap on a host fast enough to reach it: the Linux frontend runs the
phone's record headless (`lighttouch-linux --library DIR --record ID --engine libqemu-arm.so
--headless --seconds N --no-audio --script FILE`, with timed `touch`/`button`/`screenshot`
lines) and its `status` lines carry the produced frame count. On a Ryzen 7 3700X, Flight
Control 1.9.2 holds **60 fps** throughout play, so it targets 60: the phones' ~18–25 fps are
30–40% of full speed.

Measure in-app (not the standalone engine): the Android scheduler
treats the app's worker process differently, and that turned out to be the largest effect.

**Run-to-run spread on the Poco.** Identical runs of Flight Control land anywhere from ~51 to
~56 fps. It is not core placement: sampling the vCPU thread every 250 ms over six runs found it on
the prime core (cpu7) 95–100% of the time in every run. That core's clock, chosen by schedutil
under the ROM's 2.75 GHz cap, averaged 2.42–2.51 GHz from run to run, and fps roughly follows it
(2.51 GHz: 55.0 fps; 2.42 GHz: 53.1). A change worth less than ~3% needs many interleaved pairs, or
a direct measure of the work it removes (time per call, per frame) alongside the frame rate.

## Android

| Change | Commit | Before → after |
| --- | --- | --- |
| Present guest frames on the display's vsync: the engine stamps each frame with the vsync it was latched on (engine `13b651b479`), and the presenter posts it at that time plus a steady latency, moved to just after a display vsync from Choreographer; it also asks for 60 Hz fixed-source content and turns off Android 15's touch boost | `daa3b042` | On a 120 Hz panel each 60 Hz guest frame has to land on the right one of two refreshes; posting on arrival handed the engine's ±2 ms jitter to SurfaceFlinger, which held ~30% of frames for one refresh or three: stutter at a full 60 fps. Pixel 10 at 120 Hz, Doodle Jump menu, SurfaceFlinger present gaps: **1232 at 16 ms, 273 at 8 ms, 270 at 24/25 ms (30% mis-paced) → 1739–1754 at 16 ms, 8–14 mis-paced (0.3–0.8%)**, 59.5–59.9 fps both. Spacing posts evenly without the vsync snap left 9%; the 60 Hz request alone left 27% (the Pixel keeps the panel at 120 Hz, which it counts as compatible). The Pixel 10 has no adaptive-refresh support (`hasArrSupport false`), so the presenter follows the panel's fixed vsync instead. |
| Run the panel at 60 Hz while LightTouch is in front (the window's preferred display mode, as Google Maps does) | `b33726c7` | With Smooth Display at 120 Hz the Pixel 10 kept 120 Hz for the 60 Hz fixed-source request alone; the preferred mode switches it to 60 (`renderFrameRate 60`). Doodle Jump menu, SurfaceFlinger present gaps: **1796 at 16 ms, 5 at 17, 9 at 33, none at 8 or 24** (vs 0.3–0.8% mis-paced with vsync snapping at 120 Hz); 59.8 fps, 99% of the time at ≥ 55 fps. |
| Release the 60 Hz cap and touch-boost lock while the Store or a dialog covers the guest | `21c96bba` | The cap applied to the whole window, so the Legacy Store scrolled at a fixed 60 Hz with no touch boost on 120 Hz phones and looked choppy (reported on the Pixel 10: `activeMode 60 Hz` with the Store open). Poco F3, the same 10 flings of the Store's results: panel 60 → 120 Hz, 651 → 1088 frames drawn, 99th percentile 17 ms both; back to 60 Hz on Done. |
| Ask for vCPU performance through ADPF (`APerformanceHint`, API 33+): each published frame reports the vCPU threads' CPU time against a 16.67 ms target | `44cfb147` | Pixel 10, JellyCar's live physics menu, same APK with ADPF switched off by `LT_ADPF=off` (three interleaved rounds): 5.7/5.5/6.0 → 13.2/13.4/13.3 fps (2.3×); screenshots confirm the same scene. No effect on the Poco: its LineageOS power HAL has no hint sessions (`dumpsys performance_hint`: `Hint Session Support: false`), and its 4.19 kernel has no uclamp, so an app has no portable substitute there. |
| Guest timer 4 at the hardware's 6 MHz instead of 10 MHz (Android only) | `e0d81871` | Interleaved A/B ×2 per phone: Poco animation 43.7/45.0 → 50.9/47.2 fps, frames on the 16.7 ms cadence 62% → 75%; Pixel 47.5/50.1 → 50.7/50.1 fps. Idle engine CPU −5 to −13%. Desktop keeps 10 MHz: an older desktop measurement found worse pacing there. |
| Bind the engine service with `BIND_IMPORTANT` | `25d12df7` | The `:engine` worker ran in the *foreground* cgroup: on the Pixel that excludes the X925 prime core and leaves the vCPU's cores near 400 MHz. Now it inherits the activity's *top-app* group. Pixel boot to lock screen 55.8 s → 20.9 s (same as the standalone engine); Poco 42.6 s → 37.4–38.5 s. |
| Deliver frames when the engine publishes them | `09381ce2` | Frames crossed two polling timers (the runtime publisher's 10 ms poll, then a fixed 16 ms sleep *after* each present), so present gaps were never under ~16.7 ms + work and frames were dropped. Now the engine's `qemu_ios_ui_attach` callback wakes the publisher and a new `lt_runtime_set_frame_listener` wakes the presenter. Home ↔ Spotlight: Pixel 29–32 → 50.2–50.6 fps, Poco 36–37 → 44.8–49.2 fps. |
| Keep engine threads on fast cores | `3509e0db` | Android parked the bursty vCPU thread on little cores. Engine threads now get affinity to CPUs with ≥ 50% of the maximum `cpu_capacity`. Standalone boot: Pixel 57–74 s → 19–20 s; Poco 55 s → 36–40 s. |
| Cheaper presenter scaling | `3509e0db` | The Android presenter's per-pixel nearest-neighbour upscale was rewritten to copy runs per source pixel and duplicate rows. |

### Engine (`qemu-ios`, branch `dev/perf`)

| Change | Commit | Effect |
| --- | --- | --- |
| Guest GL shim: report each CoreAnimation surface once instead of on every frame (guest package serial 27) | `7f744e80cc` | `surface_capture` in the guest's OpenGLES logged two lines per surface CA handed out, and CA rotates its buffers, so every frame cost ~12 `write(2)`s plus ~12 `GLES_OP_LOG` traps, and launchd relayed each write to syslogd as a message of its own, which syslogd parsed with regexes (`asl_parse_time` → `regexec`: the top shared-cache page in a Poco Flight Control profile). Found by sampling guest stacks at IRQs, naming the addresses with LT Bench `-DSYMS`, and reading the guest syslog through `syslog_relay` (6348 lines in 66 s of Flight Control, all but ~300 these). Measured with the shipped OpenGLES binary-patched to skip the lines vs unpatched, each installed into a copy of the same record. **Desktop, Flight Control at 57.4–57.8 fps either way: vCPU busy 72.2/69.3/71.3/73.5% → 50.9/54.3/52.1% of wall (~3.3 ms less guest work per frame). Desktop, Jetpack title: 55.7/56.9/53.6/56.0 → 59.2/58.9/58.8/58.9 fps (vCPU 90–94% → 73–77%). Poco, Flight Control, four interleaved pairs: 55.4/57.5/56.1/57.1 → 59.0/57.2/58.6/58.1 fps (56.5 → 58.2); low 46–48 → 52–56, hitches 9–16 → 1–9 per 30 s, ≥55 fps 63–83% → 85–97% of the time.** **Poco, Jetpack title, three interleaved pairs: 39.6/36.1/35.5 → 51.3/50.9/48.6 fps (37.1 → 50.3, +36%); ceiling 44–48 → 54–56, low 26–32 → 42–46, hitches 117–121 → 2–11 per 30 s** (screenshots: the same title scene). Jetpack draws more and presents the same, so the fixed per-frame cost was a larger share of its frame, and its hitches were the syslog work bunching up. **The real build (serial 27, Xcode 27.1, installed into the record and kept by the new package verdicts), Poco, verdict APK (`995ce258`) on engine `64d111110b`, two runs each against the record's own package: Jetpack title 39.4/39.4 → 50.4/50.7 fps (ceiling 48 → 56, low 30–32 → 44–46, 1% low 12 → 21–28, hitches 114 → 6–7 per 30 s); Flight Control 55.1/55.4 → 58.8/59.1 (median 56 → 60, ≥55 fps 58–66% → 98–100% of the time, hitches 12–15 → 1).** **Pixel, same build and APK: JellyCar menu 30.0/30.0 → 30.0/30.0/30.0 (its 30 fps target either way; hitches 147/41 → 8/45 per 30 s, too noisy to call); Doodle Jump menu 58.8/59.7 → 58.0/57.6 (no hitches either way; ≥55 fps 95–100% → 87–90% of the time, a small loss or noise, not chased: Doodle Jump has left the benchmark set).** Serial 27 stayed installed through six boots on the Pixel and five on the Poco (`state/guest-verdicts`: `good 27`). Serial 27 can now be installed in place through Linux `--set-guest-package` or Android "Guest tools" on a stopped device. It remains private pending the certificate and source license work in TODO. |
| CPSR writes that change only the interrupt masks or flags skip the hflags rebuild; `cpsid` no longer ends the TB; `VMRS APSR_nzcv` sets the flags inline (`LT_PSR_FAST=off` for A/B) | `0addc3e156` | iOS's kernel masks and restores interrupts around every lock (`hw_lock_try`: `cpsid if` … `msr CPSR_fc`). A Jetpack profile on the Poco put the hflags rebuild at ~3% of the vCPU and the guest kernel at 22%. **LT Bench, Poco, two interleaved pairs, best ns/op off → on: `syscall` 1165 → 991, `thread-switch` 79.6 → 70.2 µs, `rpc` 18.2 → 14.4 µs, `fault` 22.0 → 16.1 µs; compute tests unchanged.** Jetpack alone: 49.6 fps either way (within the spread). |
| QEMU_CALL request blocks (every GL call) read and written through the TLB instead of a debug page-table walk (`LT_QCALL_FAST=off`) | `2df170ca78` | A Poco `gl-sprite` profile had ~12% of the vCPU in `get_phys_addr`, `S1_ptw_translate`, `address_space_*` and the like: two full walks per GL call. **LT Bench, best ns/op off → on: Poco `gl-get` 3778/3608 → 1823, `gl-sprite` 19 736/20 569 → 15 731; Pixel `gl-get` 1024/915 → 619/377, `gl-sprite` 5557/5470 → 3755/3515.** **With `0addc3e156`, Poco Jetpack title, three interleaved pairs, both off → both on: 51.4/48.6/48.1 → 51.9/50.9/53.8 fps (49.4 → 52.2, +6%, ahead in every pair; low 36–46 → 42–50).** |
| GL calls that carry guest pointers copy through the TLB's host pointers instead of a page-table walk per page | `924e6839d5` | `gles_guest_rw` probed each page through the caller's TLB, then re-read it with `cpu_memory_rw_debug`, which walks the page tables again. Plain-RAM pages are now copied through the host pointers the probe returns. Poco, Jetpack Joyride title (232 client-array draws a frame): binding a draw's arrays 4.5 → 2.0 µs. Measured with the next row. |
| ES hosts: skip the rectangle-texture setup per draw when no rectangle texture is in use | `1997c1a488` | Every ES1 draw re-read, re-bound and re-enabled `GL_TEXTURE_2D` on each fixed-function unit and restored it afterwards (~10 driver calls per unit), dirtying texture state the Adreno driver then revalidated. Host `glDrawArrays` 8.8 → 5.9 µs; with the row above a draw costs ~9.4 µs instead of ~14.7 (~1.2 ms less per Jetpack frame). **Poco, interleaved 30 s runs: Jetpack 34.3/34.7/34.1/34.6 → 30.8/37.3/34.2/38.2 fps (34.4 → 35.1, ceiling 40 → 42–46); Flight Control 54.5/56.9/52.1 → 56.8/56.2/55.3 (54.5 → 56.1, hitches 17–42 → 8–23 per 30 s).** |
| ES hosts: cache texture name lookups | `64d111110b` | Each bind walked the list of every texture in the share group. Jetpack title, ~90 binds a frame: `glBindTexture` 4.5 → 1.6 µs. |
| GLES on Android: render the drawable into an AHardwareBuffer and copy the present out of it (`LT_GLES_AHB=0` for A/B) | `4de4ae5ba1` | The Android counterpart of the iOS host's IOSurface path: the color texture gets gralloc BGRA storage through an EGLImage (RGBA plus a per-row byte swap where BGRA is refused), so a present waits for the GPU, locks the buffer and copies its rows, instead of `glReadPixels` into a staging buffer and copying that. **Poco, Flight Control, per present: glReadPixels ~0.85–1.0 ms + copy 0.34 ms → lock 0.14 ms + copy 0.37 ms (~1.3 → ~0.5 ms of vCPU time per frame).** Frame rate, four interleaved pairs: 53.4 → 53.8 fps (55.2/51.0/55.8/51.4 vs 51.0/55.8/54.4/53.9), inside the run-to-run spread. Not yet tried on the Pixel (Tensor G5); it falls back to the readback if its gralloc or EGL refuses. |
| GLES on ES hosts: read the frame back as BGRA without a byte loop (`LT_GLES_BGRA_READ=0` for A/B) | `16c09e7fe3` | Every present read the drawable back by reading RGBA into a fresh 600 KB allocation and swizzling it a byte at a time (~2% of the vCPU in a game, plus first-touch faults on the new pages). Now `GL_EXT_read_format_bgra` straight into the destination, else a kept buffer swapped a word at a time. **Poco, Flight Control, same APK, 4 interleaved pairs: 51.9/54.7/51.7/54.3 vs 49.1/53.2/49.6/45.0 fps (53.2 vs 49.2, +8%, 4 of 4); ≥55 fps 48% vs 28% of windows; hitches 34 vs 53 per 30 s.** |
| GLES on ES hosts: convert uploads whose internal format differs from the data's | `4ae00eaca8` | Not a speed change: the white/grey untextured geometry in Jetpack Joyride, Battle Bears and Real Racing on Android (opaque PVRTC uploaded as `GL_RGB` from RGBA data, refused by ES). |
| LCD: convert and compose frames without the BQL | `d7c4927685` | The 60 Hz vsync push converted (or composed and blitted) the whole scanout holding the BQL: 1.1 ms per refresh on the Poco, 5.7% of wall time, and the vCPU, which takes the BQL thousands of times a second for the timer, VIC and interrupts, kept landing behind it (its waits over 1 ms were 13% of wall). Now only the dirty snapshot and `dpy_gfx_update` hold the lock. Same app, engine before (`ad729e45fe`) vs the blit unlock alone, four interleaved 30 s rounds per phone, all 8 pairs won: **Poco, Flight Control 37.1/38.5/36.1/37.3 → 40.4/43.4/45.5/43.4 fps (37.3 → 43.2, +16%); ceiling 46 → 52, hitches 111 → 77 per 30 s. Pixel, Doodle Jump menu 32.8/30.6/34.3/33.2 → 38.7/40.8/36.9/37.7 (32.7 → 38.5, +18%); ceiling 45 → 51.** The same commit also unlocks plane composition (Doodle Jump's scanout is two composed planes) and copies opaque pixels instead of blending them; desktop vCPU BQL wait in Doodle Jump 6.9% → 1.4% of wall. With that and the PL080 change below (`a62f05bae3`) vs the blit unlock alone, four interleaved rounds per phone: **Pixel, Doodle Jump menu 36.1/39.6/40.8/35.2 → 52.6/58.0/51.7/59.5 fps (37.9 → 55.5, +46%, 4/4); frames over 50 ms ~94 → 0–3 per 30 s, low 26 → 44–56, up to 97% of the time at ≥ 55 fps. Poco, Flight Control 40.0/39.8/40.8/42.2 → 43.8/48.5/45.3/41.0 (40.7 → 44.7, +10%, 3/4).** |
| PL080: move a paced FIFO's burst in one push | `a62f05bae3` | The I2S pacing timer (every 2 ms) let the PL080 DMA move audio one 4-byte element per MMIO dispatch: 129 µs a tick under the BQL on the Poco, 4.7% of wall while a game plays music. The DMA now reads the run from guest memory once and hands it to the I2S FIFO in one call (the last element still takes the normal path, which lowers the request line and finishes the descriptor). Desktop: the pacing callback 9.9 µs; the PCM tap still carries the audio. |
| Keep the kernel's global TLB entries across an ASID switch | `ad729e45fe` | QEMU's TLB is not ASID-tagged, so each CONTEXTIDR write (~1.5K/s under iOS) flushed everything, including the kernel's global mappings: EL1 high-half refills were about half of all TLB fills. The short-descriptor walk now reports nG and the switch drops only the few non-global pages the EL1 index picked up. Desktop: kernel fills −79%, all fills −42%. Interleaved 30 s rounds: Poco, Flight Control 36.5/36.7/36.6/34.2 → 36.6/38.9/38.8/33.9 fps (36.0 → 37.1, +3%; 2 won, 2 even); Pixel, Doodle Jump menu 32.0/32.4/32.1 → 35.1/34.6/30.3 (32.2 → 33.3, +3.6%; 2 of 3). |
| **Get the vCPU off the BQL** (four commits, below). The vCPU waited for QEMU's big lock ~26% of wall time in Flight Control on the Poco, ~80% of it in waits over 100 µs behind main-loop holds; it took the lock ~35K times/s. After: ~5K/s, 9% of wall. | engine `80c5b311f9`, `6622f7ab69`, `76ace3a65b`, `974c213352` | Same engine before/after (`256d81f9fb` vs `974c213352`), interleaved 30 s rounds. **Poco, Flight Control: 24.0/24.0/25.4/27.1/26.2 → 34.7/36.1/35.3/34.8/36.0 fps (25.3 → 35.4, +40%), 5/5 pairs. Pixel, Doodle Jump menu: 17.3/19.5/17.6 → 30.7/31.9/34.1 (18.1 → 32.2, +78%), 3/3.** **Pixel, JellyCar menu: 14.8/14.7/14.7 → 23.6/23.3/23.1 (14.7 → 23.3, +58%), 3/3** (its full speed is 30). Desktop full-speed runs still hold 60/60/30 fps (Flight Control/Doodle Jump/JellyCar) with no GL refusals. Per-fix split: one APK per commit, Poco Flight Control, two interleaved rounds: before 25.3 (the five rounds above) → exceptions 27.1/26.4 (+1.5) → tick counter 25.6/25.9 (±0, within noise; kept, as it removes ~18K lock takes/s) → GL calls 33.0/32.8 (+7.1, the big one) → audio clock 34.3/36.7 (+2.6). |
| ↳ Exceptions and exception returns without the BQL | `80c5b311f9` | iOS takes an exception per syscall, page fault and IRQ. `cpsr_write_eret` locked twice per return for EL-change hooks the ARM1176 does not have; `cpu_handle_exception` locked for `do_interrupt`, which is CPU-local without hooks (new `TCGCPUOps.do_interrupt_needs_bql`); `cpu_handle_interrupt` locked only to clear the EXITTB every exception entry sets (now an atomic clear). |
| ↳ Tick counter reads without the BQL | `6622f7ab69` | cputlb now honours `lockless_io` like physmem; timer1 opts in and only its TICKSHIGH/LOW reads (~18K/s) skip the lock. |
| ↳ Guest GL calls without the BQL | `76ace3a65b` | Every `gl*` call (`QC_GLES` via the `QEMU_CALL` cp15 register, ~5K/s) was `ARM_CP_IO`: the vCPU waited for the lock per call, then held it through the host GL work, delaying the main loop's timers. Only plain GL calls skip it; the bridge state other threads read got its own small locks. |
| ↳ No clock read per audio FIFO element | `974c213352` | The I2S model read the virtual clock for each 4-byte element the PL080 DMA pushed (~44K/s), for a stall-trace timestamp: a fifth of the main loop's CPU, all under the BQL, and the main loop's long holds are what the vCPU waited behind. |
| Keep two targets per indirect branch site | engine `bd7ceb2003` | A return or table jump alternating between two targets missed the one-entry cache every other time. **Poco, Flight Control, four interleaved pairs: 49.3 → 49.9 fps (+1%), 3 of 4 ahead.** Within noise; kept because it is never behind and costs one more compare on a miss. |
| Cache each indirect branch's last target in its TB (`-accel tcg,indirect-cache=on`, passed by every port; `LT_INDIRECT_CACHE=off` for A/B) | engine `6c789b3e83` | Every ARM TB ending in BX / a PC load called `helper_lookup_tb_ptr` (~2M/s in Flight Control on the Poco, ~20% of vCPU time, mostly a cache miss on the jump-cache entry and another on the TB it names). A desktop simulation found 59% of those jumps go where the same site went last time. Each such exit now keeps a one-entry cache in its own TB, keyed by target PC, Thumb bit and the address space's context id (renewed whenever that jump cache empties or any TB is invalidated); a hit is a load, a compare and a jump. **Poco, Flight Control, same APK, interleaved: 42.6/43.1/46.3/43.5/45.8 → 45.4/47.9/45.4/49.0/49.6/50.5 fps (44.3 → 48.0, +8%), 5 of 6 pairs; ceiling 52–56 → 54–60, hitches 60–92 → 43–79 per 30 s.** |
| Chain direct jumps across guest pages (`-accel tcg,cross-page-chain=on`, passed by every port) | engine `53744be61d` | System-mode QEMU never chains a jump to another guest page, because the page could be remapped to other code while the TB found there stays valid. iOS 3 never does that (static kernel text, code-signed user code at fixed addresses, no JIT/ASLR/aliases; a reused page is written first, which invalidates its TBs). A third of TB lookups in Flight Control were such jumps (0.66M of 2M/s). Flight Control, interleaved: Poco 17.3/21.3/19.3/19.0/19.4/22.2 → 27.5/23.9/21.4/25.3/23.7/25.2 fps (means 19.8 → 24.5, +24%); Pixel (Doodle Jump's animated menu; the Pixel's page 2 differs) 17.6/14.0/14.2 → 18.8/17.4/17.8 (+18%); won all 9 pairs. Kernel-only chaining gave about half of it. JellyCar's menu on the Pixel did not move (13.7 → 14.0), so that scene is bound elsewhere. |
| Hash cs_base into the jump cache; TLBIASID empties only that ASID's jump cache (and the TLB only if it is current) | `68d4df35e4` | Jump-cache misses in Flight Control 20.1% → 10.2% of lookups; whole-cache flushes ~20/s → 0. Poco, three interleaved rounds: 19.3/16.6/20.0 → 20.8/18.8/20.5 fps (+7%). A third of the misses were one PC with two TBs (iOS lazy VFP flips FPEXC.EN, which is in cs_base) evicting each other. |
| Look up the accelerometer/multitouch devices once instead of walking the QOM tree per event | `15c120f16f` | Each 100 Hz tilt sample ran `object_resolve_path_type` under the BQL: about a quarter of the main loop's CPU in Flight Control. |
| 16K-entry TB jump cache (128 page groups) | `0584dbc9cc` | Flight Control, Poco, three interleaved rounds: 4K 18.8/19.5/20.7 → 16K 22.0/20.9/21.9 fps (+10%); 8K was in between. |
| One TB jump cache per ARM ASID (16 per CPU, lazy epoch clearing) instead of emptying the only one on every context switch | `f9fe5bfd74` | Flight Control play on the Poco (`fcbench.py`, three interleaved 30 s rounds): 18.1/18.1 → 20.1/19.7/20.2 fps, +10%. No change to boot time, which is why it was first parked. A 64K-entry geometry was worse in play (16.8/16.8/18.0). |
| Android builds default to API 29 native ELF TLS | `5323ba7cfc` | Removes emulated-TLS calls from every TCG helper that touches thread-locals: ~5–8% faster boot. |
| PKE: keep the Montgomery radix inverse for the loaded modulus | `a41eb8e07b` | The boot's RSA work recomputed it on every operation: ~7% faster boot. |
| Already in the fork before this work (upstream adopted them later) | 909d3ccb62; cdbcff9724, 38642ff7a2, a4749f1ba6 | 4K target pages; learned per-TB I/O instruction limits (no re-translation after mid-block device reads). |

Jetpack Joyride's title screen (Poco) is the heaviest scene measured so far: 863 GL calls and 232
`glDrawArrays` a frame, host GL ~16–18% of wall time before the three GL rows above. Its best 500 ms
windows reach 40–46 fps, so it is mostly bound by guest code, not GL. It does not hold 60 on the
desktop either: the headless Linux frontend on the Ryzen 7 3700X VM runs the same title screen
(Legacy Store copy 5997, version 1.3.5) at ~54 fps, so the Poco's ~35–36 is about two thirds of that.
In it the guest kernel is 21% of the vCPU (44% of the time in translated code): Mach IPC, the
scheduler and locks, ~5 300 syscalls a second.

### Client-array host state (2026-10-18, engine `6762325419`, `dev/perf-codex`)

The host retains each context's client-array enables, resolved pointers and client-active texture
unit. Guest data is still refreshed every draw; disabled arrays and failed fetches are disabled
before a host draw. Changed pointers retain strict validation. `LT_GLES_ARRAY_CACHE=off` restores
the original bind/check/unbind path in the same APK; the cache defaults on.

LT Bench, best / median of five rounds, µs per call. Each row is an interleaved off/on pair;
all runs use the same APK (SHA256 `e1d5298663acf5b78c9417e97760528aee80b3e2d3943a7b434b935dc63e202b`).

| Phone / pair | `gl-draw` off → on | `gl-sprite` off → on |
| --- | --- | --- |
| Poco 1 | 7.81 / 8.21 → 7.49 / 9.39 | 15.71 / 18.12 → 17.68 / 19.08 |
| Poco 2 | 8.92 / 9.05 → 7.38 / 7.60 | 19.87 / 20.17 → 15.68 / 17.20 |
| Poco 3 | 7.93 / 8.30 → 7.05 / 7.90 | 16.22 / 16.64 → 16.68 / 18.90 |
| Pixel 1 (on first) | 1.40 / 1.63 → 1.28 / 1.46 | 3.63 / 3.92 → 3.63 / 3.77 |
| Pixel 2 (on first) | 1.49 / 1.76 → 1.32 / 1.78 | 3.51 / 3.66 → 3.55 / 3.77 |

`gl-draw` best time improves in every pair (~10% on average); `gl-sprite` is within the run spread.
Opt-in `IT_GLES_PROF=1` now reports array setup plus cleanup time and counts host pointer, enable and
client-unit calls. A two-array draw previously made 2 pointer, 4 enable/disable and 4 client-unit
calls, plus the strict error checks. With stable host pointers those calls disappear after warmup.
A further same-APK off/on pair on the Pixel with profiling enabled and `LT_INLINE_JC=off` measured
**0.619 → 0.228 µs per draw** for setup plus cleanup across 50,000 draws (`gl-draw` and `gl-sprite`),
a 63% reduction. The pointer / enable / unit counts were **2 / 4 / 4 → approximately 0 / 0 / 0**.
The extra profiling clocks affect end-to-end GL benchmark times, so those are kept separate from
the uninstrumented pairs above.

Poco Jetpack title, three interleaved 30 s pairs, serial 27, same APK. Columns are fps except hitches
(frames over 50 ms, with their total duration). Screenshots confirm the title scene; the earlier
Stocks/YouTube navigation error was discarded. No skipped frames in these six samples.

| Pair / cache | Mean | Ceiling | Median | Low | 1% low | Hitches |
| --- | --- | --- | --- | --- | --- | --- |
| 1 off | 50.6 | 56 | 52 | 44 | 27.8 | 6 (350 ms) |
| 1 on | 53.2 | 58 | 54 | 48 | 28.5 | 1 (52 ms) |
| 2 off | 51.5 | 56 | 52 | 44 | 20.9 | 7 (373 ms) |
| 2 on | 52.2 | 56 | 52 | 48 | 28.1 | 8 (450 ms) |
| 3 off | 52.5 | 58 | 52 | 48 | 28.1 | 6 (340 ms) |
| 3 on | 51.9 | 56 | 52 | 46 | 27.8 | 7 (386 ms) |

Mean across pairs: **51.6 → 52.5 fps (+1.7%, two of three ahead)**. Ceiling stays 56–58;
median 52 → 52–54, low 44–48 → 46–48, 1% low 20.9–28.1 → 27.8–28.5, hitches 6–7 → 1–8.
The local call reduction is useful, but these game runs do not establish a consistent Jetpack gain
or a stutter fix. Raw windows and screenshots are retained under `build/perf-codex/poco-{1,2,3}-{off,on}/`.
Linux and Android builds succeed; the ASan/UBSan production-slice regression covers refreshed data,
disables, faults, scratch growth, fixed conversion, driver rejection/retry, context switches and VBOs.

### Batch command-buffer reads (2026-10-18, experimental, default off)

`LT_GLES_BATCH_FAST=on` tries the existing plain-RAM TLB copy for GL batch buffers, retaining
`cpu_memory_rw_debug` as fallback. No guest faults are armed for batches. Two interleaved Pixel
pairs used one APK (`batch-prof.apk`), array caching on, inline jump-cache probe off, and
`IT_GLES_PROF=1`. Direct timings are means per 4,096 batches; the following are medians of the five
steady 12-byte windows after LT Bench warmed up:

| Pair | Batch copy off → on | RAM hit rate on | `gl-sprite` best / median off → on |
| --- | --- | --- | --- |
| 1 | 0.206 → 0.043 µs | 4096/4096 | 4.34 / 4.40 → 3.96 / 4.21 µs |
| 2 | 0.199 → 0.044 µs | 4096/4096 | 4.14 / 4.23 → 3.93 / 4.31 µs |

The copy itself is ~4.6–4.8× cheaper. End-to-end `gl-draw` medians were 2.34 → 2.42 and
2.42 → 2.42 µs; `gl-upload` 62.34 → 63.66 and 64.18 → 65.35 µs. This is a small local saving;
no game FPS or pacing claim is made. Ceiling, low, 1% low and hitches are not applicable to these
individual-call timings; game measurements remain open before considering a default change.
Linux and Android builds succeed. Raw results/logs: `build/perf-codex/pixel-batch-{1,2}-{off,on}*`.

Poco Jetpack title, two interleaved 30 s pairs, one APK (engine `50a08eb2d2`, recording inactive,
array caching on, no `IT_GLES_PROF` or instruction accounting):

| Pair | Mean off → on | Ceiling | Median | Low | 1% low | Hitches (>50 ms) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 53.1 → 53.2 | 58 → 58 | 54 → 54 | 46 → 48 | ~28 → 28.5 | 12 (840 ms) → 7 (387 ms) |
| 2 | 53.1 → 53.8 | 58 → 58 | 52 → 54 | 48 → 50 | 28.3 → 28.8 | 3 (152 ms) → 5 (283 ms) |

Mean **53.07 → 53.50 fps (+0.8%)**. Both pairs are ahead, but the first barely moves and pacing
does not consistently improve. Keep the switch **off by default**; the local copy saving is real,
but there is no convincing game-level improvement. Screenshots confirm the same title scene and
no skipped frames. Raw results: `build/perf-codex/poco-batch-{1,2}-{off,on}/`. Pair 1 off retains the
printed result and full runtime log; its exact original measurement window was not saved, so its
1% low is available only at the printed integer precision. Later windows are saved exactly.

### Manual scene captures (2026-10-18, engine `50a08eb2d2`)

Android now has Start/Stop profile above its FPS graph. Start queries the guest agent's frontmost
bundle ID and localized app name; each session saves independent files under `files/profiles/`.
The engine records actual publication times and intended LCD times, approximate cumulative vCPU
wall/CPU time, GL count/total/max per publication interval, and individual GL calls/BQL waits/holds
of at least 0.5 ms. Android separately records buffer acquisition, scaling, pacing and post timings.
No guest update, `IT_VCPU_ACCT`, `IT_GLES_PROF` or instruction instrumentation is required.
`LT_PROFILE=off` disables the recording API; recording work is inactive until Start.

The Pixel Start/Stop path saved `Battle Bears` / `net.skyvu.battlebears1` correctly. Its capture
(79.9 s including a static notification prompt/menu) produced 4,091 engine-event lines and 97 Android
post rows, ~268 KiB including metadata/manifests/logs. Pull, text/JSON report and histogram/timeline
PNG generation succeeded. A second capture identified `DoodleJump` /
`com.yourcompany.DoodleJump`, wrote 805 Android post rows, and saved successfully after pausing the
guest and pressing Stop profile. These were functional captures, not game-speed measurements: static scenes
produce long gaps. No before → after FPS improvement or recording-overhead claim is made. Ceiling,
median, low, 1% low and hitches are reported by the analysis tool for each capture, with the same
scene-context caveat. Uninstrumented interleaved pairs remain the basis for performance decisions.

Captures stop on user request/guest Stop and are bounded to 15 minutes or about 128 MiB. A missing
stop footer identifies a partial/crashed capture. `tools/perf/profile.py` ranks GL calls and shows
the longest engine and Android presentation intervals with overlapping slow-call and lock details.
Counters overlap, completed GL totals can cross frame boundaries, snapshots can lag, and GL wall time
is not GPU execution time. See [PROFILING.md](PROFILING.md) for retrieval and interpretation.
Linux and Android builds succeed. Captures and generated reports remain local under
`build/perf-codex/profile-captures/` and `build/perf-codex/profile-battlebears*`.

The user's Pixel captures of DoodleJump, COD Zombies, Jetpack, Gravity Guy and Bike or Die are
saved under `build/perf-codex/pixel-user-captures/`, with histogram/timeline PNGs, detailed text,
JSON and an index under `pixel-user-reports/`. These cover manual play and transitions, so they
are not controlled game benchmarks. All five have stop footers and no discarded rows.
COD Zombies' 83–84 ms gaps consume ~78–79 ms of vCPU CPU; several Jetpack 0.36–0.42 s gaps
have only 0.2–7 ms of completed GL. Gravity Guy has long intervals with substantial time outside
vCPU execution. Scene annotations and focused CPU profiles are needed to distinguish game
pauses/loading from emulator stalls. The longest Android post gaps largely track the engine gaps;
their buffer/scale/post stages do not account for hundreds of milliseconds.

### Recorder overhead and audio investigation (2026-10-18)

The user reported intermittent repeating audio on the Pixel while recording. Two AudioTrack
restart warnings confirm underruns in the session, but their timestamps (phone-local
15:17:11.870 and 15:18:34.510) fall between saved captures, so they do not prove recorder causation.
The original recorder could flush buffered event data under the BQL, and presentation data under
the JNI lock used by PCM reads. Engine `9b38a2ffd3` queues records to a background writer without
holding its queue mutex during I/O. Android presentation uses the same approach. Queue limits
fail the capture explicitly instead of blocking playback; `LT_PROFILE_SYNC=on` restores
synchronous writes for comparison. Live captures also skip detailed accounting table scans and
allocations. Metadata now retains native PCM and AudioTrack counters before and after recording.
This removes a source of recording-induced stalls; audible recovery on the Pixel remains to be
confirmed and the looping report is not marked resolved.

Original recorder (`50a08eb2d2`), Poco Jetpack title, two same-boot interleaved 30 s recording
off/on pairs, batch-copy and array-cache switches on, no full instruction accounting:

| Pair | Mean off → recording | Ceiling | Median | Low | 1% low | Hitches (>50 ms) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 54.0 → 50.0 | 58 → 56 | 54 → 50 | 50 → 46 | 28.3 → 26.9 | 6 (303 ms) → 8 (459 ms) |
| 2 | 53.8 → 50.2 | 58 → 54 | 54 → 50 | 50 → 46 | 28.3 → 20.8 | 3 (189 ms) → 8 (420 ms) |

Mean **53.88 → 50.07 fps (−7.1%)**: recording has measurable overhead and its frame rate must not
be treated as an uninstrumented baseline. Raw exact windows and charts:
`build/perf-codex/poco-recording-{1,2}-{off,on}*`.

Updated recorder (`9b38a2ffd3`), same settings and two new same-boot off/on pairs:

| Pair | Mean off → recording | Ceiling | Median | Low | 1% low | Hitches (>50 ms) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 52.7 → 48.6 | 58 → 54 | 54 → 50 | 46 → 38 | 20.6 → 20.0 | 11 (917 ms) → 17 (1255 ms) |
| 2 | 52.0 → 49.6 | 58 → 56 | 54 → 50 | 44 → 40 | 20.7 → 20.1 | 13 (1038 ms) → 14 (1124 ms) |

Mean **52.33 → 49.07 fps (−6.2%)**. The new session's unrecorded pacing was also worse; do not
compare absolute rates across these builds as an interleaved optimization result. Recording still
has significant overhead, and asynchronous writes have not demonstrated a pacing improvement.
Both new captures saved complete files with no discarded rows; their start/end counters showed
zero AudioTrack underruns, PCM drops, native pull failures and output write failures on the Poco.
That checks the new recording path, not audible correctness on the Pixel. Linux/Android builds
succeeded. APK SHA256: `45dd9f125bb0bae51202e80cee52512eb0290dc1c52e893424734e03176c0292`.
Raw windows/charts: `build/perf-codex/poco-recording-async-{1,2}-{off,on}*`; captures:
`build/perf-codex/poco-profile-captures/`.

Five further Pixel captures on the updated recorder (DoodleJump, Home Screen, Bike or Die!,
Jetpack and Gravity Guy) are retained under `build/perf-codex/pixel-new-captures/`; charts,
text/JSON and a detailed index are under `pixel-new-reports/`. All have complete footers,
zero recorder errors and zero discarded rows. The user reported **no audible looping this time**.
AudioTrack underruns increased by 0/1/0/1/5 respectively, with zero PCM drops, native pull failures,
output write failures or discarded read samples. Underruns alone do not prove audible looping.
The original regression remains open pending broader confirmation.

These are manual scenes, not an A/B optimization comparison. New Jetpack: mean 58.2 fps, ceiling
60, median 58, low 56, 1% low 30.0, two >50 ms engine gaps totaling 265 ms over 31.6 s. Its largest
gap at 6.30 s is 214 ms: 206 ms inside vCPU execution, 83 ms of vCPU CPU, 3 ms completed GL and
4 ms BQL waits. The remaining time requires a host scheduling/blocking trace; it cannot all be
called guest CPU work. Gravity Guy's 1.55/1.22 s early gaps consume 1.44/1.16 s of vCPU CPU;
the user identified these as loading/menu transitions, so they are not evidence of active-play
stutters. Another 201 ms gap near 15.37 s consumes 191 ms CPU, with its scene still unknown.
A focused CPU profile of a confirmed gameplay hitch is the next useful evidence. The analyzer
now prints execution wall time alongside CPU time.

### Focused GL context timing (2026-10-18)

Engine `c2e06923de` adds `LT_GLES_CONTEXT_PROF=on` (default off). It times only viewport,
framebuffer bind and surface present setup, make-current and dispatch, logging totals and maxima
every five seconds. No live recorder, `IT_GLES_PROF` or instruction accounting was active.
On the Snapdragon 865-class Poco, Jetpack title, six steady five-second blocks:

| Call | Count | Selected-context changes | Init µs/call | Make-current µs/call | Dispatch µs/call |
| --- | --- | --- | --- | --- | --- |
| Viewport | 3396 | 1752 | 0.565 | 110.474 | 4.039 |
| Present surface | 1625 | 1008 | 0.726 | 20.837 | 1049.176 |

The expensive viewport service in the live captures has a context-selection lead: the host viewport
operation itself is small on the Poco. Selected-context changes count engine state selections,
not driver-internal switches. Dispatch includes strict error checks and can include synchronous
driver waits; these are wall times, not GPU execution times. No optimization or FPS gain is claimed.
Screenshot verifies the Jetpack title; raw log and scene: `build/perf-codex/context-stages-*`.
APK SHA256: `e534dd536d9c137d2515908ffeb675a76dce8101d68883a3454db8b585372d77`.

### Exact-zero VFP multiplication (2026-10-18, experimental, default off)

The recent Poco Jetpack native profile contains `float32_mul` at 1.07% self,
`soft_f32_mul` at 0.62%, and `parts64_canonicalize` at 0.37%. These shares overlap
other operations and are not a saving estimate. Source inspection found that even
softfloat's host-FPU multiplication path falls back to software for a zero result
when only one operand is zero; its underflow predicate is shared with addition.
Calls without the cumulative inexact flag also use software.

Engine `f43fbe4986` adds `LT_VFP_MUL_ZERO_FAST=on`, default off. AArch32 VFP
single-precision multiplication of an exact zero by a normal finite value or
another zero returns the XOR sign and zero magnitude, without changing exception
flags. Nonzero pairs, subnormals, infinities and NaNs still use softfloat. Selection
is during translation: the baseline generates the original helper, with no runtime
switch/counter/clock overhead. Half/double precision and other translators are unchanged.

Independent default-off `LT_VFP_MUL_ZERO_PROF=on` selects diagnostic helpers. CPU 0
counts all calls, eligible zero operations, and mixed zero/normal operations, timing
one in 256 mixed operations. Five-second blocks have exact monotonic bounds, sample
counts and summed/max durations. Clock overhead and host descheduling are included;
these diagnostic frame windows are not ordinary game baselines.

Linux/Android builds succeeded (`build/perf-codex/vfp-mul-zero-build.txt`). Frozen
APK/symbols: `vfp-mul-zero.apk`, `vfp-mul-zero-symbols/`; build identity retained in
`vfp-mul-zero-build-identity.json`. APK SHA256:
`6ebb6f5d72cb2820e5d8e62cc68a891b3e5af9fd696fe52f1a92e35956644577`.
Two focused pairs (off/on, on/off), 35 s per arm, follow the completed BQL
comparison and its clean stop. All other diagnostics/experiments off, array cache on,
no live recorder/full accounting. Each arm selects complete contiguous timing blocks
wholly inside its frame window. **Both pairs complete, second reversed.** All four
final scenes reviewed: correct Jetpack title, recorder off, zero skips.

| Pair | Sample mean ns, off → on | Median of block means ns | Mixed zero/normal calls/s | Eligible share |
| --- | --- | --- | --- | --- |
| 1 | 357.05 → 343.42 | 356.20 → 336.83 | 271253 → 273027 | 41.95% → 42.04% |
| 2 (on first) | 368.14 → 333.79 | 368.63 → 333.71 | 280130 → 268513 | 42.38% → 41.83% |

These are timings of the mixed zero/normal subset, including clocks/descheduling,
not all multiplication calls. Six complete blocks per arm contain 31823/32853 →
32027/31492 samples; maximum individual sample 25.938/23.125 → 284.011/3.959 µs.
Retain all samples and blocks, including the first enabled arm's outlier. Both
pairs favor the shortcut locally (3.8% and 9.3% of the instrumented sample mean).
Mixed zero/normal operations are about 30.4–30.9% of all measured multiplies;
the rest still execute the helper's eligibility checks. Do not extrapolate these
timer-inclusive subset costs or variable call rates to whole-game gains.

| Pair | Diagnostic mean fps off → on | Ceiling | Median | Low | 1% low | Hitches (>50 ms) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 52.94 → 54.03 | 58 → 60 | 54 → 54 | 46 → 48 | 20.17 → 28.49 | 16 (1077.4 ms) → 5 (300.5 ms) |
| 2 (on first) | 53.77 → 52.09 | 58 → 58 | 54 → 52 | 48 → 44 | 28.22 → 20.28 | 7 (356.0 ms) → 15 (1004.2 ms) |

Diagnostic mean **53.36 → 53.06 fps (−0.6%)**, one pair ahead, one behind;
hitches **23 → 20**, total **1433.4 → 1304.7 ms / 70 s per mode**. No consistent
pacing or throughput gain in these instrumented windows. These are not ordinary
game baselines or profiler-overhead measurements. Keep off; an ordinary
diagnostic-off comparison remains open after the higher-priority combined and
masked-interrupt work. Evidence: `poco-vfp-mul-zero-focused-*`,
`vfp-mul-zero-analyze.py`, `vfp-mul-zero-results.json`; per-arm `timings.json`
retains contiguous exact blocks and separate enclosing frame bounds. Reviewed
montage: `vfp-mul-zero-scenes.png`. The runner cleanly stopped, checked no qemu
engine remained, restored baseline, and handed the phone to the combined comparison.

| Pair / mode | FPS [start, end) ns | Timing [begin, end] ns |
| --- | --- | --- |
| 1 off | [432157727997589, 432192727997589) | [432160337647119, 432190375657577] |
| 1 on | [432328054983201, 432363054983201) | [432330742600596, 432360774786053] |
| 2 on | [432510495258444, 432545495258444) | [432513336802141, 432543364201869] |
| 2 off | [432689830961501, 432724830961501) | [432692467838479, 432722492903311] |

### BQL diagnostics without instruction accounting (2026-10-18)

Engine `f64fb44e3c` adds default-off `LT_BQL_PROF=on`. It reuses the BQL wait/hold
tables, main-loop callback timing and outer vCPU execution/idle timing without enabling
per-TB instruction, lookup, context-switch or TLB-fill accounting. Full `IT_VCPU_ACCT`
still enables both. `[acct-mode]` labels full versus BQL-only mode; zero instruction
and lookup counters in the latter are unmeasured, not a workload finding.

Engine `f9d7faec8c` scopes the MMIO attribution pointer to each dispatch and clears it
after longjmp cleanup. A lockless counter read previously could leave its region to be
misattributed to the next unrelated BQL acquisition. New cumulative dumps also retain
exact nanosecond boundaries and a completion marker. `bqlsites.py` now uses the actual
selected interval instead of the requested one, ignores incomplete new dumps and can
retain all sites/bounds in JSON. Legacy rounded timestamps remain readable.

This is diagnostic work, not an optimization or FPS gain. Existing holder spans can include condition waits that
temporarily release the BQL, and callbacks can unlock it internally; those totals do
not establish that the lock was held throughout. Actual vCPU waits are the stronger
evidence. Full capture settings and limitations are in [PROFILING.md](PROFILING.md).

Linux/Android builds succeeded (`build/perf-codex/bql-prof-final-build.txt`), engine
`f9d7faec8c`. Frozen APK/symbols: `bql-prof.apk`, `bql-prof-symbols/`; SHA256:
`a0c057076baac4494912df6d524cc26f8cd4a31b3126cf9adb98cbcad5a65c64`.
Two 30 s off/on pairs completed, second reversed, after the LCD stage runner's
clean stop. Snapdragon 865-class Poco, Jetpack title; all other diagnostics/experiments
off, array cache on, no live recorder or full accounting. All four final screenshots
reviewed: correct title, recorder off. First off arm has one publisher skip; all
others zero. Retain that arm and its skip. These measure diagnostic overhead.

| Pair | Mean fps off → on | Ceiling | Median | Low | 1% low | Hitches (>50 ms) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 53.10 → 49.43 | 56 → 56 | 54 → 50 | 50 → 36 | 28.48 → 15.00 | 5 (252.0 ms) → 31 (2370.6 ms) |
| 2 (on first) | 53.53 → 50.93 | 58 → 58 | 54 → 52 | 50 → 40 | 28.61 → 20.04 | 3 (166.8 ms) → 15 (987.5 ms) |

Mean **53.32 → 50.18 fps (−5.9%)**, both pairs slower with diagnostics on.
Hitches **8 → 46**, total duration **418.8 → 3358.1 ms / 60 s per mode**.
Even without instruction accounting, these diagnostics perturb throughput and pacing;
never treat enabled-arm rates or wait percentages as uninstrumented baselines.

Enabled-arm BQL waits total **9.08/8.74% wall**, 9199/7145 acquisitions/s.
`accel/tcg/cpu-exec.c:1062` dominates at **6.09/5.84% wall**, 2043/1691 acquisitions/s,
29.8/34.5 µs mean wait. Timer1 contributes 0.7/0.9%; main vCPU lock site
`tcg-accel-ops-mttcg.c:401` 0.6/0.8%; anonymous region aggregate 0.6/0.6%;
LCD 0.4/0.3%; VIC0 0.4/0.2%. These are actual acquisition waits in selected
complete accounting intervals, not holder spans. `bqlsites.py` parsed the real new
dumps successfully; results retain every site and exact actual bounds.

| Pair / mode | FPS [start, end) ns | BQL [start, end] ns |
| --- | --- | --- |
| 1 off | [431451506228171, 431481506228171) | disabled |
| 1 on | [431624396567480, 431654396567480) | [431625194257011, 431650202677731] |
| 2 on | [431804392530797, 431834392530797) | [431808410405535, 431833417156984] |
| 2 off | [431974598175732, 432004598175732) | disabled |

Artifacts: `build/perf-codex/poco-bql-prof-{1,2}-{off,on}/`,
`bql-prof-analyze.py`, `bql-prof-results.json`, `bql-prof-scenes.png`. Enabled
arms retain `bql-sites.{txt,json}` separately from the FPS windows. The runner
cleanly stopped/restored baseline before handing the phone to focused VFP captures.

### Masked physical interrupt precheck (experimental, default off)

The first two independent BQL-enabled Poco Jetpack captures attribute 6.1% and
5.8% of wall time to `cpu_handle_interrupt` acquiring BQL (2043/1691 checks/s).
These are instrumented observations, not an estimate of recoverable performance.
The generic handler takes BQL before ARM determines whether a pending IRQ/FIQ is
masked. Delivered interrupts were much less frequent in an earlier exception
capture, motivating a direct count of these no-op acquisitions.

Engine `e82b9e770b` adds default-off `LT_MASKED_IRQ_FAST=on`. An optional target
precheck reuses ARM's existing physical-exception routing and unmask predicates,
including SCR/HCR overrides. It only accepts snapshots containing IRQ/FIQ and
optionally EXITTB. It preserves pending IRQ/FIQ levels and EXITTB chain invalidation;
new requests retain their kicks. Debug, halt, reset, virtual/NMI/unknown requests,
M-profile, replay and single stepping retain the locked path. Guest unmasking still
ends/rechecks the TB, including with the existing PSR optimization enabled.

Independent default-off `LT_IRQ_CHECK_PROF=on` times and counts CPU-0 checks without
full instruction accounting, BQL tables or live recording. Exact approximately
five-second blocks include candidate masked checks, actual skips, delivery/no-op
outcomes, total check and acquisition wall times, and masked subsets. Timing includes
clock overhead/descheduling. Only completed checks are included; target longjmps
can bypass recording. A delivery after a masked precheck may reflect a new request
arriving before BQL acquisition, so it is not alone proof of an incorrect predicate.
Output follows BQL unlock. See PROFILING.md for fields.

Linux/Android builds succeeded (`build/perf-codex/masked-irq-build.txt`), no tests
added/run. Frozen APK/symbols: `masked-irq.apk`, `masked-irq-symbols/`; identity
in `masked-irq-build-identity.json` (app `6281142f`, documentation edits only).
APK SHA256: `9c431d362337a89400da8ba01fd3582ee61eec91ba282efca8b60dd20bb923af`.
Library SHA256: `45040f4403d95401c1c0c94777dbcbdc2f0ef50f6db242ce1b1fca64f473f156`.
Both focused 35 s pairs completed after the combined cache+lookup baseline:
off/on, then on/off, varying only `LT_MASKED_IRQ_FAST`. IRQ profiling on both,
array cache on, other experiments/diagnostics off, no live recorder/full accounting.
All four final screenshots reviewed: correct Jetpack title, recorder off, zero
publisher skips. Every arm has six contiguous complete ~5 s timing blocks wholly
inside its 35 s frame window, about 30 s of direct timings per arm.

| Metric | Pair 1 off → on | Pair 2 off → on (on first) |
| --- | --- | --- |
| Checks/s | 1665 → 1684 | 1656 → 1696 |
| Masked candidates/s | 1106 → 1124 | 1104 → 1134 |
| Masked candidates % | 66.4% → 66.8% | 66.6% → 66.8% |
| Masked mean µs/check | 7.985 → 0.422 | 8.072 → 0.426 |
| Masked BQL wait µs/check | 7.108 → 0 | 7.183 → 0 |
| Masked check wall % | 0.883% → 0.047% | 0.891% → 0.048% |
| All check wall % | 5.340% → 4.452% | 5.332% → 4.464% |
| All acquisition wall % | 5.036% → 4.199% | 5.036% → 4.201% |
| Delivered checks/s | 560.5 → 559.5 | 553.3 → 562.4 |

All 33,736/34,036 enabled masked candidates skip the lock. Off arms have 28/24
masked-candidate deliveries, enabled arms zero: a request can change before lock
acquisition. Completed maximum check times 3.135/3.666 → 4.160/3.360 ms are
retained. The local masked-check saving repeats, including reverse order, but
most remaining wait is outside that subset: skipping all masked checks does not
recover the earlier full 6% interrupt-wait observation.

| Pair | Diagnostic mean fps off → on | Ceiling | Median | Low | 1% low | Hitches (>50 ms) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 51.97 → 52.97 | 56 → 58 | 52 → 54 | 44 → 44 | 27.15 → 27.85 | 13 (972.7 ms) → 9 (838.5 ms) |
| 2 (on first) | 54.20 → 53.03 | 58 → 58 | 54 → 54 | 50 → 48 | 28.82 → 28.29 | 3 (151.8 ms) → 10 (526.4 ms) |

Aggregate diagnostic mean **53.09 → 53.00 fps (−0.2%)**; hitches **16 → 19**,
total duration **1124.6 → 1364.9 ms** over 70 s per mode. One pair wins, one loses;
no consistent game/pacing gain. These instrumented FPS results are not ordinary
baselines or a profiler-overhead measurement. Keep the optimization off. An
ordinary diagnostic-off runner is prepared, not queued: the separate delivery
experiment below has priority and also covers eligible masked no-ops.

Exact half-open frame / complete-block timing bounds (monotonic ns):

| Pair/mode | FPS [start, end) | Timing [begin, end] |
| --- | --- | --- |
| 1 off | [433923155694885, 433958155694885) | [433927854207383, 433957863277059] |
| 1 on | [434107396548356, 434142396548356) | [434107481622002, 434137489276313] |
| 2 off | [434461063175565, 434496063175565) | [434463995312699, 434493999079354] |
| 2 on | [434284970737559, 434319970737559) | [434287343786464, 434317359895047] |

Raw `poco-masked-irq-focused-{1,2}-{off,on}/`; `masked-irq-analyze.py`,
`masked-irq-results.json` retain all blocks, counts, windows and every hitch;
reviewed montage `masked-irq-scenes.png`. Runner confirmed clean Stop, no qemu
engine and baseline environment readback; `masked-irq-focused-finished` permits
the queued delivery comparison to start.

### Surface-present stage diagnostic (2026-10-19, default off, captures complete)

Existing captures combine GPU synchronization and guest-memory row copies in the
surface-present duration (roughly 1 ms/call in the earlier focused capture). This
is not enough to justify removing synchronization or changing the copy path.
Engine `5b38fec1df`, `LT_GLES_PRESENT_PROF=on`, now independently splits preparation, acquire, readback,
conversion, copy and tail, with no clocks inside the row loop. Acquire includes
the required GPU completion/map; mapped copy includes platform row byte swapping.
Mapped/readback/invalid/test paths retain attempt/error/fault-retry counts,
phase totals/maxima, and the phase breakdown/timestamp of the slowest whole call.
Exact five-second blocks have completion markers; `tools/perf/presentstages.py`
selects contiguous complete blocks wholly inside an exact frame window and saves
all blocks. No live recorder/full accounting/per-draw profiling is needed.
Linux/Android final builds succeeded (`present-prof-final-build.txt`), no tests
added/run. Frozen `present-prof.apk`, `present-prof-symbols/libqemu-arm.so` and
`present-prof-build-identity.json` retain source pins/hashes (app changes at build
are documentation and the offline analyzer only).

Two 30 s pairs, profiler off/on then on/off, completed after ordinary L2 cleanup.
Array cache on; L2 and other experiments/diagnostics off; user-facing Start profile
and full accounting off. All four final title scenes reviewed, zero skips. This
measures a dedicated engine diagnostic, not user-facing recording performance.

| Pair | FPS off → diagnostic on | Ceiling | Median | Low | 1% low | Hitches (>50 ms) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 54.30 → 50.87 | 58 → 56 | 54 → 52 | 50 → 40 | 28.71 → 19.88 | 2 (116.6 ms) → 18 (1255.3 ms) |
| 2 (on first) | 54.17 → 52.33 | 58 → 58 | 54 → 52 | 48 → 46 | 28.60 → 20.50 | 5 (272.2 ms) → 10 (717.9 ms) |

Aggregate **54.23 → 51.60 fps (−4.9%)**, hitches **7 → 28**, total
**388.8 → 1973.2 ms/60 s per mode**. Use focused timings to locate work; do not
use their FPS as an ordinary baseline. No optimization or game gain is claimed.

Both enabled arms selected five complete contiguous timing blocks wholly inside
their frame windows. All returned attempts use the mapped IOSurface path, no
errors/fault retries, no readback or conversion phase. Mapped copy includes any
platform row swizzle. Acquire includes flush, required GPU completion and mapping.

| Metric | Enabled arm 1 | Enabled arm 2 |
| --- | --- | --- |
| Selected timing seconds / attempts | 25.029 / 1301 | 25.074 / 1341 |
| Total mean / wall share | 922.10 µs / 4.793% | 982.84 µs / 5.256% |
| Prep / acquire / copy / tail mean µs | 0.86 / 644.32 / 173.16 / 103.76 | 0.90 / 707.85 / 173.72 / 100.37 |
| Slowest total / acquire component µs | 6696.56 / 6428.39 | 6726.09 / 6399.27 |
| Slowest end timestamp ns | 437350013408473 | 437518700760909 |

The longest surface-present calls are dominated by acquire; these ~6.7 ms calls
alone do not explain 50–100 ms frame gaps. Keep the required synchronization.
Clocks/descheduling are included; phase maxima can belong to different calls.

Exact frame windows [begin, end), monotonic ns:

| Arm | Frame window |
| --- | --- |
| 1 off | 437154897122193, 437184897122193 |
| 1 on | 437333077035146, 437363077035146 |
| 2 on | 437511682019401, 437541682019401 |
| 2 off | 437682403887148, 437712403887148 |

Selected timing windows: enabled 1 `[437333662314781, 437358691367532]`,
enabled 2 `[437512165868828, 437537239779339]`. Raw `poco-present-prof-*`,
`present-prof-results.json`, per-arm `stages.json` and reviewed `present-prof-scenes.png`
preserve all blocks, slowest call components, frame windows and hitches. Runner
confirmed clean Stop/no-qemu/baseline readback; `present-prof-finished` released
the serialized context experiment. Fields/scope in PROFILING.md.

### CPU-only physical IRQ delivery (2026-10-19, experimental, default off)

The first masked-check pair leaves 4.20% wall in BQL acquisitions after masked
candidates skip it. Source inspection found that physical ARMv6 IRQ/FIQ entry
calls the same CPU-only `arm_cpu_do_interrupt` path already used without BQL
for synchronous exceptions. It changes banked registers, CPSR, PC and CPU-owned
exception state; it neither queries nor acknowledges the VIC. The guest's later
MMIO acknowledgement continues to take BQL.

Engine `5b956fc6ae` adds default-off `LT_IRQ_DELIVERY_FAST=on`, gated on one
current and one maximum vCPU. A target predicate permits only physical IRQ/FIQ
(optionally EXITTB) on ARMv6 A/R, excluding v7/newer and M-profile, EL-change hooks,
virtual/NMI/unknown/special requests. Replay and stepping use the locked path.
It calls the original target interrupt handler with the atomic request snapshot;
IRQ/FIQ levels remain pending, delivered IRQ accounting and EXITTB chain-breaking
are retained, and newly arriving requests retain their kicks and the final
`exit_request` check. A later deassertion does not undo an IRQ already sampled.
No target interrupt semantics or guest exception code are replaced.

`LT_IRQ_CHECK_PROF` remains independent. New fields count `unlocked` completed
checks (including masked no-ops) and time the `delivered-ns` / `delivered-wait-ns`
subset, allowing direct measurement of the remaining wait. Ordinary mode has no
profiling clocks/counter updates. Timings include clocks/descheduling, and target
longjmps can bypass completed records as before.

Linux/Android builds succeeded (`build/perf-codex/irq-delivery-build.txt`), no
tests added/run. Frozen `irq-delivery.apk`, `irq-delivery-symbols/` and clean build
identity `irq-delivery-build-identity.json`: app `29a58213`, engine `5b956fc6ae`.
APK SHA256: `24aba9dd778e351106c8d061581688b7e997692acef7d39f8b41f61492137b78`.
Library SHA256: `c9a1b9765cf2d5b7888b473622eafedf6f12624f5fe4fe298d00975b29a8fd6a`.

Two focused 35 s pairs off/on, on/off run after the completed masked-IRQ
focused comparison and its clean stop, ahead of the indirect L2 experiment.
`LT_IRQ_CHECK_PROF=on` both; only delivery-fast varies. Masked fast path, L2,
other experiments/diagnostics and live recorder/full accounting off; array cache
on. `irq-delivery-run.py` / `irq-delivery-analyze.py` preserve exact frame/timing
bounds and all hitches under `poco-irq-delivery-focused-*`.

Both focused pairs complete (second on/off). All four final screenshots reviewed:
correct Jetpack title, recorder off, zero skips. Six contiguous complete five-second
blocks per arm, wholly inside each exact 35 s frame window:

| Direct timing metric | Pair 1 off → on | Pair 2 off → on (on first) |
| --- | --- | --- |
| Mean delivered check / acquisition wait (µs) | 87.205 / 83.265 → 1.946 / 0 | 74.900 / 71.263 → 2.168 / 0 |
| All check / acquisition wall (%) | 5.765 / 5.448 → 0.293 / 0 | 5.135 / 4.829 → 0.189 / 0 |
| Delivered interrupts/s | 551.97 → 969.10 | 557.75 → 567.91 |
| All checks/s | 1690.79 → 2928.25 | 1703.61 → 1678.05 |
| Unlocked completed checks | 0 → 87872 (100%) | 0 → 50358 (100%) |
| Max completed check (µs) | 4150.68 → 61.93 | 4483.49 → 68.85 |

| Diagnostic pacing metric | Pair 1 off → on | Pair 2 off → on (on first) |
| --- | --- | --- |
| Mean fps | 53.06 → 49.54 | 54.43 → 53.83 |
| Ceiling | 58 → 58 | 60 → 58 |
| Median | 54 → 52 | 54 → 54 |
| Low | 46 → 34 | 50 → 48 |
| 1% low fps | 28.25 → 14.85 | 28.78 → 28.61 |
| Hitches (>50 ms) per 35 s | 10 (819.4 ms) → 42 (3234.8 ms) | 2 (101.4 ms) → 8 (764.8 ms) |

Aggregate diagnostic mean **53.74 → 51.69 fps (−3.8%)**. Hitches **12 → 50**,
total duration **920.8 → 3999.6 ms** over 70 s per mode. The first enabled arm's
interrupt spike is concentrated in two contiguous five-second blocks: 1906.57 /
1665.69 deliveries/s with 38.40 / 45.34 diagnostic fps; its other blocks stay
551–569/s. The reverse enabled arm stays 557–585/s, yet also loses throughput
and pacing. Retain the outlier; it is not a repeatable steady frequency increase.

The direct wait disappears, but that is not recovered vCPU time: guest device
MMIO acknowledgements still lock and waits may move there. More interrupt/guest
handler work coincides with the first spike; causation and wait migration are not
proved. **Keep off: no game/pacing benefit established.** Profiler-off ordinary
pairs remain prepared only and are deferred behind the unmeasured L2 candidate;
these diagnostic FPS values are not ordinary baselines or an overhead estimate.

Exact half-open frame windows and selected complete timing windows (monotonic ns):

| Arm | Frame [begin, end) | Timing [begin, end] |
| --- | --- | --- |
| 1 off | [434650140183826, 434685140183826) | [434653684393825, 434683693384594] |
| 1 on | [434833235134798, 434868235134798) | [434834350906568, 434864359308432] |
| 2 on | [435016923205509, 435051923205509) | [435017459224832, 435047469036591] |
| 2 off | [435201041681116, 435236041681116) | [435205902778614, 435235910834228] |

`irq-delivery-results.json` retains all blocks and every hitch;
`irq-delivery-analysis.txt` and reviewed `irq-delivery-scenes.png` retain summaries
and all four scenes. Runner session ended exit 0 with clean Stop, no qemu engine,
and baseline environment readback; `irq-delivery-focused-finished` then allowed
the serialized L2 runner to start. Ordinary delivery runner must be reserialized
before any later launch; its old dependency would overlap L2.

### Exception and SVC diagnostics (2026-10-18)

Engine `42395839f3` adds `LT_EXCEPTION_PROF=on` (default off): CPU 0 AArch32 exception counts,
raw SVC `r12` counts, short-descriptor data-abort statuses, iOS 3 Mach-message trap send/receive
attempts, and instrumented host dispatch time by exception type. Timings exclude guest handlers,
earlier BQL waits and snapshot logging; counters cover all guest processes. Complete snapshots
every five seconds can be matched to published-frame timestamps with `tools/perf/exceptions.py`.
The next capture should quantify the open kernel-work lead before another optimization.

Linux/Android builds succeeded (`build/perf-codex/exception-prof-final-build.txt`). Instrumentation
overhead has not been measured; no FPS gain is claimed. Frozen exception-only APK/symbols:
`exception-prof.apk`, `exception-prof-symbols/` under `build/perf-codex/`. APK SHA256:
`53ba5e44932a07c00b7a5908af3ded81b8c55712f7990a7f2eb79e8f9e131d9b`.

Focused Poco Jetpack title capture using the later `9400ab1b68` APK below, with exception and LCD
timing enabled, lookup inlining/forced-snapshot skipping off. The live recorder and full accounting
were off. Complete exception snapshots at **424584902925270 → 424614912061404 ns** cover 30.009 s
and 1,563 published frames. `exceptions.py` successfully parsed the real log and full frame log.

| Exception | Entries/s | Entries/frame | Instrumented host mean µs | Host total ms |
| --- | --- | --- | --- | --- |
| SVC | 3368.7 | 64.68 | 1.063 | 107.51 |
| Data abort | 3973.4 | 76.29 | 0.544 | 64.90 |
| Undefined instruction | 772.4 | 14.83 | 1.062 | 24.62 |
| FIQ | 455.1 | 8.74 | 1.491 | 20.36 |
| IRQ | 115.8 | 2.22 | 1.264 | 4.39 |
| Prefetch abort | 13.2 | 0.25 | 0.809 | 0.32 |

Host dispatch totals about 222.1 ms / 30.009 s (0.74% of wall time), including diagnostic work
and descheduling but excluding guest handlers and earlier BQL waits. Optimizing entry dispatch
alone has limited headroom here. Raw SVC `r12=-31` occurs 1860.5/s, `-3` 838.3/s. Mach-message
send/receive attempts are 1432.5/1597.9 per second (27.50/30.68 per frame); attempts can overlap
within one trap and are not delivered-message counts. Alignment status 1 accounts for 3907.1
data aborts/s; the earlier direct unaligned-LDM/STM experiment remains rejected.

The separate 45 s diagnostic frame window is mean/ceiling/median/low/1% low **52.07/58/52/44/19.99**,
25 hitches totaling 1793.7 ms, zero skipped frames. It has no matched uninstrumented arm and is not
an optimization or overhead comparison. Evidence, screenshot, full/window frame logs and JSON:
`build/perf-codex/poco-exception-lcd-diagnostic/`.

### LCD forced-repaint dirty snapshots (skip rejected)

Engine `9400ab1b68` adds `LT_LCD_FORCED_SNAPSHOT_SKIP=on` (default off). The linear
LCD path snapshots and clears dirty memory even when a forced repaint will read every row.
The experiment skips that snapshot on forced repaints and retains the dirty bits. This cannot
hide writes, but can cause later redundant repaint or GL surface upload work. Composed/rotated
scanouts bypass this path; establish whether the game uses it before measuring FPS pairs.

Independent `LT_LCD_SNAPSHOT_PROF=on` (default off) counts linear updates, forced repaints and
skips, with summed/max snapshot wall times in five-second blocks. Skipped work has zero duration.
Diagnostic output follows the LCD unlock; timing includes host descheduling. Linux/Android builds
succeeded (`build/perf-codex/lcd-snapshot-build.txt`). Runtime results follow.
Frozen APK/symbols: `lcd-snapshot.apk`, `lcd-snapshot-symbols/` under `build/perf-codex/`.
APK SHA256: `8553650c03fbb563ccba39748f5ed3adda55b7b9e99befe30c2872cf0614a34c`.

The exception/LCD diagnostic above confirms Jetpack uses this linear path. Its final six
five-second timing blocks contain 1,803 updates, 1,566 forced repaints (86.9%), zero skips:
882.20 ms total snapshot wall time, of which 760.40 ms is on forced repaints. Mean snapshot
**489.29 µs**, forced mean **485.57 µs**, maximum **3216.35 µs**. These blocks are approximately
30 s, have no explicit timestamp fields, and are not the exact exception or FPS window.
This is measurable main-loop/BQL work (roughly 25 ms/s on forced snapshots). Same-APK skip
off/on pairs must check whether retained dirty bits add work elsewhere before enabling it.

Three same-APK pairs on the Snapdragon 865-class Poco, Jetpack title, 30 s per arm.
Order off/on, **on/off**, off/on. Array cache on; lookup inlining, dirty-query fast path,
exception/full accounting and the live recorder off. LCD timing is enabled in both modes.
All six screenshots confirm the title and recorder off; zero skipped frames.

| Pair | Mean fps off → on | Ceiling | Median | Low | 1% low | Hitches (>50 ms) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 51.33 → 48.90 | 56 → 58 | 52 → 52 | 44 → 34 | 20.01 → 14.89 | 15 (1086.8 ms) → 36 (2747.4 ms) |
| 2 | 54.03 → 54.63 | 58 → 60 | 54 → 54 | 50 → 50 | 28.66 → 29.18 | 4 (219.3 ms) → 5 (302.5 ms) |
| 3 | 52.83 → 51.77 | 58 → 58 | 52 → 52 | 48 → 42 | 28.58 → 20.40 | 4 (202.7 ms) → 13 (1017.8 ms) |

Aggregate mean **52.73 → 51.77 fps (−1.8%)**; two pairs lose. Hitches increase in all three:
23 → 54 total, duration **1508.9 → 4067.8 ms** over 90 s per mode. Reject this version and
keep it off; a saving in the snapshot stage did not translate to useful frame pacing. The
measurements do not establish which retained-dirty-bit side effect caused the regressions.

Last six five-second timing blocks per arm (approximately 30 s, not the exact FPS windows):

| Pair | Snapshot mean µs/update off → on | Forced / total updates off → on | Skipped snapshots off → on |
| --- | --- | --- | --- |
| 1 | 521.84 → 98.47 | 1547 / 1803 → 1472 / 1804 | 0 → 1472 |
| 2 | 517.22 → 48.09 | 1621 / 1803 → 1641 / 1803 | 0 → 1641 |
| 3 | 498.32 → 82.69 | 1587 / 1803 → 1558 / 1801 | 0 → 1558 |

The aggregate snapshot mean is 512.46 → 76.42 µs/update, total 2771.91 → 413.27 ms across
those blocks. Skipped work is recorded as zero; this excludes branch/timer overhead on a skip.
Each skipped forced repaint leaves dirty bits set, so this is not an isolated TLB-reset saving.

Exact half-open frame windows (monotonic ns):

| Pair | Off [start, end) | On [start, end) |
| --- | --- | --- |
| 1 | [426101847474170, 426131847474170) | [426281864197070, 426311864197070) |
| 2 | [426623018924180, 426653018924180) | [426451400211589, 426481400211589) |
| 3 | [426798180816040, 426828180816040) | [426976255640243, 427006255640243) |

Evidence: `build/perf-codex/poco-lcd-snapshot-{1,2,3}-{off,on}/` (frames, scene screenshots,
engine logs, timing blocks). `lcd-snapshot-results.json` retains individual hitch times/durations;
`lcd-snapshot-analyze.py` regenerates it. Do not repeat this skip unchanged.

Removed from current engine source in `197f75d7ae`: every linear update now takes and
clears its snapshot, including forced repaints. The independent snapshot/stage diagnostics
remain; skip fields are fixed at zero for log-reader compatibility. The environment switch
`LT_LCD_FORCED_SNAPSHOT_SKIP` is no longer read. Linux/Android builds succeeded
(`build/perf-codex/lcd-skip-remove-build.txt`). No new FPS gain is claimed for removing a
default-off rejected experiment. Frozen comparison APKs and symbols remain unchanged,
including the queued current-build lookup confirmation and LCD stage diagnostic APK.


Separate experiment, engine `42d50e5d2e`: `LT_TLB_DIRTY_FAST=on` (default off) preserves dirty
snapshot clearing and TLB locking, but checks the compact entry's flags and host address range
before fetching the full entry's slow-write flags. Invalid, already tracked and out-of-range
entries avoid that metadata fetch. Both paths check the same flags and update the same entries;
the experiment is selected outside the loops. This does not retain dirty bits or skip resets.

The LCD timing includes the bitmap copy/clear, TLB reset and
`memory_global_after_dirty_log_sync()`. TCG's after-sync hook synchronously calls `run_on_cpu`
to wait for the current TB to finish, preserving dirty-write ordering. The full ~0.5 ms snapshot
cost cannot be attributed to the TLB scan alone. If the scan experiment is inconclusive, split
those stages before further changes; removing the TB barrier without equivalent ordering is unsafe.

Linux/Android builds succeeded (`build/perf-codex/tlb-dirty-build.txt`). Android disassembly
confirms the full-entry load follows the flag/range branches (`tlb-dirty-disassembly.txt`).
Three same-APK Poco off/on pairs, reversing the second pair, with forced-snapshot
skipping off and LCD snapshot timings on in both modes are complete below.
Frozen APK/symbols: `tlb-dirty.apk`, `tlb-dirty-symbols/` under `build/perf-codex/`.
Frozen measured engine is `1cd3a771c3`, which also timestamps LCD snapshot blocks explicitly.
Linux/Android builds succeeded (`lcd-timestamps-build.txt`). No TLB scan code changed after
`42d50e5d2e`. No previous measurements are replaced.
APK SHA256: `9d568d3fb0262661595912ac1641d9a84dff485ad93bc43f6dda03eca199fa24`.

Snapdragon 865-class Poco, Jetpack title, 30 s per arm. All six final screenshots
reviewed: same title, manual recorder off, zero skipped frames. Array cache on;
other optimization experiments, full accounting and live recording off.

| Pair | Mean fps off → on | Ceiling | Median | Low | 1% low | Hitches (>50 ms) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 52.13 → 53.13 | 58 → 58 | 52 → 54 | 46 → 48 | 28.32 → 28.39 | 6 (302.9 ms) → 4 (221.5 ms) |
| 2 (on first) | 53.43 → 52.37 | 58 → 56 | 54 → 52 | 50 → 48 | 28.43 → 28.26 | 4 (203.6 ms) → 2 (100.7 ms) |
| 3 | 51.57 → 53.50 | 58 → 58 | 52 → 54 | 44 → 48 | 20.52 → 28.49 | 8 (568.7 ms) → 5 (271.4 ms) |

Aggregate mean **52.38 → 53.00 fps (+1.2%)**, two of three pairs ahead, with the
reversed pair losing 1.07 fps. Hitches total 18 → 11, duration 1075.1 → 593.5 ms
over 90 s per mode. This is mixed throughput evidence within the session spread;
the lower hitch counts alone do not establish a repeatable pacing improvement.

The enclosing snapshot means are 521.03/511.41/494.90 → 545.40/473.65/507.85 µs/update
across separate six-block timing windows. Weighted overall **509.11 → 508.97 µs/update**:
no confirmed reduction in the targeted cost. There are 5411 → 5413 updates,
4733 → 4775 forced, no skipping. Keep the switch **off** pending stage timing;
no repeatable game or snapshot speedup established. The stage diagnostic follows the
current-build lookup confirmation on the phone, using its preserved APK.

Exact FPS [start, end) ns:

| Pair | Off | On |
| --- | --- | --- |
| 1 | [428683212366415, 428713212366415) | [428847099057394, 428877099057394) |
| 2 | [429195619038928, 429225619038928) | [429025941462430, 429055941462430) |
| 3 | [429362738087926, 429392738087926) | [429534492208850, 429564492208850) |

Separate snapshot [begin, end] ns:

| Pair | Off | On |
| --- | --- | --- |
| 1 | [428680026891676, 428710094953592] | [428847098134894, 428877163990247] |
| 2 | [429195018566063, 429225052105948] | [429026492203523, 429056558625960] |
| 3 | [429359454210948, 429389537220624] | [429531624729476, 429561709085246] |

Raw evidence: `build/perf-codex/poco-tlb-dirty-{1,2,3}-{off,on}/`;
`tlb-dirty-analyze.py` and `tlb-dirty-results.json` retain exact windows and each hitch.
Scene overview: `tlb-dirty-all-six-scenes.png`. The completed runner handed the phone
to the existing lookup-confirmation runner; no historical commit was remeasured.

Source follow-up (unimplemented): under the TLB lock, `cpu->neg.tlb.c.dirty` records
which MMU modes can contain entries. It starts at zero, every insertion sets its bit,
and clearing a bit accompanies flushing that mode's main and victim tables. A reset
could conservatively skip modes with a clear bit. Do not infer emptiness merely from
`n_used_entries` without accounting for victims. Measure the reset stage's share and
active-mode counts before implementing another scan experiment.

### LCD snapshot stages (diagnostic, captures complete)

Engine `9fd2a85805`, default-off `LT_LCD_SNAPSHOT_STAGES`, separately times presync,
setup/allocation, atomic bitmap copy/clear, TLB reset, clear listeners and the TCG
ordering barrier. It counts requested-range dirty pages and empty snapshots without
skipping any work. Each block shares the enclosing snapshot's exact boundaries;
output follows the LCD unlock. Timings include timer overhead and host descheduling.
Summed stages exclude page counting and some wrapper bookkeeping, included in the
outer duration. This distinguishes scan costs from the synchronous vCPU wait before
considering another dirty-tracking change. See PROFILING.md for field definitions.

Linux/Android builds succeeded (`build/perf-codex/lcd-stages-build.txt`). Frozen
APK/symbols: `lcd-stages.apk`, `lcd-stages-symbols/` under `build/perf-codex/`.
APK SHA256: `2dec4262f195fea2e356906be637016fbfe319f55e2ba0e6375cf6cf61f6f13d`.
Four focused stage arms (TLB fast off/on/on/off) completed after the ordinary TB
lookup confirmation. Snapdragon 865-class Poco, Jetpack title, 30 s per arm;
array cache on, other experiments/diagnostics, live recorder and full accounting
off. All four final scenes reviewed: correct title, recorder off, zero skips.
Stage timing is on in both modes; these are diagnostic FPS windows and do not
measure diagnostic overhead.

| Pair | Diagnostic mean fps off → on | Ceiling | Median | Low | 1% low | Hitches (>50 ms) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 52.80 → 53.17 | 56 → 58 | 54 → 54 | 48 → 48 | 28.78 → 28.35 | 5 (286.4 ms) → 3 (151.8 ms) |
| 2 (on first) | 48.23 → 54.57 | 58 → 58 | 50 → 54 | 32 → 48 | 11.93 → 28.49 | 44 (3513.6 ms) → 3 (168.8 ms) |

The second off window has substantial hitching; retain it, with cause unknown.
The ordinary three-pair result above remains mixed. Do not treat these diagnostic
rates as proof of a repeatable throughput or pacing gain.

Stage means below use the last six complete five-second blocks per arm, approximately
30 s, separately bounded from each FPS window. All means are µs/snapshot.

| Pair / mode | Updates / empty | Presync | Setup | Bitmap | Reset | Clear listeners | Barrier | Outer total |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 off | 1805 / 213 | 1.543 | 2.196 | 1.400 | 82.294 | 0.855 | 443.317 | 535.666 |
| 1 on | 1803 / 208 | 1.689 | 2.266 | 1.459 | 56.034 | 0.805 | 477.121 | 543.516 |
| 2 off | 1804 / 358 | 1.561 | 2.375 | 1.648 | 108.221 | 0.937 | 444.988 | 564.205 |
| 2 on | 1803 / 163 | 1.474 | 2.093 | 1.494 | 56.233 | 0.770 | 445.990 | 512.152 |

The fast scan reduces the measured reset stage in both pairs, by 26.3 and 52.0 µs.
The synchronous vCPU ordering barrier dominates at **443–477 µs**, about 79–88%
of the outer total. The first pair's enclosing total does not improve despite
the cheaper reset. Keep fast reset off for ordinary use pending a repeatable game
benefit; the stage saving alone does not resolve the 60 fps goal.

Requested-range empty snapshots are **571/3609 (15.8%) off, 371/3606 (10.3%) on**.
These counts vary with delivered work; they are not an optimization result. They
exclude aligned bitmap padding. A zero requested-range count alone cannot justify
skipping tracking resets for the entire copied/cleared bitmap or the ordering barrier.
No empty-snapshot skipping has been implemented.

Exact bounds (monotonic ns; FPS is half-open, stage bounds are logged block endpoints):

| Pair / mode | FPS [start, end) | Stage [begin, end] |
| --- | --- | --- |
| 1 off | [430748371824533, 430778371824533) | [430747237959377, 430777322421397] |
| 1 on | [430921829982332, 430951829982332) | [430922795527071, 430952846896330] |
| 2 off | [431264035025743, 431294035025743) | [431263068462149, 431293134085679] |
| 2 on | [431088954182216, 431118954182216) | [431086203683258, 431116254624497] |

Evidence: `build/perf-codex/poco-lcd-stages-{1,2}-{off,on}/`, analyzer
`lcd-stages-analyze.py`, `lcd-stages-results.json`, and `lcd-stages-all-four-scenes.png`.
Each arm retains its raw logs and matching inner/outer stage blocks. The queue
stopped the guest cleanly, verified no qemu engines and restored baseline debug-env
before handing the phone to the separate BQL diagnostic comparison.

### Publisher notification preservation (no repeatable throughput gain)

`Portable/src/Runtime.cpp` adds `LT_PUBLISH_WAKE_EARLY=on` (default off). The old loop snapshots
the engine notification count after frame copying, listener callbacks and audio draining. A
notification during that work is then treated as already handled by its 10 ms wait. The experiment
snapshots before work and waits only if that count is unchanged. Control/audio polling retains
the 10 ms timeout. There is no lock held across engine calls or callbacks.

Independent `LT_PUBLISH_WAKE_PROF=on` (default off) logs exact five-second block boundaries,
loop counts, notifications during work, subsequent wait duration and frame timestamp age at
publication. A notification during work may already have been consumed by the frame acquisition;
it is not evidence of a missed frame by itself. Timestamp age includes engine LCD/pacing work
before capture, rather than measuring only publisher latency. Focused A/B captures below
are separate from ordinary FPS baselines; no throughput improvement is established.

Linux/Android builds succeeded (`build/perf-codex/publish-wake-build.txt`). Frozen APK/symbols:
`publish-wake.apk`, `publish-wake-symbols/` under `build/perf-codex/`, engine `4d06b57f50`.
APK SHA256: `ca730421e1c3f1e213f433b774130c35c9c977616e19fdc0404ba7610e814578`.

Two same-APK diagnostic pairs on the Snapdragon 865-class Poco, Jetpack title, 35 s
per arm, order off/on/on/off. Publisher timing on in both; array cache on; other
experiments, full accounting and manual recorder off. All four final screenshots
confirm the title and recorder off; zero skipped frames. The runner received SIGTERM
during the final off launch, after which the guest remained running at the correct
title. That arm was captured after another 40 s settling without relaunching; no
completed window was repeated. This additional settling is a comparison limitation.

| Pair | Diagnostic mean fps off → on | Ceiling | Median | Low | 1% low | Hitches (>50 ms) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 52.51 → 53.71 | 58 → 58 | 54 → 54 | 46 → 48 | 20.53 → 28.15 | 16 (1037.4 ms) → 6 (338.0 ms) |
| 2, reversed | 54.46 → 53.06 | 58 → 56 | 54 → 54 | 50 → 48 | 28.61 → 28.53 | 6 (305.0 ms) → 3 (153.0 ms) |

Aggregate mean **53.49 → 53.39 fps (−0.2%)**, one pair ahead and one behind. Hitches
22 → 9, total duration 1342.3 → 491.1 ms over 70 s per mode, but frame age barely
changes and fewer diagnostic hitches do not establish a repeatable pacing gain.
Keep default off; no steady-60 improvement demonstrated.

| Pair | Frame age mean ms off → on | Max ms off → on | Notifications during work off → on | Subsequent waits ms off → on |
| --- | --- | --- | --- | --- |
| 1 | 1.987 → 1.959 | 13.102 → 13.792 | 1 → 4 | 10.273 → 0.004 |
| 2 | 2.047 → 2.031 | 11.328 → 8.038 | 7 → 3 | 70.702 → 0.006 |

These timing summaries cover six five-second blocks, separately from FPS bounds.
They confirm that the switch avoids waiting after notifications during work, but
those notifications may have already been consumed; they do not count delayed frames.

Exact half-open FPS windows and timing bounds (monotonic ns):

| Pair / mode | FPS [start, end) | Six timing blocks [begin, end] |
| --- | --- | --- |
| 1 off | [427949592197632, 427984592197632) | [427954990878359, 427985007655431] |
| 1 on | [428124689861107, 428159689861107) | [428128441541887, 428158473281250] |
| 2 on | [428307779336350, 428342779336350) | [428313373858118, 428343406784461] |
| 2 off | [428511138843147, 428546138843147) | [428517238666947, 428547266432664] |

Evidence: `build/perf-codex/poco-publish-wake-{1,2}-{off,on}/` contains frame logs,
scenes, engine logs and raw timing blocks. `publish-wake-results.json` retains each
hitch end/duration and exact windows; `publish-wake-analyze.py` regenerates it.

### Single-page dirty queries (local saving; no repeatable game gain)

Engine `cc6063a40f` adds `LT_DIRTY_QUERY_FAST=on` (default off). A fresh Poco Jetpack profile
put `physical_memory_is_clean` at 1.25% inclusive and `physical_memory_get_dirty_flag` at
0.59% self / 0.89% inclusive. Single-page flags now use a direct bit read instead of a generic
range scan; the combined three-client query shares one RCU guard. All bitmap pointers remain
inside their RCU lifetime, including concurrent growth. No coroutine TLS pointer is cached.

Linux/Android builds succeeded (`build/perf-codex/dirty-query-build.txt`). Frozen APK/symbols:
`dirty-query.apk`, `dirty-query-symbols/` under `build/perf-codex/`. The ordinary pairs and focused host timings below are complete.
Keep other experiments off when measuring.
APK SHA256: `6c92d049a1f724ce4667b8cd86f370f910093cd3dca0e3ec008674ab35f9ea37`.

Three interleaved off/on pairs on the Snapdragon 865-class Poco, Jetpack title, 30 s per arm.
Array cache on; all other experiments, live recorder and full accounting off. All six screenshots
confirm the title and recorder off, with zero skipped frames. The profiler below was absent.

| Pair | Mean fps off → on | Ceiling | Median | Low | 1% low | Hitches (>50 ms) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 51.47 → 53.30 | 58 → 58 | 52 → 54 | 44 → 48 | 19.81 → 20.72 | 16 (1152.3 ms) → 10 (621.6 ms) |
| 2 | 54.10 → 52.80 | 60 → 58 | 54 → 54 | 50 → 44 | 28.33 → 28.03 | 5 (256.1 ms) → 11 (701.9 ms) |
| 3 | 54.43 → 54.90 | 58 → 60 | 54 → 54 | 50 → 50 | 28.75 → 28.65 | 4 (217.3 ms) → 3 (153.3 ms) |

Aggregate mean **53.33 → 53.67 fps (+0.6%)**, within session drift; one pair loses 1.30 fps.
Hitches total 25 → 24, duration 1625.7 → 1476.8 ms over 90 s per mode. No repeatable game or
pacing gain; keep default off; focused query costs follow. LT Bench results are mixed too:
`icall` best/median ns 30.1/34.3, 30.3/31.9, 30.2/32.0 → 30.5/33.2, 30.0/30.7, 30.7/30.8.

Exact half-open frame windows (monotonic ns):

| Pair | Off [start, end) | On [start, end) |
| --- | --- | --- |
| 1 | [424814735180807, 424844735180807) | [425029575935256, 425059575935256) |
| 2 | [425246966154965, 425276966154965) | [425473577875816, 425503577875816) |
| 3 | [425699973660209, 425729973660209) | [425918197469605, 425948197469605) |

Evidence: `build/perf-codex/poco-dirty-query-{1,2,3}-{off,on}/` contains frame logs,
LT Bench output, engine logs and scene screenshots. `dirty-query-results.json` retains metrics,
individual hitch end times/durations and full LT Bench results; `dirty-query-analyze.py` regenerates it.

Engine `4d06b57f50` separately adds `LT_DIRTY_QUERY_PROF=on` (default off) for focused host timings:
CPU 0 samples one in 256 combined queries, with explicit five-second block boundaries, query/sample
counts and summed/max durations. Timings include timer overhead and descheduling, exclude logging
and profiler bookkeeping. The diagnostic itself may perturb scheduling; capture it separately
from the ordinary FPS pairs. Linux/Android builds succeeded (`dirty-query-prof-build.txt`).
Frozen diagnostic APK/symbols: `dirty-query-prof.apk`, `dirty-query-prof-symbols/`. Runtime results follow.
APK SHA256: `b1d695fda6aef562790e1ec4feab45149571e4919989574f089e9c07fc340341`.

Two focused off/on pairs, second reversed, same diagnostic APK on the Snapdragon 865-class
Poco, Jetpack title. Sampler and perfmaps enabled; array cache on; other experiments, full
accounting and live recorder off. All four screenshots confirm the title and recorder off;
zero skipped frames. Each timing sum covers the last six explicitly bounded blocks, approximately
30 s. Each FPS window below is a separate 35 s diagnostic capture. These are not ordinary
FPS baselines and supply no sampler-overhead measurement.

| Pair | Sampled mean ns/query off → on | Samples off → on | Queries/s off → on |
| --- | --- | --- | --- |
| 1 | 502.74 → 427.29 (−15.0%) | 12582 → 15404 | 107309.8 → 131411.9 |
| 2 | 495.71 → 419.93 (−15.3%) | 11768 → 11544 | 100359.1 → 98474.2 |

A repeatable local saving of about 75 ns/query, including timer overhead/descheduling in both
arms. Call rates differ, so do not turn this into an end-to-end saving estimate. Ordinary game
pairs above remain mixed, and the diagnostic windows also provide no FPS gain:

| Pair | Diagnostic mean fps off → on | Ceiling | Median | Low | 1% low | Hitches (>50 ms) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 54.54 → 53.14 | 58 → 58 | 56 → 54 | 50 → 48 | 28.29 → 28.32 | 5 (265.7 ms) → 5 (253.2 ms) |
| 2 | 55.51 → 51.80 | 60 → 58 | 56 → 52 | 50 → 42 | 28.67 → 20.16 | 2 (117.2 ms) → 14 (969.0 ms) |

Keep default off for ordinary use. This confirms a cheaper query, not a useful change in game
pacing. It does not resolve the steady-60 goal.

Exact query timing blocks (monotonic ns):

| Pair | Off [begin, end] | On [begin, end] |
| --- | --- | --- |
| 1 | [427152179231634, 427182195048029] | [427371128798217, 427401136924716] |
| 2 | [427764029390672, 427794047688004] | [427582389697043, 427612400233438] |

Evidence: `build/perf-codex/poco-dirty-focused-{1,2}-{off,on}/` contains the raw six-block
summaries, full logs, screenshots and 35 s frame logs. `dirty-query-focused-results.json` retains
exact FPS boundaries and every hitch end/duration; `dirty-query-focused-analyze.py` regenerates it.
Each arm of pair 1 additionally has a subsequent 15 s CPU profile and before/after guest maps.

### First runtime guest-PC attribution (2026-10-18, diagnostic)

`tools/perf/guestpc.py` completed its first real mapped profile on the Snapdragon 865-class
Poco: Jetpack title, dirty-query fast path off, sampler and perfmaps on, recorder/full accounting
off. Exact 15 s profile sample bounds: **427193086575160 → 427208091264998 ns**.
Worker PID 11991; matching before/after maps required. 18,721 total samples, zero lost;
13,355 vCPU self samples. Guest instructions map **44.44% of vCPU event weight** (5,942 samples),
with 7,413 unmapped samples, zero ambiguous or unstable sampled mappings. Unmapped includes
native work and absent JIT mappings, not just native code. No guest process/stack attribution.

A local symbol cache was built from the exact frozen `dirty-query-prof-symbols/libqemu-arm.so`
and previously cached matching host libraries, with no device reads during the next benchmark.
Native self costs: `tb_lookup_legacy` 4.95%, `do_ld4_mmu` 2.70%, `qht_lookup_custom` 1.56%,
TLS dynamic resolver 1.03%, dirty-flag queries 0.59%, bitmap scanning 0.48%.
Call-chain unwinding errors: **46.13%**; incomplete caller attribution cannot establish absence
of a cost. This is a diagnostic baseline, not an FPS or overhead comparison.

The subsequent enabled-arm profile also resolves successfully: worker 12925, exact sample
bounds **427409397840078 → 427424407670801 ns**, 18,678 total samples / zero lost,
13,428 vCPU samples. Stable before/after maps resolve **44.11%** of vCPU event weight
(5,919 mapped / 7,509 unmapped), with zero ambiguous/unstable sampled mappings. Native self
costs include lookup 4.73%, `do_ld4_mmu` 3.12%, TLS resolver 0.92%; call-chain unwind errors
46.08%. Call rates differ between arms, so these normalized shares are diagnostic context,
not absolute CPU-time savings. Evidence is in the matching `poco-dirty-focused-1-on/` directory.

Raw guest address bands account for 17.01% of vCPU event weight in 0xc0000000–0xcfffffff,
13.94% in 0x30000000–0x3fffffff and 13.44% below 0x10000000. The hottest 4 KiB instruction
page is 0x001b1000 (1.72% of vCPU weight). These ranges/pages are not resolved function names
or process identities. Preserve that distinction until matching guest symbols are available.

Evidence: `build/perf-codex/poco-dirty-focused-1-off/` (`cpu-profile/`, before/after maps,
`guest-pcs.{txt,json}`, `symbolized/{self,callgraph}.txt`). Reader command:
`guestpc.py cpu-profile/perf.data perf-11991-after.map --before-map perf-11991-before.map`.
Local symbolization recipe: `build/perf-codex/symbolize-focused.py`.

### TB lookup hit path (2026-10-18, default off pending confirmation)

Engine `164ec930cc` adds `LT_TB_LOOKUP_INLINE=on` (default off). A fresh Poco Jetpack profile
attributed 5.19% self samples to `tb_lookup`; 46.2% of call chains failed to unwind, so caller
attribution is incomplete. Disassembly showed its common hit path out of line, including a
stack frame and compiler-generated register clearing on return. The experiment inlines only
the fully validated jump-cache hit into callers. Misses and all accounting runs use the existing
lookup; misses repeat the initial probe. Compiler register hardening remains enabled.

Preliminary Linux and Android builds succeeded (`build/perf-codex/tb-lookup-inline-build.txt`).
Android disassembly confirms a hit skips the extra function call.

After removing the separate indexed-cache experiment, Linux/Android builds also succeeded on
`fd92f34185` (`build/perf-codex/tb-lookup-inline-final-build.txt`). The inlining experiment is
still default off. Its frozen APK/symbols are `tb-lookup-inline.apk` and
`tb-lookup-inline-symbols/` under `build/perf-codex/`. APK SHA256:
`7f20b957ab434d21ccbdaf2ba52657a34dee5c7eaa66ec87e391d64cf8cc5ab0`.

Three same-APK interleaved off/on pairs on the Snapdragon 865-class Poco, Jetpack title,
30 s each. Array caching on; batch fast path and context timing off; no live recorder/full
accounting. All six scene screenshots confirm the title and recorder off. Zero skipped frames.

| Pair | LT Bench `icall` best / median ns, off → on | Mean fps off → on | Ceiling | Median | Low | 1% low | Hitches (>50 ms) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 27.1 / 28.8 → 26.8 / 28.5 | 52.03 → 54.13 | 56 → 58 | 52 → 54 | 46 → 50 | 27.94 → 28.27 | 8 (419.0 ms) → 6 (304.5 ms) |
| 2 | 31.2 / 33.0 → 26.8 / 27.5 | 53.60 → 54.73 | 58 → 60 | 54 → 54 | 50 → 50 | 28.61 → 27.99 | 5 (301.9 ms) → 8 (425.1 ms) |
| 3 | 31.6 / 32.7 → 26.5 / 27.1 | 53.80 → 54.43 | 58 → 58 | 54 → 54 | 50 → 50 | 28.80 → 28.50 | 3 (153.4 ms) → 2 (116.9 ms) |

Aggregate mean **53.14 → 54.43 fps (+2.4%)**, ahead in three of three. This is a small throughput
gain; no consistent pacing improvement: hitches total 16 → 16, duration 874.3 → 846.4 ms over
90 s per mode, 1% lows remain ~28 fps. `icall` improves clearly in pairs 2/3; other LT Bench
costs are mixed with frequency variation. Keep the default off until a reverse-order confirmation
and a combined baseline; this is not steady 60 fps.
A fresh same-APK confirmation completed using current engine `9fd2a85805`
(`lcd-stages.apk`, SHA256 above), order on/off, off/on, on/off, 30 s per arm.
All diagnostic switches and other experiments were off, array caching on. All six
final screenshots reviewed: correct title scene, recorder off, zero skipped frames.

| Pair | Mean fps off → on | Ceiling | Median | Low | 1% low | Hitches (>50 ms) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 (on first) | 52.60 → 53.23 | 58 → 58 | 52 → 54 | 46 → 48 | 27.88 → 25.85 | 6 (305.3 ms) → 12 (822.6 ms) |
| 2 | 52.00 → 53.30 | 58 → 58 | 52 → 54 | 46 → 48 | 28.09 → 28.13 | 7 (534.4 ms) → 6 (334.5 ms) |
| 3 (on first) | 53.63 → 53.87 | 58 → 58 | 54 → 54 | 48 → 48 | 22.14 → 28.28 | 9 (683.3 ms) → 7 (385.9 ms) |

Aggregate mean **52.74 → 53.47 fps (+1.4%)**, ahead in all three confirmation
pairs, including both on-first pairs. Across the original and confirmation sets,
all six pairs favor inlining for throughput, but pacing still does not improve
consistently: confirmation hitches total **22 → 25**, duration **1523.0 → 1542.9 ms**
over 90 s per mode. Keep default off pending a combined baseline; no steady-60 or
stutter fix established. The completed runner handed the phone to LCD stage captures.
Artifacts: `build/perf-codex/poco-tb-confirm-{1,2,3}-{off,on}/`, summarized by
`tb-confirm-analyze.py` / `tb-confirm-results.json`, with exact frame windows and hitches.

The combined baseline completed on current engine `f43fbe4986` in frozen
`vfp-mul-zero.apk` (identity/SHA256 in the VFP section), Snapdragon 865-class Poco,
Jetpack title, three 30 s pairs off/on, on/off, off/on. Both `LT_GLES_ARRAY_CACHE`
and `LT_TB_LOOKUP_INLINE` vary together; all diagnostics, other experiments and
the live recorder/full accounting are off, including both VFP switches. All six
final screenshots reviewed: correct title, recorder off, zero publisher skips.
This measures their combined effect rather than adding earlier percentages.

| Pair | Mean fps both off → both on | Ceiling | Median | Low | 1% low | Hitches (>50 ms) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 53.63 → 53.07 | 58 → 58 | 54 → 54 | 50 → 48 | 28.33 → 28.16 | 3 (150.7 ms) → 7 (360.9 ms) |
| 2 | 53.57 → 54.17 | 58 → 58 | 54 → 54 | 50 → 50 | 28.26 → 28.24 | 1 (50.6 ms) → 3 (151.9 ms) |
| 3 | 52.97 → 53.83 | 58 → 58 | 52 → 54 | 50 → 50 | 28.43 → 28.14 | 9 (503.1 ms) → 7 (371.8 ms) |

Aggregate mean **53.39 → 53.69 fps (+0.6%)**, two of three pairs ahead, within
session drift. Hitches **13 → 17**, total duration **704.4 → 884.6 ms** over 90 s
per mode; each enabled 1% low is slightly lower. No repeatable combined throughput
or pacing benefit established. Keep lookup inlining default off; array caching
remains on for its direct host-call saving. The earlier six individual lookup
wins do not establish a combined game benefit or steady 60 fps.

Exact half-open frame windows (monotonic ns):

| Pair | Both off [start, end) | Both on [start, end) |
| --- | --- | --- |
| 1 | [432873115121118, 432903115121118) | [433040687255169, 433070687255169) |
| 2 | [433388548898943, 433418548898943) | [433215693039738, 433245693039738) |
| 3 | [433569569402988, 433599569402988) | [433737896954955, 433767896954955) |

Raw logs/settings/scenes: `poco-combined-baseline-{1,2,3}-{off,on}/`;
`combined-baseline-analyze.py` / `combined-baseline-results.json` retain every
hitch and exact bounds; reviewed montage `combined-baseline-scenes.png`. The
runner confirmed clean Stop, no qemu engine and baseline environment readback
before handing the phone to the already queued masked-IRQ focused comparison.

Evidence: `build/perf-codex/poco-tb-lookup-inline-{1,2,3}-{off,on}/` contains exact frame logs,
screenshots, environment, LT Bench and engine logs. `tb-lookup-inline-results.json` records exact
half-open 30 s boundaries and individual hitch durations. A 15 s attached call-graph profile
after the final frame window is in `poco-tb-lookup-inline-profile/` (18,037 total samples, zero lost).
Its symbolized vCPU report has 13,357 samples with 45.6% call-chain unwind errors. The inlined
hit now appears in callers (`helper_lookup_tb_ptr_cached` 4.08% self, `cpu_exec_loop` 1.00%);
`tb_lookup_legacy` is 0.09% self / 2.74% inclusive. This is one enabled-arm CPU profile, not a
matched profile A/B. TLS remains 0.95% self; dirty-flag queries 0.60%, `find_next_bit` 0.42%.

### Cache in the indirect miss helper (2026-10-19, experimental, default off)

The recent Poco native profile puts `helper_lookup_tb_ptr_cached` at 4.08% self
after lookup hit-path inlining. Source inspection found that every generated
two-entry site miss still fetches the full architectural state and performs the
validated global lookup, even for a target this same source site recently used.

Engine `6a71c1842f`, `LT_IBTC_L2=on`, adds a bounded 16K-entry side table in the
C miss helper, allocated only when enabled: **384 KiB on the 64-bit builds**.
Each entry checks both the source site's address and its complete generated key
(target PC/Thumb plus the address-space/invalidation generation). A hit promotes
the target into the existing two-entry site cache. Misses use the original full
lookup and fill only under its existing context/breakpoint guards and for
`CF_PCREL` targets. Their invalidation path renews the generation; non-PCREL
invalidation need not do so and cannot safely populate this table. ARM targets
always use PC-relative TBs. Accounting, execution logging and breakpoints use
the original lookup. Only machines with
one current and one maximum vCPU can enable the table or its diagnostic.

This differs from the rejected generated global probe and embedded indexed-site
cache: the generated path and TB layout are unchanged, and disabled mode allocates
no table. Existing jump-cache geometry and invalidation rules are unchanged;
old generation keys cannot hit after invalidation. The site address is essential:
the generated key alone omits architectural flags implicit in the source TB and
cannot safely validate a global hit. No attempt to retain hits across TLBI.

Independent default-off `LT_IBTC_L2_PROF=on` counts CPU-0 helper attempts/hits
and samples one in 256 attempted helper calls, with hit/fallback subsets and exact
approximately five-second bounds. Clocks/descheduling are included. Longjmps can
leave an attempted call without a completed timing sample. Ordinary helpers have
no clocks or counter updates; the cache and profiler switches are checked there.

Linux/Android builds succeeded (`build/perf-codex/ibtc-l2-pcrel-build.txt`), no
tests added/run. Disassembly confirms the common body is inlined into the helper
(`ibtc-l2-pcrel-disassembly.txt`); no extra common-path wrapper call. Frozen APK/symbols:
`ibtc-l2-pcrel.apk`, `ibtc-l2-pcrel-symbols/`; build identity
`ibtc-l2-pcrel-build-identity.json` (app documentation dirty only, engine clean),
app `b636084a`, engine `6a71c1842f`. APK SHA256:
`c133324d9064025139861d413df894703d854f4e6c2115838bf9c5363ee182d4`.
Library SHA256: `93fe8be855d7ac586ecd38f548376a0567d28b7cd85fbb8b042c7c90b2ef88fe`.

Focused runner/analyzer `ibtc-l2-run.py` / `ibtc-l2-analyze.py` started after the
completed IRQ-delivery comparison's exact process exit and successful cleanup
marker. Two 35 s pairs off/on, on/off; array cache on, L2 profiler on both, other
experiments/diagnostics and the recorder/full accounting off. Both pairs complete;
all four final title scenes reviewed, recorder off, zero skips. Each arm has six
complete contiguous five-second timing blocks wholly inside its frame window.

| Metric | Pair 1 off → on | Pair 2 off → on (on first) |
| --- | --- | --- |
| Helper attempts/s | 495125 → 460796 | 494547 → 465299 |
| L2 hit rate | 0 → 67.166% | 0 → 66.670% |
| Sampled mean / median block mean (ns) | 526.62 / 522.45 → 497.65 / 497.42 | 522.40 / 522.89 → 500.65 / 500.19 |
| Hit / fallback sampled mean (ns) | — / 526.62 → 438.86 / 618.30 | — / 522.40 → 448.13 / 604.52 |
| Max completed sample (µs) | 200.31 → 60.47 | 13.23 → 27.14 |
| Diagnostic mean / ceiling / median / low fps | 51.46 / 56 / 52 / 44 → 52.09 / 56 / 52 / 48 | 51.06 / 56 / 52 / 44 → 53.09 / 58 / 54 / 48 |
| Diagnostic 1% low fps | 19.98 → 28.11 | 20.10 → 28.47 |
| Hitches (>50 ms) per 35 s | 20 (1272.3 ms) → 10 (557.6 ms) | 14 (1003.6 ms) → 4 (203.8 ms) |

Sampled mean drops **5.5% / 4.2%**, repeating the local saving in reverse order.
These timings include clocks/descheduling; do not extrapolate them to whole-game
savings. Preserve all outliers, including the larger enabled maximum in pair 2.
Aggregate **diagnostic** mean **51.26 → 52.59 fps (+2.6%)**; hitches **34 → 14**,
total **2275.9 → 761.4 ms per 70 s per mode**. This is promising diagnostic pacing
evidence, not an ordinary FPS baseline or a profiler-overhead measurement. Keep
off pending separate ordinary comparisons. Three profiler-off 30 s pairs off/on,
on/off, off/on started after the exact focused process exited and its successful
clean-stop marker appeared (`ibtc-l2-ordinary-run.py`).

Exact monotonic windows (frame bounds half-open; timing blocks complete):

| Arm | Frame window [begin, end) | Selected timing [begin, end] |
| --- | --- | --- |
| 1 off | 435394732365677, 435429732365677 | 435394938718594, 435424945707645 |
| 1 on | 435574134564567, 435609134564567 | 435578795467326, 435608811278408 |
| 2 on | 435758354496528, 435793354496528 | 435763010071995, 435793017093650 |
| 2 off | 435942358760885, 435977358760885 | 435947013935779, 435977022858111 |

`ibtc-l2-results.json` retains all timing blocks, exact frame bounds and every
hitch; `ibtc-l2-analysis.txt` and reviewed `ibtc-l2-scenes.png` cover all four arms.
Runner confirmed clean Stop, no remaining qemu engine and baseline environment
readback before writing `ibtc-l2-focused-finished`. Settings/fields in PROFILING.md.

#### Ordinary helper-cache comparison

Three 30 s pairs off/on, on/off, off/on on the same frozen `ibtc-l2-pcrel.apk`;
all profiling, accounting and the user-facing recorder off. Array cache on;
other experiments off. All six final scenes reviewed: correct Jetpack title,
recorder off, zero skips. This is an ordinary FPS comparison.

| Pair | Mean off → on | Ceiling | Median | Low | 1% low | Hitches (>50 ms) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 53.70 → 54.03 | 58 → 58 | 54 → 54 | 50 → 48 | 28.10 → 28.38 | 4 (219.6 ms) → 4 (250.8 ms) |
| 2 (on first) | 51.50 → 52.30 | 56 → 56 | 52 → 52 | 42 → 48 | 20.32 → 26.83 | 11 (848.0 ms) → 9 (649.1 ms) |
| 3 | 51.97 → 54.27 | 58 → 58 | 52 → 54 | 44 → 50 | 20.75 → 28.59 | 13 (979.3 ms) → 8 (403.2 ms) |

Aggregate **52.39 → 53.53 fps (+2.2%)**, all three pairs ahead, including the
reverse-order pair. Hitches **28 → 21**, total duration **2046.9 → 1303.1 ms/90 s
per mode**. Pair 1 is nearly flat and its hitch duration increases; do not discard
it. This is promising game-level evidence alongside the repeated direct helper
saving. Keep default off pending a longer independent confirmation; it does not
establish steady 60 fps or eliminate 1% low frames. Do not add this percentage to
earlier experiments measured in other sessions.

Exact frame windows [begin, end), monotonic ns:

| Pair | Off | On |
| --- | --- | --- |
| 1 | 436116231785350, 436146231785350 | 436283160115547, 436313160115547 |
| 2 | 436637900306089, 436667900306089 | 436459567761104, 436489567761104 |
| 3 | 436817545410968, 436847545410968 | 436984607926894, 437014607926894 |

Raw `poco-ibtc-l2-ordinary-*`, `ibtc-l2-ordinary-results.json` and
`ibtc-l2-ordinary-analysis.txt` retain all frame windows and individual hitch
lengths; reviewed montage `ibtc-l2-ordinary-scenes.png`. Runner completed clean
Stop/no-qemu/baseline readback and wrote `ibtc-l2-ordinary-finished` before the
presentation diagnostic started.

### Android surfaceless context binding (unsupported; experiment removed)

Viewport make-current costs about 110 µs/call in the earlier focused capture.
Source inspection found each native EGL context binds a dummy 1x1 pbuffer even
though the renderer uses private FBOs. Engine `e9e13d795e` tried omitting that
binding behind default-off `LT_GLES_SURFACELESS`, keeping pbuffer lifetime,
release flushing and GPU-completion waits unchanged. Android ES1/2 clients must
advertise `GL_OES_surfaceless_context`; failed capability/binding checks fall back.
Linux/Android builds succeeded (`surfaceless-build.txt`), no tests added/run.
Frozen `surfaceless.apk`, symbols and `surfaceless-build-identity.json` retained.

The first off/on 35 s pair finished, user recorder off, focused context profiling
on both, array cache on, other experiments/diagnostics off. All three enabled-arm
contexts reported `surfaceless=fallback api=1 reason=client-extension`. **No
context used the experiment**, so this cannot measure its benefit. The runner
stopped further pairs and confirmed clean Stop/no-qemu/baseline readback in its
`finally` cleanup; `surfaceless-cleanup-finished` exists. Both final title scenes
reviewed, zero skips. Switch-request off/on observed FPS **53.26 → 52.34**,
ceiling **58 → 58**, median **54 → 52**, low **46 → 40**, 1% low **28.18 → 20.28**,
hitches **11 (873.8 ms) → 15 (1205.5 ms)**. These are two pbuffer diagnostic runs,
not a before/after optimization result.

Exact frame windows: off `[437866395928745, 437901395928745)`, requested on
`[438044394914042, 438079394914042)`. Raw `poco-surfaceless-focused-1-*`,
`surfaceless-results.json` and reviewed `surfaceless-scenes.png` retained.
The unsupported binding experiment was removed in `2e79c09b2e`; exact contiguous
context timing completion markers remain. `tools/perf/contextstages.py` parsed
the real logs, selects complete intervals inside exact frame bounds, rejects gaps,
and preserves every block and maxima. Do not retry this on the same ES1 driver
without new capability evidence.

### Hitch attribution with scheduler traces and timestamped CPU samples

2026-10-19: changed the investigation from whole-scene hotspot totals to
**time-aligned scheduler states and CPU samples inside individual long frames**.
These are diagnostic observations, not an optimization A/B or another measured
FPS gain. User-facing Start profile, full accounting and all focused engine timing
counters stayed off. Array cache on, all other optimization experiments off.
Frozen `hitch-sched.apk`: app `5ab5880d`, engine `2e79c09b2e`; Linux/Android builds
completed. APK SHA-256 `cebcf786c02f631abe84d64f195b3e4edff1077db72ec03da1d43a38b6a895c4`;
matching unstripped library SHA-256
`098d5b8a6ab88e484be2021abd445695176ead53a1a6404afdb78253daea19be`.

Two 65 s Perfetto scheduler/frequency traces bracket separate exact 60 s frame
windows on the Snapdragon 865-class Poco, Jetpack title. Capture 2 also attaches
simpleperf at 1 kHz `cpu-clock`, `--clockid monotonic`, frame-pointer call chains,
`-p PID -m 8 --cpu-percent 25`; no sample losses reported (80,408 process samples).
Same-worker guest-PC perfmaps taken before/after; only unambiguous stable mappings
are attributed. Perfmap writes and tracing can perturb execution, so neither
capture is an ordinary baseline. Both before/final scenes reviewed: Jetpack title,
Start profile off. Both runners completed clean Stop/no-qemu/baseline readback;
Pixel untouched.

| Capture | Mean | Ceiling | Median | Low | 1% low | Hitches >50 ms | Skipped publications |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1, scheduling | 54.50 | 60 | 54 | 50 | 28.438 | 8 (409.964 ms) | 0 |
| 2, scheduling + samples | 53.817 | 58 | 54 | 48 | 28.359 | 7 (371.120 ms) | 1 |

The single sequence gap in capture 2 is a separate **33.231 ms** interval; none of
its seven >50 ms gaps contains a skipped publication. Do not call every published
frame gap a single guest rendering call.

| vCPU state inside all >50 ms gaps | Capture 1 | Capture 2 |
| --- | --- | --- |
| Running | 340.721 ms (83.1%) | 312.659 ms (84.2%) |
| Runnable, waiting to be scheduled | 36.076 ms (8.8%) | 40.136 ms (10.8%) |
| Sleeping | 33.167 ms (8.1%) | 18.325 ms (4.9%) |

Thus **these Poco title gaps mainly contain vCPU execution**. This rules against
one long off-CPU wait as their dominant component, but does not distinguish guest
work from engine or driver work, or prove the frame path's critical dependency.
Sleeping alone does not identify BQL waits. This conclusion does not establish the
cause of earlier, longer Pixel user-capture pauses.

Capture 2 has **312 vCPU samples inside the seven hitches**, 38–55 per hitch,
versus 38,822 inside ordinary <=20 ms gaps. Sampled self-time shares:

| Region | Ordinary gaps | Hitch gaps |
| --- | --- | --- |
| Native engine | 34.85% | 41.35% |
| Translated guest, low addresses | 27.02% | 24.04% |
| Translated guest, high addresses | 18.25% | 16.67% |
| GL driver | 11.06% | 8.33% |
| Other native | 7.88% | 8.01% |
| Anonymous/unmapped | 0.94% | 1.60% |

No dominant sampled function identifies a culprit. `float32_mul` rises from
0.79% to 4.17%, but that is only **13 hitch samples**: a lead, not proof or
justification to repeat the rejected VFP-zero optimization. `tb_lookup_legacy`
is 5.32% ordinary / 3.85% hitch self samples. Guest regions do not identify the
process; kernel/dyld symbol coverage remains incomplete. Inclusive native chains
overlap and do not unwind guest stacks.

All capture-2 hitch execution is on CPU 7. Its running-time-weighted reported
frequency is **2.681 GHz**, versus **2.721 GHz** for ordinary CPU-7 intervals.
Three hitches (ending 28.435, 35.550 and 43.052 s) run entirely at the maximum
recorded **2.7456 GHz**. Frequency variation therefore cannot explain all these
hitches. Counter values are reported frequencies, not a measurement of retired
cycles or effective clock under throttling.

Trace processor v58.2 reports no error/data-loss counters and complete vCPU state
coverage for each 60 s window. MONOTONIC-to-trace clock offset spread is 103 ns /
1 ns; no suspend occurred. Exact windows [begin,end), monotonic ns:

- Capture 1: `[444628761101894,444688761101894)`, PID 11060, vCPU TID 11266.
- Capture 2: `[444989508326028,445049508326028)`, PID 12366, vCPU TID 12545.

Reusable `tools/perf/hitchsched.py` and `hitchcpu.py` processed these real captures;
no tests added/run. `tools/perf/hitch-sched.pbtxt` retains the capture configuration.
Raw traces, frames, reviewed scenes, maps, perf data, per-gap states/frequencies
and CPU distributions remain under `build/perf-codex/poco-hitch-sched-{1,2}/`;
build identity/symbols are adjacent. Next: correlate a few frame-path milestones
(guest present → LCD output → frontend publication), with minimal default-off
instrumentation, to locate where cadence first breaks. More scene-wide GL totals
will not answer that question; preserve required GPU synchronization.

#### Sparse milestones locate delayed guest framebuffer flips

Engine `78eaa87497` adds default-off `LT_FRAME_PATH=PATH`: 131,072 bounded
in-memory records (about 5 MiB), dumped only after `qemu_cleanup` stops vCPU and
device activity. No allocation, mutex acquisition or file I/O on producers.
It timestamps explicit GL surface presents, LCD register flips, vsync/scan,
capture/suppression and engine publication. No guest package change or user
recorder. Linux/Android builds passed. Frozen `frame-path.apk`, symbols and
identity retained; app `85e09f5d`, APK SHA-256
`5e1dac13bebbc141ac0cbc3375c07104bf4876083be91f8bc9d8aa981daf3b00`.

One focused 60 s Poco Jetpack title capture, with a concurrent scheduler trace:
array cache on, other experiments and diagnostics off, no simpleperf/perfmaps.
Diagnostic metrics: **52.683 fps**, ceiling **56**, median **52**, low **48**,
1% low **28.042**, **6 hitches (301.566 ms)**, zero skipped publications.
This is not an optimization comparison or ordinary FPS baseline. Both scenes
reviewed, Start profile off; clean Stop/no-qemu/baseline readback confirmed.
Recorder footer: **83,047 events, zero dropped/incomplete**, engine write-error=no.
Perfetto reports no error/data-loss counters, full state coverage, constant clock
offset. PID 14628, vCPU TID 14827; exact frame bounds
`[445850097880699,445910097880699)` monotonic ns.

**All six gaps already exist at engine publication.** Frontend lag is
0.193–0.606 ms at the start and 0.248–0.635 ms at the end. Each gap contains
three timely LCD ticks, a single late guest framebuffer flip and one publication;
there are no identical-capture suppressions. Maximum virtual-deadline lateness
inside these gaps is **0.076662 ms**. This locates their late milestone before
the LCD latches the guest's new framebuffer, rather than in frontend copying or
a delayed LCD timer.

| Gap end (s) | Frontend gap ms | Engine gap ms | Guest flip after previous publication ms | Latest surface-present end to flip ms |
| --- | --- | --- | --- | --- |
| 0.551 | 50.462 | 50.249 | 34.848 | 2.845 |
| 9.134 | 50.289 | 49.912 | 47.938 | 40.950 |
| 11.384 | 50.107 | 50.050 | 35.478 | 1.395 |
| 22.801 | 50.135 | 50.277 | 43.048 | 41.948 |
| 53.367 | 50.032 | 50.006 | 33.289 | 38.380 |
| 54.884 | 50.541 | 50.487 | 35.757 | 3.656 |

One explicit GL-present context, 3,205 calls/window, all success, maximum
**5.298 ms**; calls overlapping hitches are at most **2.936 ms**. The 9.134 s
and 22.801 s gaps contain a present completed early, followed by about 41–42 ms
before the guest flip. These timestamps do not prove that a particular present
is the frame later flipped: app surfaces and final scanout bases differ. They
do show that timing host present calls alone misses the late guest-side stage.
The 53.367 s gap has its sole overlapping present after its flip.

Scheduler states distinguish different cases: the 9.134 s gap includes
**25.535 ms runnable delay / 23.596 ms execution**, while the other five contain
37.910–43.150 ms execution. Do not generalize the earlier 83–84% execution share
to every capture or dismiss host scheduling. Next: correlate guest address-space
residency through the render-to-flip interval to distinguish delayed app work
from other guest/kernel/composition work.

`tools/perf/framepath.py` processed this actual completed capture; it joins
publication serials, checks completion/counts and retains all hitch milestones,
present calls and surrounding context boundaries. Raw traces, reviewed scenes,
`milestones.json/txt`, scheduler report and engine log:
`build/perf-codex/poco-frame-path-1/`. No tests added/run. No speedup claimed.

#### Guest address-space residency narrows the post-render delay

Engine `5511f5466d` adds default-off `LT_FRAME_PATH_CONTEXT=on`, alongside
`LT_FRAME_PATH`: changed AArch32 CONTEXTIDR/TTBR0 markers in a bounded
1,048,576-record buffer (about 40 MiB), with writes still deferred until Stop.
Linux/Android builds passed. This records the installed address space, including
kernel and native engine/driver work, not guest process identity or userspace CPU
time. Transitional register pairs remain separate.

One completed 60 s Snapdragon 865-class Poco Jetpack title capture, user recorder
off, array cache on, other experiments/full accounting/focused counters off;
scheduler trace only, no simpleperf/perfmaps. App `9437d59c`, engine
`5511f5466d`; frozen `frame-context.apk`, symbols and identity retained. APK
SHA-256 `dfeb042494283c7883f0d54be9f42a14007dd3cd60f8cbf52acd3eca4c766301`.
Diagnostic metrics: **53.200 fps, ceiling 58, median 54, low 48, 1% low 27.863,
11 hitches / 616.772 ms, zero skips**. These are not before/after improvement
measurements. Exact window `[446409644953923,446469644953923)` monotonic ns,
engine PID 16403, vCPU TID 16559. Both scenes reviewed, Start profile off;
clean Stop/no-qemu/baseline readback confirmed. **302,468 records, zero
dropped/incomplete/write errors**. Perfetto reports no error/data-loss counters,
complete scheduler coverage, clock-offset spread 52 ns.

All **3,248** explicit GL presents ran with CIDR **7**, TTBR0 **0xdb6e018**
installed. All eleven in-hitch framebuffer flips ran with CIDR **6**, TTBR0
**0xa802018** installed. A third address space, CIDR **5**, TTBR0 **0xa7ba018**,
accounts for substantial execution in the gaps. Joining the complete scheduler
timeline to address changes gives:

| Frame class | Intervals | Wall ms | vCPU running ms | Render-associated key 7 share of running | Key 5 share | Flip-associated key 6 share |
| --- | --- | --- | --- | --- | --- | --- |
| Normal (≤20 ms, no skips) | 2,789 | 46,410.748 | 37,198.426 | 66.68% | 15.45% | 16.46% |
| Late (>20 to ≤50 ms, no skips) | 391 | 12,971.459 | 10,612.942 | 68.11% | 16.13% | 14.25% |
| Hitch (>50 ms, no skips) | 11 | 616.772 | 491.525 | 48.10% | 35.37% | 12.92% |

Key 5 executes **173.848 ms** during the hitches, **15.804 ms per hitch**,
versus **2.060 ms per normal interval**. Its running-time share is about
**2.29×** the normal-frame share. This is an association within this capture,
not proof it causes the missed deadlines or a named system daemon.

The gap ending **44.798688 s** is a useful next target: latest completed
surface present → framebuffer flip is **24.268125 ms**, including
**20.673855 ms execution with key 5 installed**, versus 1.398073 ms under the
render-associated key and 1.644166 ms under the flip-associated key. The nearest
present is a temporal neighbor; its identity/dependency with the final scanout
frame remains unproven. Address-space identity must be established before calling
this audio, SpringBoard, logging or another subsystem. Host scheduling also
remains relevant: the worst **81.032 ms** gap contains **52.116 ms running /
24.734 ms runnable**. Maximum LCD deadline lateness in the eleven gaps is
0.997350 ms.

**Timestamp ordering correction:** sequence 4753's publication marker was
0.464583 ms later than its frontend observation (47.831607 s into the window).
The marker had been emitted after unlocking the frame-copy mutex, permitting
the frontend to copy before that marker was timestamped. Its preceding gap is
10.481250 ms, outside all eleven hitch boundary pairs. `framepath.py` rejects
this by default. The explicit `--allow-late-publication-markers` legacy option
preserves/reports the anomaly and still rejects an affected hitch boundary;
raw timestamps were not edited. Address/scheduler joins use frontend bounds and
are independent of that marker's timing. Do not interpret the older markers as
exact publication linearization times. Engine `c0347336e6` moves the marker
inside the existing copy lock before unlock. Linux/Android builds passed;
`frame-context-ordered.apk`, symbols and identity are frozen separately, with
no new phone capture of that fix yet.

Actual capture analysis: `framepath.py --residency-intervals`,
`hitchsched.py --all-states`, and `frameresidency.py`; full per-hitch execution,
runnable/sleeping residency and normal-frame comparison are retained in
`build/perf-codex/poco-frame-context-1/{milestones,hitches,residency}.{json,txt}`.
Next: map key 5 to a guest process, then locate its expensive work and the
render-to-flip dependency; separately investigate the host runnable delays.
Pixel untouched. No tests added/run, no new optimization or speedup claimed.

#### Process snapshots identify mediaserverd during the delayed flips

Engine `6d87d27dc8` adds default-off `LT_GUEST_GDB_PORT=PORT` to the embedded
entry. It starts the existing QEMU debugger on loopback only. Both Linux and
Android builds passed. A debugger attaches **after** the complete frame window,
reads the guest's proc/task/pmap list, then detaches before clean Stop. The
existing `qemu-ios/imgtools/lldb/xnu.py` derives offsets from the matching
kernel's accessors. No guest package update. This avoids assuming that ASIDs or
physical page tables identify the same task across different boots.

Two completed Snapdragon 865-class Poco Jetpack title captures, same frozen
`frame-guest-process.apk` / symbols / identity, app `96853da6`, engine
`6d87d27dc8`, clean at freeze. APK SHA-256
`75eafcac725ca0b402be89631c5a84aa6085ec6a06a0d8317c36d950232aea96`.
User recorder/full accounting/focused timing off, array cache on, other
experiments off, audio enabled. Both have scheduler/context milestones; only
capture 2 adds attached 1 kHz monotonic cpu-clock samples, FP call chains and
before/after JIT maps. These are diagnostic observations, **not an A/B speed
comparison**; the 52.68 → 55.07 fps difference is not a measured improvement.

| Capture | Mean fps | Ceiling | Median | Low | 1% low | Hitches >50 ms | Exact 60 s monotonic bounds | Engine / vCPU |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Process mapping | 52.683 | 58 | 52 | 48 | 28.047 | 9 / 473.829 ms | `[447464415631021,447524415631021)` | 19134 / 19293 |
| Mapping + samples | 55.067 | 58 | 56 | 50 | 28.539 | 5 / 257.218 ms | `[447765665621479,447825665621479)` | 20257 / 20415 |

Zero skipped publications in either window; before/final scenes reviewed as
the correct title with Start profile off. Clean Stop/no-qemu/baseline readback
confirmed, debugger forwards removed. **301,018 / 349,485 milestone records**,
zero drops/incomplete/write errors. No late publication markers: the ordered
marker fix now passes the phone capture's default ordering validation. All
3,206 / 3,344 explicit presents succeed and match the **jetpack** process's
page table. Hitch flips match **SpringBoard**. The recurring third task is
**mediaserverd, guest PID 14**, established from the same worker's process list.
The older capture's unnamed key 5 remains unnamed; this identification applies
to these new, directly mapped captures.

| Capture | mediaserverd installed key | Normal execution share | Hitch execution share | Its running time inside hitches |
| --- | --- | --- | --- | --- |
| Process mapping | CIDR 0x11 / TTBR0 0xafbc018 | 16.47% | 39.89% | 156.293 ms |
| Mapping + samples | CIDR 0x11 / TTBR0 0xb30c018 | 14.65% | 27.74% | 59.987 ms |

In capture 1's gap ending **35.683582 s**, latest completed present → flip
is **34.487917 ms**, including **25.612030 ms running under mediaserverd's
stable CIDR/TTBR0 pair**. Jetpack accounts for 5.220365 ms and SpringBoard 1.442188 ms
of that interval. This strengthens the audio-service lead; an installed pmap
still includes kernel/native execution, and the nearest present is not a proven
dependency or frame identity. Scheduler waits remain separate: all hitch gaps
contain **391.775 / 216.225 ms running**, **59.775 / 25.174 ms runnable** and
**22.279 / 15.819 ms sleeping**, respectively.

**Named samples:** local inputs were decrypted from the already-held matching
IPSW; kernel decompression Adler32 verified. `guestsymbols.py` reads 32-bit
Mach-O code-section symbols and dyld v1 cache mappings, deduplicates alias
images, uses each image's canonical LC_ID_DYLIB, and names indirect stubs
separately. These are nearest symbols for sampled TB starting PCs, not guest
stack unwinds or exact sampled guest instructions. No firmware was published;
temporary encrypted/decrypted rootfs images were removed after extracting the
symbol inputs.

Capture 2 records **79,613 process samples, zero lost**. Selected vCPU frame
classes contain **39,481 normal / 8,086 late / 217 hitch samples**. After
excluding samples within 10 µs of address-register changes into a separate
boundary group, mediaserverd's stable key has **5,612 normal / 61 hitch samples**.
Its stereo sample-rate converter
`SRC_convert_table_i16_scalar_stereo` receives **4.35% / 4.92%** of those
normal/hitch samples. Native `float32_add` receives **0.89% / 11.48%** (50 / 7
samples), `float32_mul` **1.28% / 4.92%** (72 / 3), `float32_sub` **0.37% /
3.28%** (21 / 2), and `soft_f32_mul` **0.36% / 3.28%** (20 / 2).
Guest `sleh_alignment` is **0.37% / 11.48%** (21 / 7). These small hitch
counts nominate paths for focused timing, not a sufficient verdict about a
single function or justification to repeat rejected VFP/alignment experiments.
Next: measure floating-point exceptional/zero paths and faulting guest PCs in
the audio work, while retaining sound and architectural exception semantics.

Perfetto reports no error/data-loss counters and complete selected-window state
coverage. Capture 2 has one **open initial R row**, timestamp
448067652582155, followed 6.562 µs later by a complete S state; both precede
the frame window by about 2.116 s. Default `hitchsched.py` correctly rejects
it. Explicit `--allow-superseded-initial-state` preserves the raw row and next
state in the report, excluding only that trace-initial row when its immediately
following complete state ends before the first frame. Window coverage/overlap
checks remain mandatory. No in-window missing intervals were filled. Clock
offset spread 105 / 0 ns; raw trace untouched.

Artifacts: `build/perf-codex/poco-frame-guest-process-1/` and
`poco-frame-guest-process-samples-1/`, with raw traces, process snapshots,
milestones/scheduler/residency summaries and capture 2's named `cpu.json/txt`.
`guestprocs.py` was used successfully in capture 2 and confirmed detach;
`hitchcpu.py` ran on the actual samples with exact frozen symbols/hash checks.
Pixel untouched. No tests added/run. The stable 60 fps target remains unmet.

#### Focused audio-path diagnostic

Engine `6743f6a81b` adds default-off `LT_AUDIO_PATH_PROF`, selecting sampled
original AArch32 f32 add/sub/mul helpers during translation. CPU 0 times one
in 1021 calls per operation and retains operands, result, pre/post FP flags,
rounding/flush settings and a read-only unwound instruction PC in the deferred
milestone buffer. It records each CPU 0 AArch32 alignment fault's instruction
PC/address/access/MMU index and retains architectural fault handling. Ordinary
arithmetic helpers remain unchanged with the switch off. No guest package update.
Engine `6b9bca678a` reserves 2,097,152 records (80 MiB) only with this diagnostic
enabled. Linux/Android clean final builds passed (`audio-path-build-final.txt`);
no tests added/run. Frozen `audio-path.apk` / matching symbols / identity:
app `27b2da6e`, engine `6b9bca678a`, both clean at freeze; APK SHA-256
`737170d7946004e59f3806450b181e810c2b2d54c1d8becda79bbe553706c84e`.

One completed 60 s Poco Jetpack title capture, recorder/full accounting/perfmaps
off, audio and array caching on, other experiments off. Exact monotonic window
`[448770150215315,448830150215315)`, engine 22846 / vCPU 23005. Same-worker
post-window debugger snapshot confirms guest PID 14 mediaserverd at CIDR 0xf /
TTBR0 0xac5d018. Both scenes reviewed, debugger detached, clean Stop/no-qemu/
baseline restoration confirmed; phone capture data removed after successful pull.

| Mean fps | Ceiling | Median | Low | 1% low | Hitches >50 ms | Skipped |
| --- | --- | --- | --- | --- | --- | --- |
| 52.150 | 56 | 52 | 46 | 20.050 | 30 / 1742.444 ms | 0 |

This is a focused diagnostic, **not** an A/B improvement, regression or overhead
measurement. Arithmetic counters and fault markers can perturb the long-frame
tail. 1,098,662 complete records, zero dropped/incomplete/write errors, zero late
publication markers. Perfetto has no error/loss counters, complete state
coverage, clock-offset spread 261 ns; no initial-state exception needed.
All 3,187 explicit presents succeed; max overlapping tick lateness 0.692109 ms.

**Conversion bursts:** all **227,042 alignment faults** in the window occur under
mediaserverd's pmap, all stores to addresses congruent to 2 mod 4 from EL0.
**223,494** are PC `0x303f4e2c`, with 887 each at `0x303f4e7c`, `0x303f4ec0`,
`0x303f4ef4`, `0x303f4f18`. Matching local cache symbols name all five as
AudioCodecs `convertScaledFloatToS16`. The main instruction is `0xe8a10300`,
`STMIA r1!, {r8,r9}`; local disassembly confirms the preceding packed-PCM steps.
This identifies the process/function for the previously known alignment-store
pattern. It does **not** justify repeating the rejected direct unaligned-store
bypass, which departed from ARM1176 fault behavior and lost throughput.

| Frame class | Frames | mediaserverd running | Share of class execution | Its alignment faults | Its sampled f32 calls |
| --- | --- | --- | --- | --- | --- |
| Normal <=20 ms | 2699 | 5113.735 ms | 14.37% | 26,719 | 18,233 |
| Late 20–50 ms | 399 | 1934.330 ms | 17.60% | 82,400 | 16,324 |
| Hitch >50 ms | 30 | 860.827 ms | 55.48% | 117,923 | 16,689 |

23 of 30 hitches contain alignment faults. Grouping faults separated by at most
10 ms yields **41 conversion bursts**, with median start-to-start spacing
**1494.644 ms**. This grouping is a diagnostic heuristic, not a guest audio
buffer boundary. Examples span 24–53 ms and contain roughly 4,000–7,000 faults;
span includes other execution and host waits, not just fault handling time.
The 97.788 ms hitch ending 10.299930 s contains 6,400 faults and 50.158 ms
running under mediaserverd. Its latest completed present → flip spans 61.152 ms,
including 46.920 ms running under that pmap. As before, proximity alone does
not establish the present's frame identity or a guest scheduler dependency.

**No exceptional-float spike found:** all 51,246 sampled mediaserverd operations
have zero/normal operands; none have NaN, infinity or subnormal inputs. Only
7 lack the sticky-inexact/RNE status eligibility check used by hardfloat;
all seven are multiplies, only one in a hitch. Normal → hitch timer-inclusive
means: add **178.26 → 184.90 ns**, sub **175.31 → 181.62 ns**, mul
**203.38 → 208.33 ns**. These compare frame classes, not optimization modes.
Largest in-hitch sampled call is 1.198 µs. PC unwind succeeds for every sample.
Hot audio instruction PCs name DCT-IV pre/post-modulation and libvDSP work.
The observed difference is concentrated batches of work; no sampled individual
arithmetic call explains a tens-of-ms hitch. Timers dominate such short calls,
so do not multiply these means by an inferred call count to estimate CPU cost.

All hitch gaps contain 1551.639 ms running, 132.456 ms runnable and 58.349 ms
sleeping. Audio is a recurring contributor, not a complete explanation for
every hitch. Next: confirm conversion-burst overlap with lighter fault-only
tracing, then measure the full architectural alignment-handler round trip and
the guest scheduling/IO reason for these batches. Preserve sound and faults;
do not pursue fresh-status/exceptional-float shortcuts on these observations.

Evidence: `build/perf-codex/poco-audio-path-1/` retains raw trace/milestones,
same-worker process snapshot, `audio.json/txt`, scheduler/residency reports,
`summary.json`, private converter disassembly and `audio-bursts.png` timeline.
`audio-path-run.py` and `audio-path-report.py` retain capture/report recipes.
`audiopath.py` processed the actual complete capture; no tests added/run, no
new optimization or speedup. Pixel untouched. Stable 60 fps remains unmet.

#### Lighter fault tracing and architectural return timing

Engine `c5cc5be3b9` adds default-off `LT_ALIGNMENT_PATH_PROF=on` for fault-only
markers with original arithmetic helpers and no FP counters. Optional `returns`
mode additionally brackets one in 257 CPU 0 EL0 ARM faults, matching privileged
thread register, TTBR0, CIDR and exact next instruction at exception return.
The original architectural handler/return runs. Unmatched/new-fault/overflow
cases are observable, and the pending table is bounded. Default/fault-only
generated exception-return code uses the original helper.
`alignmentlatency.py` joins matched intervals to the same scheduler timeline;
time excludes triggering access/unwind and includes waits/diagnostic work.
Clean final Linux/Android builds pass (`alignment-path-build-final.txt`). Frozen
`alignment-path.apk`: app `46d69408`, engine `c5cc5be3b9`, both clean at freeze;
APK SHA-256 `bee59aea573c6989821356203383e7b1b4369b1b78a4c0781546d237b3a2ed0c`.
No tests added/run or speedup claimed.

**Fault-only confirmation:** one completed 60 s Snapdragon 865-class Poco
Jetpack title capture, `LT_ALIGNMENT_PATH_PROF=on`, `LT_AUDIO_PATH_PROF=off`.
User recorder/full accounting/perfmaps off, audio and array caching on, other
experiments off. Exact window `[449606694435620,449666694435620)`, engine
25086 / vCPU 25243. Same-worker post-window debugger snapshot identifies
mediaserverd PID 14, CIDR 0x13 / TTBR0 0xb2ed018. Final scene reviewed as the
Jetpack title with Start profile off; debugger detached, clean Stop/no-qemu/
baseline restoration confirmed. Phone trace removed after pull.

| Mean fps | Ceiling | Median | Low | 1% low | Hitches >50 ms | Skipped |
| --- | --- | --- | --- | --- | --- | --- |
| 52.500 | 58 | 52 | 48 | 20.158 | 25 / 1448.322 ms | 0 |

708,421 complete milestone records, zero dropped/incomplete/write errors and
zero late publication markers. Perfetto has complete scheduler coverage and
no error/loss counters; clock-offset spread 52 ns. Default scheduler validation
passes. All 3,212 explicit presents succeed. This remains a diagnostic capture,
not an A/B improvement or instrumentation-overhead comparison.

All **227,047 faults** belong to mediaserverd's `convertScaledFloatToS16`:
223,499 at PC 0x303f4e2c, 887 each at the same four tail PCs as above. There
are **zero VFP samples/counters**. Faults overlap **22/25 hitches**. The same
10 ms grouping yields **41 bursts**, median start spacing **1497.628 ms**.

| Frame class | mediaserverd running | Share of class execution | Faults |
| --- | --- | --- | --- |
| Normal <=20 ms | 5227.913 ms | 14.58% | 31,339 |
| Late 20–50 ms | 1885.086 ms | 17.49% | 91,431 |
| Hitch >50 ms | 683.264 ms | 53.58% | 104,277 |

This reproduces the audio-conversion association without arithmetic counters.
All hitches contain 1275.308 ms running, 116.541 ms runnable and 56.473 ms
sleeping. The three fault-free hitches (50.988/50.223/51.702 ms) still contain
39.409/40.526/42.765 ms running and 7.052/8.129/5.730 ms runnable delay;
the conversion burst cannot explain every long frame. Next: sampled full
architectural handler timing, then guest scheduling/IO behind the periodic work.
Preserve original faults; the rejected unaligned-store bypass remains rejected.

Evidence: `build/perf-codex/poco-alignment-path-on-1/` retains raw events,
Perfetto, same-worker process snapshot, milestones/audio/scheduler/residency
reports, `summary.json` and reviewed `audio-bursts.png`. Capture/report recipes
are `alignment-path-run.py` and `alignment-path-report.py` beside it. Pixel
untouched. No new optimization or measured gain; stable 60 fps remains unmet.

**Architectural return timing:** a subsequent `LT_ALIGNMENT_PATH_PROF=returns`
capture uses that same frozen APK, with original arithmetic helpers/no VFP
counters. Window `[449908257382797,449968257382797)`, engine 26228 / vCPU
26384. Same-worker debugger snapshot identifies mediaserverd PID 14, CIDR 0xf /
TTBR0 0xa6c5018. Both scenes reviewed as Jetpack title/Start profile off;
debugger detached, clean Stop/no-qemu/baseline restored and phone trace removed
after pull. 736,590 complete records, zero dropped/incomplete/write errors,
zero late publication markers. Perfetto has complete coverage, no error/loss
counters and 157 ns clock-offset spread; default scheduler validation passes.

| Mean fps | Ceiling | Median | Low | 1% low | Hitches >50 ms | Skipped |
| --- | --- | --- | --- | --- | --- | --- |
| 51.117 | 56 | 52 | 46 | 20.057 | 28 / 1642.669 ms | 0 |

Diagnostic only: the two modes are not an interleaved optimization/overhead
comparison. All 235,490 faults are again in the audio converter. Faults overlap
23/28 hitches; 40 bursts (same grouping), median spacing 1509.049 ms. Media
normal/hitch execution share is 14.37/55.85%. All 3,122 explicit presents
succeed. Hitches total 1463.878 ms running, 122.479 ms runnable, 56.311 ms
sleeping; these distinguish guest/native work from host scheduling delays.

The return analyzer successfully processes the real capture. All **1,665**
sampled beginnings have matching returns: **916** wholly inside the window,
749 outside, zero abandoned, zero pending and zero cross-frame samples. All
window samples belong to the same mapped mediaserverd key. Duration begins
after fault-state restore and ends after the original architectural return;
it excludes the triggering access/unwind and includes instrumentation/waits.

| Frame class | Samples | Mean wall us | Median us | p99 us | Max us | Mean running us |
| --- | --- | --- | --- | --- | --- | --- |
| Normal | 88 | 3.201 | 3.125 | 3.750 | 9.115 | 3.201 |
| Late | 329 | 3.340 | 3.177 | 6.355 | 8.958 | 3.340 |
| Hitch | 499 | 3.406 | 3.178 | 5.625 | 40.990 | 3.370 |

The 40.990 us outlier includes 22.656 us running, 13.125 us runnable and
5.209 us sleeping. No sampled individual handler takes milliseconds. The
evidence points to many small operations concentrated into conversion batches,
plus separate host scheduling delay, rather than a rare very slow fault.
Systematic 1/257 sampling and diagnostic overhead limit extrapolation; these
numbers are not an estimate of attainable FPS gain. Next: guest audio buffer/
decode cadence and scheduling dependencies; preserve sound and ARM1176 faults.
Do not repeat the rejected unaligned-store bypass. Evidence under
`build/perf-codex/poco-alignment-path-returns-1/` includes matched and unmatched
data in `alignment-latency.json/txt` and the same raw/reports as fault-only.
No tests added/run, no optimization or speedup claimed. Pixel untouched.

An additional argument-only LLDB inspection on that frozen build attempted
breakpoints at the two converter entries. Neither produced a stop within the
45 s probe deadline; zero argument samples were obtained. The debugger detached
successfully and the runner confirmed clean Stop/no-qemu/baseline restoration.
This supplied no buffer-size/cadence evidence and no FPS measurement. Raw
attempt: `build/perf-codex/poco-audio-arguments-1/`; do not infer that the
converter stopped running from an unconfirmed breakpoint setup.

The next cadence probe extends the working sampled fault hook: return-mode
beginnings additionally record R0–R3, LR, SP and raw CIDR. No guest reads or
debugger are required. These are fault-time registers, not entry arguments.
`alignmentlatency.py` validates/retains paired snapshots and preserves old
captures with zero snapshots. Linux/Android builds pass on app `fb7d8823` /
engine `950a664438`. One completed 60 s Poco capture now confirms the register
hook: frozen `alignment-args.apk` SHA-256
`96441e68ac637aa834319e447b4b7b6e30b959051541ccf70e3691d016002bd0`, worker4253 /
vCPU4557. Same-worker post-window debugger mapping detached successfully and
identifies mediaserverd PID14, CIDR0x19 / TTBR0 0xb4c1018. Both scenes reviewed as
Jetpack title with Start profile off; MLA, full accounting and FP diagnostics off.
Clean Stop/no-qemu/baseline verified. Window `[451807950198739,451867950198739)`
host monotonic ns;741,558 records, zero drops/incomplete/late publication markers.
Scheduler validation passes with complete coverage and208ns clock-offset spread.

Diagnostic metrics: mean51.950fps, ceiling58, median52, low46,1%low20.049,
28 hitches/1570.464ms, zero skipped publications. This is not an optimization
or instrumentation-overhead A/B. All237,803 faults are again the audio converter;
41 bursts, median spacing1504.614ms,26/28 hitches overlap faults. Media normal/
hitch execution share14.70/53.60%. The hitch intervals contain1414.364ms running,
97.288ms runnable and58.812ms sleeping. No VFP samples/counters.

All1670 sampled handler beginnings/returns match (926 inside the window,
744 outside), with paired registers and no abandoned/pending/cross-frame samples.
All926 inside-window snapshots have LR0x303ddc60: matching local disassembly
confirms `ACMP4AACBaseDecoder::ProduceOutputPackets` called
`convertScaledFloatToS16`; they share thread0xe02c4220. R2 is always0x3f800000
(scale1.0); fault-time signed R3 ranges−4..1004, median480. This is a changing
loop remainder, not an entry buffer length. The caller sets that length from
decoder fields before the call, so measure actual entries next rather than
infer packet sizes from fault-time snapshots.

Normal/hitch handler medians are both3.281us; p99 12.865/8.594us. The largest
sample is1.760052ms and spans multiple installed address spaces:1.185730ms
running under the media pmap,0.502448ms elsewhere,0.045417ms runnable,
0.026457ms sleeping. Pmap residency includes kernel/other threads; this is not
proof that one fault's body became millisecond-scale. Guest thread scheduling
markers are needed to separate that interruption. Raw/reports:
`build/perf-codex/poco-alignment-args-1/`, including `callers.json/txt`,
`alignment-latency.json/txt`, complete frame/scheduler/process data and reviewed
scenes. No tests run or optimization gain claimed. The selected-PC probe below
will observe actual decode/convert entries and scheduler boundaries with both
fault and arithmetic tracing off.

#### Non-fused VFP multiply/add helper experiment (rejected)

`LT_VFP_MLA_FAST=on` (default off) combines each AArch32 single-precision
VMLA/VMLS/VNMLA/VNMLS into one generated helper call. It still invokes the
original `float32_mul` then `float32_add` using the same status object, sign
changes and NaN operand order. It does not use fused multiply/add or alter
rounding/exception handling. Half/double precision remain unchanged. Audio
arithmetic diagnostics or either zero-multiply switch suppress the experiment
so those paths keep their own selected helpers; the engine logs the effective
and requested modes. This targets helper boundaries in the AAC/libvDSP code
identified above, rather than exceptional operands or alignment bypass.

LT Bench `vfp-mla` times a long loop of eight independent scalar VFP2
accumulators across all four forms, with non-exact products to establish the
ordinary sticky inexact flag. No per-operation host clocks. Build and focused/
ordinary same-APK interleaved measurements pending; no gain claimed.
Initial and clean final Linux/Android builds passed; LT Bench's focused IPA
also built on the Mac (10 rounds, ONLY=vfp-mla). Frozen `vfp-mla.apk` / symbols
and build identity: app `5994bdfb`, engine `f235c93372`, both clean at freeze;
APK SHA-256 `bc7ced2656d9130350ec58dc65547fdb5ed17e03076326fa432093a41ae1e103`.
No tests added/run. Ordinary measurement setup keeps audio enabled and Start
profile/full accounting/fault/context/FP diagnostics off, array caching on,
other experiments off. The focused benchmark times whole loops.

Three interleaved/reversed Poco pairs completed (off/on, on/off, off/on), using
that same frozen APK. All focused benchmark and before/final game scenes were
reviewed: Jetpack title, Start profile off. Ten LT Bench rounds per arm, then a
settled 30 s game window on the same worker. No full accounting, perfmaps or
focused diagnostics. Audio enabled. All arms finished with clean Stop/no-qemu;
the runner restored and checked the baseline environment.

| Pair | LT Bench best / median ns, off → on | Mean fps | Ceiling | Median | Low | 1% low | Hitches >50 ms | Skipped |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 7.7 / 8.9 → 7.6 / 9.1 | 53.933 → 53.567 | 58 → 58 | 54 → 54 | 48 → 48 | 28.075 → 28.417 | 4 / 203.204 ms → 2 / 101.145 ms | 0 → 0 |
| 2 | 7.8 / 10.6 → 7.6 / 9.3 | 53.800 → 52.500 | 58 → 56 | 54 → 52 | 48 → 48 | 28.198 → 28.416 | 3 / 172.970 ms → 2 / 100.932 ms | 0 → 0 |
| 3 | 7.6 / 8.3 → 8.2 / 8.9 | 54.000 → 52.767 | 58 → 58 | 54 → 52 | 48 → 48 | 28.225 → 28.058 | 3 / 153.216 ms → 4 / 221.575 ms | 0 → 0 |

Mean **53.911 → 52.944 fps (−1.8%)**, slower in all three pairs. Neither focused
loop timing nor pacing shows a repeatable gain. Rejected and removed from the
engine in `3cc8fa6127`; the useful guest benchmark stays. Preserve the original
separate multiply/add helpers. No tests added/run. Raw evidence:
`build/perf-codex/poco-vfp-mla-{1,2,3}-{off,on}/`, `vfp-mla-results.json`,
`vfp-mla-run.py`, and `vfp-mla-rejected.patch`. Scene sheets:
`vfp-mla-reviewed-scenes-{1,2,3}.png`.

#### Selected guest instruction boundaries for scheduling/decode attribution

Engine `ab08a5d277` adds default-off `LT_GUEST_TRACE_PC`: up to32 comma-separated
ARM/Thumb instruction addresses, with bit0 selecting Thumb. Translation inserts
register snapshots only at those addresses; the default generated code contains
no probe. CPU0 records PC/thread/CIDR/TTBR0, R0–R3, LR/SP/R12 and processor mode
in three paired bounded frame-path events, without guest reads or breakpoints.
A reached-PC snapshot precedes the instruction's condition/execution; infer
function arguments and scheduling semantics only from matching disassembly.
`1422252ff7` gives this mode the same bounded80MiB trace capacity as the audio
diagnostic. `guestpoints.py` validates triplets/context/process identity and
compares observations across exact frame classes, retaining outside-window data.
Initial and clean final Linux/Android builds pass after removing the rejected
MLA experiment and adjusting capacity. No tests added/run. Runtime capture pending.

This is the next causal approach: observe actual guest scheduling transitions
and decoder boundaries, with fault/arithmetic logging off. Installed address
space alone cannot show whether a game/compositor thread is runnable or blocked.
Local kernel disassembly identifies the actual TPIDRPRW write in `Switch_context`;
a selected boundary immediately afterward can observe the scheduled thread
without changing the architectural register handler. Selected scheduler entries
can separately establish enqueue/block requests, subject to their actual code.
No new optimization, measured gain or established scheduler dependency yet.

The first real selected-PC capture (app `d89f3697`, engine `1422252ff7`,
frozen `guest-pc.apk` SHA-256
`0f0b127415680f454b555d28fff0f845c0785b1e3ddb7502b49b9d3c2157d306`)
selected decoder/convert entry and return boundaries, three boundaries after
TPIDRPRW writes, and kernel block/enqueue entries. It **overflowed**:2,097,152
records retained,705,852 dropped. Both analyzers reject it. Do not use the
partial selected-PC stream for hitch attribution or infer a scheduling cause.
The standalone complete frame log measured54.100fps,ceiling58,median54,low50,
1%low27.997,13 hitches/659.378ms,zero skips; this is neither an optimization
nor a diagnostic-overhead comparison. Raw evidence:
`build/perf-codex/poco-guest-pc-1/`. Debugger detached, clean Stop/no-qemu and
baseline restoration verified. Pixel untouched.

`d70057ad74` addresses the buffer limit with optional
`LT_GUEST_TRACE_DELAY_MS`: skip whole PC triplets before assigning IDs until
the specified delay from initial translation/selection setup (0–600000ms,
default0) expires. Frame/context logging stays active. This removes irrelevant
boot/settling PC records without increasing the bounded buffer or accepting
loss. Linux/Android builds pass; no tests run. The new frozen
`guest-pc-delayed.apk` is app `59d01bc3` / engine `d70057ad74`, SHA-256
`360faa03e887d4c21150cf24c2b0ebf0df3f9d8772f566ad03c79f38a79a15ef`.
The delayed capture completed on worker6912 / vCPU7069. Same-worker process
mapping detached cleanly; both scenes show the Jetpack title with Start profile
off. Fault/arithmetic/full accounting and other experiments stayed off. Clean
Stop/no-qemu/baseline restoration verified. Window
`[452358854486966,452418854486966)` host monotonic ns.1,433,952 records, zero
drops/incomplete/late publication markers. Scheduler validation passes with
complete coverage, no loss counters and156ns clock-offset spread. Paired PC
analysis retains234,612 observations inside complete frame intervals and142,173
outside; all triplets are complete. No missing scheduled-thread transition was
found when comparing every intervening observation against the three selected
post-TPIDRPRW-write boundaries.

Diagnostic metrics: mean51.850fps,ceiling56,median52,low46,1%low28.172,
9 hitches/488.681ms,zero skips. This is not an optimization or overhead A/B.
The complete stream now separates guest threads rather than assuming all
execution under the media pmap belongs to one decoder.

**Actual converter calls:**1,879 matched calls/returns in the60s window, all
from one media thread, each with entry length1024 scalar values and scale1.0.
No unmatched converter entry remains. Its scheduled execution includes guest
kernel/native work performed while that thread is selected, not just converter
instructions. Comparing calls by their actual entry output address:

| Output alignment | Calls | Median wall | Median selected-thread running | p99 selected-thread running | Total selected-thread running |
| --- | --- | --- | --- | --- | --- |
| 4-byte aligned | 1035 | 24.948us | 24.948us | 70.521us | 27.869ms |
| 2 mod4 | 844 | 423.073us | 423.073us | 707.553us | 387.910ms |

The roughly17× median difference is an observation within one instrumented
capture, not an optimization A/B. Matching code uses unaligned STM for the
second group; the original ARM1176 faults/guest handler remain intact. There
are80 decode/conversion batches (10ms grouping gap),40 containing unaligned
outputs; those40 have median spacing1506.323ms. The overall batch-spacing
median is741.379ms because the fault-only trace missed the aligned batches.
Do not repeat the rejected direct-store bypass: target the cost while preserving
faults and sound.

**Scheduling displacement is now observed:** the busy thread consistently
associated with Jetpack's pmap is0xe0791774; the decoder is0xe078eccc. Decoder-selected running time is1.285%
of normal-frame execution versus13.058% of hitch execution (453.907ms across
normal frames,52.758ms across hitches). Six of nine hitches overlap conversion;
three do not. Kernel `thread_setrun` observations for another, currently
nonselected thread can be matched to the later post-write scheduled boundary.
The three examples below are request-to-scheduled intervals overlapping the
listed frame; they can begin before that frame. They include enqueue/dispatch
processing and are not exact run-queue-residency timers. The request caller
resolves to the kernel's `thread_dispatch` in these examples, consistent with
requeueing a displaced game thread rather than waiting solely for an IPC reply.

| Hitch end | Frame gap | Jetpack enqueue request → scheduled | Decoder running inside that interval |
| --- | --- | --- | --- |
| 23.516s | 50.272ms | 26.952ms | 15.098ms |
| 26.500s | 51.198ms | 22.959ms | 14.993ms |
| 40.584s | 50.794ms | 29.150ms | 20.333ms |

**Other sources remain:** the67.403ms gap at2.933s and67.133ms gap at48.500s
contain no decoder execution/converter calls. They include respectively
9.055/5.643ms selected execution on a thread observed under launchd's pmap,
plus5.236/3.751ms on one observed under syslogd's pmap. These labels come from
installed-page-table observations, not a guest thread-owner enumeration; kernel
threads can borrow a pmap. Capture actual guest syslog messages and confirm
thread ownership next; the current labels alone do not prove logging or name
its source. The third decoder-free hitch at11.416s
includes8.620ms host runnable delay. Guest and host scheduling are separate
components; no universal single-cause claim is justified.

Raw/reports: `build/perf-codex/poco-guest-pc-delayed-1/`, including validated
`guest-points.json/txt`, `decode-scheduler.json/txt`, `decode-insights.json/txt`,
`ready-insights.json/txt`, frame/context/scheduler data, same-worker process
snapshot and reviewed scenes. Offline reproduction scripts are preserved as
`guest-pc-report.py`, `guest-pc-insights.py` and `guest-ready-insights.py` in
`build/perf-codex/`, with matching private disassembly inputs there. Next: a
focused benchmark/profile for the preserved alignment/return path, plus guest
syslog message capture for the separate logging bursts. No speedup claimed.

#### Exception-return duplicate hflags rebuild (focused measurements complete)

Static inspection found that `helper_cpsr_write_eret` calls `cpsr_write` with
`CPSRWriteExceptionReturn` and a valid mask that always includes `CPSR_M`.
That function already rebuilds hflags in TCG; the helper then aligns PC and
rebuilds the same flags again. PC is not an input to the hflags calculation.
Engine `9171922768` adds default-off `LT_ERET_HFLAGS_FAST=on`, skipping only
the second rebuild. Linux/Android builds and the focused Mac LT Bench build pass.
Architectural alignment faults, guest handler execution, CPSR/mode updates,
PC alignment and EL hooks remain intact. No measured game speedup yet.

LT Bench adds `stm-aligned` / `stm-unaligned`: the converter's exact
`STMIA r1!, {r8,r9}` store into pre-touched 2 KiB output, at 0/2 mod4.
Each round times 32,768 stores as one loop; byte/guard/writeback checks run
after timing. `ONLY` accepts a comma-separated exact list, enabling a
focused `syscall,fault,stm-aligned,stm-unaligned` build without GL work.
These measurements target preserved exception handling, not the rejected
unaligned-store bypass.

Two same-APK interleaved/reversed pairs on the Snapdragon 865-class Poco F3,
order off/on/on/off, five rounds per benchmark. Frozen app `bad04241` /
engine `9171922768`, `eret-hflags.apk` SHA-256
`99ee9fb09f38dc349ea1362c778ed9aed383bfc3238597c21f17acf150dccf00`.
User recorder, full accounting, fault/arithmetic/context/PC diagnostics and
other experiments off; array cache on. Four result screens reviewed. Every
store's byte/guard/writeback checks passed; all four passes completed. Clean
Stop, no stray qemu engine and default-off baseline restoration verified.

| Path | Pair 1 best / median ns/op, off → on | Pair 2 best / median ns/op, off → on |
| --- | --- | --- |
| syscall | 864.3 / 915.8 → 853.0 / 900.0 | 897.5 / 1045.3 → 871.5 / 898.4 |
| fault | 16336.1 / 16510.4 → 14237.2 / 15866.6 | 15692.6 / 16116.0 → 13702.7 / 16365.3 |
| stm-aligned | 6.5 / 6.6 → 6.5 / 6.7 | 6.6 / 6.7 → 6.6 / 8.7 |
| stm-unaligned | 1531.5 / 1555.7 → 1481.6 / 1510.2 | 1548.2 / 1580.8 → 1563.9 / 1611.2 |

Page-fault best times improve about13% in both pairs, but medians are mixed.
The targeted misaligned store improves in pair1 and loses in pair2: no
repeatable audio-path improvement established. Keep the switch default off.
These static benchmark screens have no game ceiling/median/low/1% low or
hitch count/durations; none is reported as an FPS gain or hitch fix. Ordinary
Jetpack comparison remains unmeasured. Raw evidence under
`build/perf-codex/poco-eret-hflags-{1,2}-{off,on}/`, frozen identity in
`eret-hflags-build-identity.json`, structured `eret-hflags-focused-results.json`
and reviewed `eret-hflags-focused-scenes.png`. No tests added/run.

#### Android guest syslog capture for decoder-free pauses

The Android app-services helper adds an explicit `syslog --seconds 1..600`
command through its existing private, owner-only USB socket. It starts the
guest's `com.apple.syslog_relay`, emits bounded chunks as lossless hex with
Android monotonic receive begin/end times, then closes the service and reports
counts/completion. The helper and Android APK build pass. It runs only when
invoked; ordinary benchmarks do not launch a relay. No tests added/run.

With the guest already running and `ANDROID_SERIAL` set:

```sh
python3 tools/perf/androidsyslog.py capture --seconds 60 --out build/syslog.jsonl
python3 tools/perf/androidsyslog.py analyse build/syslog.jsonl
```

The tool retains the original JSONL, a byte-exact `.raw`, readable `.txt`,
validated `.summary.json`, and `.identity.json` with the engine worker PID.
It rejects incomplete footer/count/time bounds and a changed engine worker.
Relay timestamps are arrival bounds, not guest message execution timestamps;
historical backlog and transport delay remain possible. Use message contents
to nominate sources, then verify execution/ownership for hitch attribution.
The first real Poco capture completed: frozen app `f47ab798` / engine
`9171922768`, `guest-syslog.apk` SHA-256
`88e7caa852d60e6150d180f78c4fa6e2df7becbb50717fbce836c9f4489e0656`.
Worker12896/vCPU13053; frame window
`[453873798205868,453933798205868)` monotonic ns. Relay completed70s,
34,632 bytes/25chunks/316 readable lines, with historical startup backlog.
1,474,148 frame-path records, zero drops/incomplete/late publication markers;
all PC triplets and thread-switch continuity validate. Perfetto covers the
complete window with no error/loss counters and156ns clock-offset spread.
Scenes reviewed as Jetpack title, Start profile off. Debugger attached only
after both captures, detached, clean Stop/no-qemu/baseline verified.

Diagnostic metrics: mean52.283fps, ceiling56, median52, low46,
1%low27.910,14 hitches/824.208ms,zero skips. Not an optimization or recorder
overhead comparison. Decoder execution overlaps8/14 hitches. Converter
medians remain25.364us aligned versus426.224us at2 mod4 (1,005/872 calls),
an observation within this capture, not a before/after speedup.

**A concrete logging source is now visible:** the supposedly quiet serial27
GL shim still writes `[mbxshim] present tally` every300 CA presents. Source
`contrib/it-gles/mbxshim.c` emits it through seven `w`/`wd` fragments, each
making a guest stderr write and a host log call. The relay shows those fragments
as separate Jetpack syslog messages. This differs from the previously fixed
per-surface reporting. Live tally messages arrive about1.04–1.15s after the
corresponding native present, so arrival/frame overlap would misattribute them.

The complete native stream has one context,6,093 presents and no failed native
returns. Using the source's cumulative counter to infer each tally's interval
between native present N's end and N+1's beginning gives three overlapping
hitches out of ten in-window tally candidates:

| CA present count | Post-present interval | Overlapping frame gap | Decoder running in frame |
| --- | --- | --- | --- |
| 2400 | 82.191ms | 81.825ms at5.618s | 8.610ms |
| 4200 | 57.842ms | 66.047ms at39.225s | 0 |
| 5100 | 60.586ms | 68.814ms at56.069s | 5.359ms |

The decoder-free39.225s gap includes9.155ms on a launchd-associated thread,
5.952ms and3.853ms on two syslogd-associated threads. These remain pmap
associations, not an owner enumeration. The post-present interval also includes
CA/other execution and host waits: do not label its full duration as logging.
Ordinal matching is inferred, not a direct log timestamp.

Reanalyzing the **existing** delayed-PC capture (no old commit remeasured),
both previously unexplained decoder-free67ms gaps overlap the inferred tally
intervals: counter2100 at2.839s (69.214ms interval;67.403ms frame gap),
counter4500 at48.412s (51.576ms interval;67.133ms frame gap). This strengthens
the periodic-tally lead; an ordinary same-APK A/B is still required to establish
the benefit of suppressing it.

Raw evidence/reports: `build/perf-codex/poco-guest-syslog-1/` including relay
JSONL/raw/text/identity/summary, validated frame/context/PC/scheduler/decoder
reports and reviewed scenes. Reproduction: `guest-pc-report.py`,
`guest-pc-insights.py`, `syslog-tally-report.py`; existing-capture ordinal report
`prior-tally-ordinal-insights.json`, all under `build/perf-codex/`.

#### Periodic success tally suppression (`LT_GLES_TALLY_QUIET`)

Engine `14cead9c5e` adds default-off `LT_GLES_TALLY_QUIET=on`, negotiating a
narrow log policy with the
guest shim. New guest tools serial28/version1.1.26 still count every CA result,
log the first outcome, and keep periodic logs if any failure has occurred.
Only all-success periodic tallies are skipped after an exact opt-in host reply;
older hosts retain the log. Rendering, CA calls and return values are unchanged.
Both A/B modes use the same guest package and APK; the policy query is common
to both and runs only at tally candidates. Optional `shim-tally` milestones
record the query before its stderr writes, removing the ordinal-timing inference
for future diagnostic captures. Linux/Android and Mac guest-package builds pass
(including the package build's standard packaging checks). Built guest
packages remain private; only source changes are pushed.

Ordinary measurements use frozen app `229acef1` / engine `14cead9c5e`, APK
SHA-256 `6c87420b2bec48426123721703fdb781a471421e321ae1aa020d83b345a4a555`,
and the same serial 28 package in both modes. The package was imported privately
through Android's Guest tools action on the stopped Poco; installed bytes and
record hash match the built package. Array caching stays on; exception-return,
indirect-L2 and other experiments stay off. No syslog relay, selected-PC/fault/
frame-path diagnostics, full accounting or user recorder. Minimal publication
logging (`LT_FRAME_LOG`) supplies metrics. Three 60 s pairs run off/on, on/off,
off/on on the Snapdragon 865-class Poco. Each window is 60 s:

| Pair | Mean fps off → on | Ceiling | Median | Low | 1% low | Hitches (>50 ms) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 52.07 → 54.17 | 58.00 → 58.00 | 52.00 → 54.00 | 44.00 → 48.00 | 21.02 → 28.43 | 18 (1325.7 ms) → 8 (439.4 ms) |
| 2 | 53.33 → 53.07 | 58.00 → 58.00 | 54.00 → 54.00 | 48.00 → 48.00 | 28.31 → 28.18 | 11 (558.9 ms) → 4 (220.4 ms) |
| 3 | 53.78 → 53.73 | 58.00 → 58.00 | 54.00 → 54.00 | 50.00 → 50.00 | 27.82 → 28.31 | 9 (488.6 ms) → 12 (637.7 ms) |

Aggregate mean **53.06 → 53.66 fps (+1.1%)**, hitches **38 → 24** and full
long-frame duration **2373.3 → 1297.5 ms per 180 s per mode**. These aggregate
changes are dominated by the first baseline's unusually long stalls. Mean fps
loses in two pairs; ceiling is 58 throughout. Pair 3's hitch count and duration
worsen. Pair 1's maximum 133.718 ms falls to 66.562 ms, but the other off runs
already peak at 53.363/67.642 ms versus 68.464/66.154 ms on. Many counted hitches
are close to the 50 ms threshold. **No repeatable throughput or general hitch
fix is established; keep the switch off.** The ordinal logging correlation
remains a lead, rather than a proven explanation for the entire large gap.
Next use exact pre-log `shim-tally` milestones and per-candidate intervals to
separate logging cost from coincident audio/scheduling work.

All 12 before/end screenshots reviewed: Jetpack title, Start profile off. All
six windows have zero skipped publications. Every boot retains `good 28`;
off logs 18 all-success tallies per run versus only the first tally on. Expected
mode banners verified, full accounting and focused diagnostics absent. Clean
Stop/no-qemu and baseline environment readback verified; suppression is off.
Pixel untouched. No tests added/run; builds and real-device measurements only.

The first on run's host runner exited 143 after the complete 60 s frame log and
chart were saved. Its full window was retained; the same still-running worker's
scene/log/mode/verdict were recovered before clean Stop. No partial timing
window was used. The remaining reversed/interleaved pairs completed normally.
Evidence: `build/perf-codex/poco-tally-quiet-{1,2,3}-{off,on}/`, frozen
`tally-quiet-build-identity.json`, exact bounds and every hitch end/duration in
`tally-quiet-results.json`; reproduce with `tally-quiet-analyze.py`.
Reviewed `tally-quiet-scenes.png` and `tally-quiet-frame-gaps.png` preserve all
arms, including pair 1's outlier baseline. Built guest packages remain private.

#### Exact tally timestamps without the syslog relay

The next diagnostic pair uses the same frozen APK/package as the ordinary
comparison above, with only sparse `LT_FRAME_PATH` milestones enabled. No
context/selected-PC/fault/FP tracing, syslog relay, accounting or user recorder.
New offline `tools/perf/tallypath.py` validates completion/counts, publication
coverage/order and adjacent native calls, then places each `shim-tally` marker
between the actual preceding native present end and following begin. It keeps
the exact marker, counter, surrounding interval and overlapping hitch bounds;
it does not infer marker time from the counter or relay arrival.

Both 60 s Poco Jetpack title captures complete, workers 21277/22220. The raw
streams contain 78,083/78,827 records, zero dropped/incomplete/write errors or
late publication markers. One native context; all tally failure counters zero.
Counter/native ordinals agree in this workload. The full frame windows are
`[456195418373524,456255418373524)` and
`[456399692288810,456459692288810)` host monotonic ns.

| Sparse diagnostic | Tally logs off → suppressed on |
| --- | --- |
| Mean / ceiling / median / low fps | 51.733 / 56 / 52 / 46 → 52.700 / 58 / 52 / 48 |
| 1% low fps | 28.027 → 28.220 |
| Hitches >50 ms / full duration | 11 / 554.659 ms → 11 / 586.507 ms |
| Skipped publications | 0 → 0 |
| Periodic candidates | 11 → 11 |
| Candidate post-present median / max | 18.847 / 62.136 ms → 16.416 / 28.868 ms |
| Other post-present intervals, count | 3142 → 3205 |
| Other post-present median / max | 16.396 / 65.049 ms → 16.327 / 60.341 ms |
| Candidate intervals overlapping a hitch | 1 → 0 |
| Actual tally markers inside a hitch | 0 → 0 |

These are diagnostic observations, **not an ordinary FPS improvement or a
matched overhead comparison**. One off candidate, counter 2400 at 8.633 s,
has a 62.136 ms native interval and 59.770 ms after the marker. Its interval
partly overlaps a 50.011 ms published gap ending 8.716 s, but the marker itself
precedes that gap's engine beginning by 32.167 ms. The whole interval includes
other guest work and host waits; do not call it 62 ms of logging. Most candidates
are ordinary, and all 11 hitches persist in the suppression minute. Direct
timestamps weaken logging as the dominant remaining source. Keep suppression
off; prioritize the separately observed audio conversion/fault path and host
scheduling work rather than repeat unchanged whole-game tally comparisons.

Both before/end scenes reviewed: title, Start profile off. Off emits 19 total
all-success tally lines through shutdown; on retains the first only. Each
clean Stop/no-qemu/baseline readback passes; Pixel untouched. No tests added/run.
Evidence: `build/perf-codex/poco-tally-path-1-{off,on}/`, including raw events,
frames, validated `milestones.json/txt`, `tallies.json/txt`, exact worker PIDs,
engine logs and `tally-path-scenes.png`. Reproduce each complete capture with
`framepath.py EVENTS FRAMES --json milestones.json` then
`tallypath.py EVENTS FRAMES --mode off|on --json tallies.json`.

#### Single-write tally logging (`LT_GLES_TALLY_BATCH`; limited comparison, keep off)

Engine `20857608bb` adds default-off `LT_GLES_TALLY_BATCH=on`. Guest tools
serial29/version1.1.27 recognize policy2 and format the same tally text into a
fixed stack buffer, then issue one stderr write and one host log call instead
of seven fragments. Counters, first/failure visibility, CA calls and present
returns remain. Quiet policy1 takes precedence; policy0/default/unrecognized
host replies keep fragments. Older guests treat2 as original unsuppressed
logging. This preserves logs, unlike the earlier suppression experiment.
Linux/Android and private Mac guest-package builds completed. Built packages
remain private. The stopped Poco's record hash and installed package bytes
match the serial29 build; the runtime verdict is `good 29`.

Frozen APK app `3ab7fb07` / engine `20857608bb`, SHA256
`60fe67e03c51e403477f0e8b567abbea86e453c4d72783c9f6cb0b10e03ac0c9`.
Both modes use that APK and serial29, BQL150us on, quiet suppression off,
nice0/CFS. User recording/full accounting/other experiments off. Focused runs
have only sparse frame-path/tally timestamps (no context, selected-PC, host
scheduler, fault/arithmetic trace or syslog relay). Their FPS is diagnostic,
not an ordinary improvement comparison. Normal candidate intervals include
all intervening guest execution and host waits, not logging alone.

First60s focused pair, off then on, workers31050/32197: same active vCPU TID
and nice0/CFS at each arm's boundaries, complete/lossless traces, expected
policy0→2, unchanged hook, serial29. Saved before/end scenes show Jetpack title
and Start profile off. Both arms stop cleanly before switching modes.

| Focused pair1 | Original fragments → single write |
| --- | --- |
| Periodic candidate intervals | 12 →12 |
| Candidate mean/median ms | 20.014219/16.330521 →15.845877/15.613438 |
| Candidate p95/max ms | 34.966562/43.835625 →18.122344/20.538125 |
| Candidates >40ms | 1 →0 |
| Other intervals: count | 3561 →3550 |
| Other intervals median/p95/max ms | 16.083281/19.807395/46.431822 →15.972344/20.002292/44.652448 |
| Other intervals >40ms | 3 →5 |
| Mean/ceiling/median/low fps (diagnostic) | 59.133333/60/60/58 →59.150000/60/60/56 |
| 1%low fps | 30.188537 →30.046196 |
| Hitches >50ms/full duration ms | 1/50.463906 →1/50.338542 |
| Skipped publications | 0 →0 |

The reversed focused pair (on first, workers1761/3862) is complete. Both saved
before/end scenes were reviewed as Jetpack title with Start profile off; the
same scheduling, identity, policy, serial and lossless-trace checks passed.

| Focused pair2 | Original fragments → single write |
| --- | --- |
| Periodic candidate intervals | 12 →12 |
| Candidate mean/median ms | 14.855937/15.407760 →15.482444/15.275312 |
| Candidate p95/max ms | 16.493750/16.787552 →17.667032/20.968958 |
| Candidates >40ms | 0 →0 |
| Other intervals: count | 3553 →3558 |
| Other intervals median/p95/max ms | 16.094114/19.894427/42.128229 →16.148073/19.889219/46.415000 |
| Other intervals >40ms | 3 →1 |
| Mean/ceiling/median/low fps (diagnostic) | 59.266667/62/60/56 →59.216667/62/60/56 |
| 1%low fps | 30.182700 →30.421143 |
| Hitches >50ms/full duration ms | 0/0 →1/50.497135 |
| Skipped publications | 0 →0 |

All tally texts/counters remain visible and show zero failed presents. The
first pair's candidate mean/tail reduction does not repeat in the reversed
pair. No repeatable local benefit, whole-game gain or hitch fix is established.
Across the two focused pairs only, diagnostic mean59.200000→59.183333fps,
hitches1→2 and full hitch time50.463906→100.835677ms per120s/mode.

**Ordinary pair completed before wrap-up:** one60s/mode on/off pair,
workers5524/6744, same frozen APK/serial29 and BQL150 configuration. All engine
diagnostics, full accounting and user recording off; only the minimal frame
publication log enabled. All four before/end scenes reviewed Jetpack title /
Start profile off. Same active vCPU TID/nice0/CFS at each arm's boundaries;
expected policies, good29 and unchanged/balanced BQL hook outcomes verified.

| Ordinary title pair1 | Original fragments → single write |
| --- | --- |
| Mean fps | 59.033333 →59.333333 |
| Ceiling / median / low fps | 60/60/56 →60/60/58 |
| 1%low fps | 29.981169 →30.511933 |
| Hitches >50ms / full duration ms | 1/50.461719 →0/0 |
| Longest gap ms | 50.461719 →35.343229 |
| Gaps >30ms | 56 →40 |
| Skipped publications | 0 →0 |

This pair favors batching by0.300fps (+0.51%), but a single pair does not
establish repeatability, and the focused reversal did not repeat the local
benefit. Keep the switch default off; no confirmed new gain or hitch fix.
At the user's wrap-up request, remaining ordinary pairs2/3 were canceled after
pair1 completed. SIGINT interrupted the next arm's process precheck, before
its launch/window; runner exit130 is intentional. Its finally cleanup completed:
clean Stop, no qemu processes, exact original debug-env restoration verified
again independently. Six arms total completed (four focused, two ordinary),
all twelve saved scenes reviewed. No comparison remains running.
Raw arms/tallies/frames/logs/identity under
`build/perf-codex/poco-tally-batch-focus-{1,2}-{off,on}/` and
`build/perf-codex/poco-tally-batch-game-1-{off,on}/`; frozen build identity:
`tally-batch-build-identity.json`. Reproduction runner/analysis:
`tally-batch-run.py`/`tally-batch-analyze.py`. Offline `tallypath.py --mode batch`
validates exact policy2 and preserves it in JSON. No tests added/run.

#### Wrap-up: cadence and earlier audio work (offline leads, no new phone run)

Fresh offline analysis of the four completed sparse tally captures finds
52/51/45/47 held ticks and50/50/44/45 frontend gaps>30ms for pair1off/on,
pair2off/on. Of adjacent held-tick spacings,17/51,22/50,23/44 and19/46 fall
between88 and92 refresh ticks (about1.5s). This recurring cadence deserves
comparison with guest producer submissions and audio/service work. It is not
proof of a clock mismatch, a timer defect or one periodic cause; the remaining
spacings and consecutive held ticks must also be explained.

`poco-tally-batch-focus-2-off/late30.json` retains44gaps>30ms,43 with two
refresh ticks and one with three; every interval has one pending framebuffer
base write. Surface presents and LCD writes are distinct milestones, so this
does not establish which presented image each refresh consumed.

Looking back one additional frontend interval in the existing
`poco-audio-queue-1/audio-stages.json`,48 of87gaps>30ms have selected-thread
AAC running somewhere in the preceding+current interval;42 exceed5ms and28
exceed10ms. Windows overlap and have variable length; these counts are not
independent events, exclusive audio totals, proof of causality or an A/B result.
AAC includes conversion. Both>50ms gaps still have no AAC in their previously
matched producer intervals. Follow exact producer/queue boundaries before
assigning a delay to audio. A next diagnostic should retain pre-hitch history
and distinguish the repeatable cadence from service/logging outliers.

Reproduce locally with `python3 build/perf-codex/wrapup-frame-leads.py`;
`wrapup-frame-leads.json` retains exact windows/counts, held indices, audio
look-back rows and input hashes. No new implementation or FPS improvement.
Resume priorities and device/build state: [PERFORMANCE-HANDOFF.md](PERFORMANCE-HANDOFF.md).

#### Focused profile of the preserved alignment-fault path

The long `stm-unaligned` LT Bench variant (1200 rounds, original ARM1176
fault/guest handler) completed with all byte/guard/writeback checks passing.
On the Snapdragon 865-class Poco F3, best/median were 1498.5/1542.0 ns per
store; the complete pass took 61,376 ms. This is one instrumented diagnostic,
not an optimization A/B. Same frozen `tally-quiet.apk` and serial28 package
as above; user recorder and other focused diagnostics off. Original short
LT Bench was restored, scene reviewed, clean Stop/no-qemu/baseline verified.

Attached simpleperf at 500 Hz for 20 s, DWARF 8192 with post-unwind: 10,966
samples, zero lost, 9,873 on the vCPU. Matching engine symbols and before/after
JIT maps resolve guest samples into `_sleh_alignment`, `_fleh_dataabt` and
exception return. Recorded call-chain inclusive shares include
`arm_rebuild_hflags` 12.70%, `cpu_restore_state` 6.77%, `tcg_tb_lookup` 5.89%
and `g_tree_lookup` 3.98%; `tb_tc_cmp` is 2.28% self. Incomplete native chains
mean these are nominations, not exact recoverable savings or game hitch time.
The direct timing experiment below checks the lookup cost before a game A/B.

Engine `c146d54fad` adds default-off `LT_HOST_PC_CACHE=on`: one exact
host-PC/result entry per TCG region, under the original region mutex. Insert,
remove and reset clear it before mutating the tree. All fault/state restoration
and guest handler behavior remain. Independent default-off
`LT_HOST_PC_CACHE_PROF=on` samples one lookup body per256 calls in five-second
blocks and counts candidate hits in either mode. Focused reversed A/B follows.
Game ceiling/median/low/1% low and hitches/durations are not applicable to this
static-screen diagnostic and have not been measured for this experiment.

Evidence: `build/perf-codex/poco-stm-profile-1/` with `perf.data`, `record.txt`,
`self.txt`, `callgraph.txt`, symbol cache, JIT maps, `guest-pc.json/txt`, engine
log and reviewed result scene. Runner `stm-profile-run.py`; private LT Bench
IPA SHA-256 `aa24eb583e041fa344404ee71232c1d94f25914d4b11d7807cc92bb21113efb9`.
Pixel untouched; no tests added/run.

**Focused lookup A/B:** final Linux/Android builds pass. Same frozen APK,
app source `779c5ed7` / engine `c146d54fad`, serial28 package, four1200-round
STM passes in off/on/on/off order. APK SHA-256
`4d81aec2a2209600e27cbb5d37e7aa38565f3df8225f7cfc5a75f865b5f21886`.
Lookup sampling enabled identically in both modes; user recorder, simpleperf,
frame/context/PC/fault/arithmetic diagnostics and accounting off. Every pass's
byte/guard/writeback checks passed; all four result scenes reviewed as LT Bench
with Start profile off. `hostpclookup.py` excludes the first region block after
pass start, conservatively removing any block that began before the pass.
All11 subsequent five-second blocks per arm are complete and retained.

| Focused timing | Pair1 off → on | Pair2 off → on (reversed order) |
| --- | --- | --- |
| Sampled lookup mean, ns | 379.793 → 288.749 | 353.489 → 287.615 |
| Median block mean, ns | 379.452 → 289.006 | 351.570 → 287.434 |
| Samples retained | 134226 → 139723 | 135996 → 134836 |
| Calls in retained blocks | 34361856 → 35769088 | 34814976 → 34518016 |
| Exact-address candidate hits | 99.99194% → 99.99186% | 99.99184% → 99.98439% |
| Largest sampled lookup, ns | 63698 → 22864 | 65573 → 24531 |
| STM best / median, ns/store | 1546.1 / 1582.1 → 1477.2 / 1522.1 | 1519.1 / 1558.0 → 1456.3 / 1535.3 |
| Whole1200-round pass, ms | 63007 → 60582 | 62181 → 62847 |

The sampled lookup body drops91.044/65.874ns (24.0/18.6%), including matched
clock overhead/descheduling and excluding mutex acquisition. STM best improves
4.46/4.13%, median3.79/1.46%; the full pass duration is mixed. This supports a
small local saving in the preserved architectural fault path. It does not
establish uninstrumented speed, ordinary game FPS or a hitch fix. Keep the
cache default off; next confirm short focused timing with lookup sampling off
before deciding whether a game comparison is justified. Maxima are systematic
samples, not all-call worst cases or evidence that these outliers were fixed.

Evidence: `build/perf-codex/poco-host-pc-{1,2}-{off,on}/` contains engine logs,
lookup reports/all blocks, mode/identity, scenes and process checks;
`host-pc-focused-results.json`, reviewed `host-pc-scenes.png`,
`host-pc-build-identity.json` and runner `host-pc-run.py` reproduce the result.
Original short LT Bench restored; final clean Stop/no-qemu and baseline
environment readback verified. Cache and lookup sampling are off. Pixel
untouched; no test suites added/run.

**Sampling-off confirmation and ordinary Jetpack:** the same frozen APK and
serial28 package completed three pairs in off/on, on/off, off/on order on the
Snapdragon 865-class Poco F3. Lookup sampling, user recorder, accounting and
other diagnostics stayed off; only the cache switch changed. Each arm ran
three short LT Bench passes (five rounds each), then the same Jetpack title
scene, settling for 50 s before a 60 s frame window. All byte/guard/writeback
checks passed. All 18 benchmark/before/end scenes were reviewed; Start profile
remained off. Zero skipped publications in all six windows.

| Ordinary Jetpack, off → on | Pair 1 | Pair 2 (reversed) | Pair 3 |
| --- | --- | --- | --- |
| Mean fps | 54.23 → 54.08 | 53.40 → 53.00 | 53.48 → 53.35 |
| Ceiling fps | 58 → 58 | 58 → 58 | 58 → 58 |
| Median fps | 54 → 54 | 54 → 54 | 54 → 54 |
| Low fps | 50 → 48 | 48 → 48 | 48 → 48 |
| 1% low fps | 28.154 → 28.466 | 28.443 → 28.067 | 28.311 → 27.786 |
| Hitches >50 ms | 14 → 6 | 6 → 10 | 9 → 15 |
| Full hitch duration, ms | 778.937 → 353.200 | 302.354 → 527.450 | 455.194 → 812.890 |
| Longest gap, ms | 68.506 → 67.861 | 51.080 → 66.725 | 51.932 → 75.756 |

Focused values below are the median of three pass bests / median of three pass
medians, in ns/op; they are not pooled round percentiles. Every pass is retained.

| Sampling off, off → on | Pair 1 | Pair 2 | Pair 3 |
| --- | --- | --- | --- |
| STM unaligned best / median | 1534.2 / 1576.6 → 1469.7 / 1522.0 | 1553.9 / 1577.4 → 1459.3 / 1486.5 | 1528.6 / 1542.2 → 1466.8 / 1520.2 |
| STM aligned best / median | 6.5 / 6.6 → 6.5 / 6.6 | 6.5 / 6.6 → 6.5 / 6.6 | 6.6 / 6.6 → 6.6 / 6.7 |
| Syscall best / median | 880.4 / 898.9 → 883.7 / 897.9 | 874.5 / 915.2 → 866.7 / 899.8 | 902.4 / 930.2 → 854.5 / 997.5 |
| Fault best / median | 15824.0 / 16609.1 → 16034.1 / 17361.5 | 16329.8 / 17961.4 → 17420.9 / 19209.7 | 17340.1 / 17906.7 → 20355.1 / 20638.1 |

Unaligned STM best improves 4.20/6.09/4.04%, median 3.46/5.76/1.43% with
lookup sampling off. This confirms a small local saving while retaining the
guest fault path. It does not establish a game benefit: aggregate Jetpack mean
53.706 → 53.478 fps (−0.42%), slightly lower in every pair; hitches 29 → 31,
full duration 1536.485 → 1693.540 ms per 180 s per mode. Pacing improves only
in pair 1 and worsens in pairs 2/3. **Keep default off; no repeatable Jetpack
throughput or hitch improvement. Do not repeat this unchanged experiment.**
This comparison does not include the later hflags specialization below.

Evidence: `build/perf-codex/poco-host-pc-ordinary-{1,2,3}-{off,on}/`,
`host-pc-ordinary-results.json` (exact windows, all passes and every hitch's
bounds/duration), reviewed `host-pc-ordinary-scenes.png`, runners
`host-pc-ordinary-run.py` / `host-pc-ordinary-analyze.py`. Clean Stop/no-qemu and
exact baseline restoration confirmed; original short LT Bench remains installed.
Pixel disconnected and untouched; no new test suites added/run.

For a visual check using the existing complete delayed-PC trace, local
`build/perf-codex/hitch-examples.py` produces `hitch-examples.png/json`.
The three examples (51.198ms with decoder displacement,50.057ms with host
runnable delay,67.133ms with no decoder) plot selected guest threads only
while the host vCPU runs, separate host waits, actual LCD ticks, and game
enqueue-request-to-scheduled intervals. They retain35ms before the gap:
some displacement starts before its first publication boundary. This is
offline analysis of existing data, not another measurement or a new cause
claim. The selected-thread association includes guest kernel/native work;
enqueue intervals are not exact run-queue timers. See PROFILING.md for the
approach. Pixel user-capture causes remain unproven by these Poco examples.

#### ARMv6 hflags specialization (focused and game comparisons complete)

The preserved-STM profile above nominates generic flag rebuilding, including
ARMv8 exception-level and control-register helpers. Engine `e414b9d74a` adds
default-off `LT_HFLAGS_V6_FAST=on`, calculating the same flag fields directly
for pre-v7 VMSA system emulation without EL2/EL3, M-profile, PMSA or AArch64.
Other configurations and unsupported current exception levels use the original
builder. ARM1176 on the iPod board explicitly disables EL3. The calculation
preserves CPACR traps, raw CPTR_EL3/PAN handling, alignment requirements,
endianness, NS and illegal-state flags; it does not skip exceptions or change
guest fault handling. Single-step-active requires an AArch64 debug target,
which the guarded CPU class lacks.

Independent default-off `LT_HFLAGS_V6_VERIFY=on` compares both flag words
against the generic builder at every eligible rebuild and reports every
1,048,576 comparisons. This diagnostic is for a separate runtime capture;
ordinary measurements keep it off. Linux/Android builds pass. Focused results
follow below; game ceiling/median/low/1%low/hitches/durations remain unmeasured.
The completed host-PC cache comparisons used their earlier frozen APK, not
this new code.

A separate 90 s Linux headless capture on a copy of an idle prepared n72ap/7E18
record completed with fast/verify on and host-PC cache off. At least 1,048,576
eligible rebuilds matched both original flag words, with no assertion failure.
The guest agent reports state 2 during the capture; final controller status
confirms state 0/clean, worker exit 0 and frontend return 0. The end screenshot
is asleep/black, so it supplies no active-UI visual evidence. These observations
cover the executed boot/runtime states, not all possible control-register values
or a performance result. No test suite added/run. The source record was idle
before the copy; the private scratch copy was removed after clean
Stop. Evidence retained as `build/perf-codex/hflags-v6-desktop{.txt,.png,-serial.log,
-record.json,-result.json}` and frozen build identity `hflags-v6-build-identity.json`.
Linux library SHA-256
`58f2831e691e324cc217f93e07c9a7bec5a75c7e942acdc54975f68d7dbc7e9f`;
APK SHA-256 `977785fcf61c8a6e32eefcbdaca0d9a383a43c2029643fefe3fe7ea0861b96d3`.

A separate Poco reference capture with that frozen APK also completed, fast
and verification on, host-PC cache and user recorder off. Worker 7849 executed
three short LT Bench passes, returned Home, then ran the Jetpack title for
60 s. At least **9,437,184 eligible rebuilds matched both original flag words**;
no assertion failure. All byte/guard/writeback checks passed, the benchmark,
Home and Jetpack scenes were reviewed with Start profile off, and clean Stop /
no-qemu were confirmed. Serial28 verdict remained good. This covers the states
executed in that capture, not every possible control-register value. Its
benchmark values include verification overhead and are not an optimization
timing result. Evidence: `build/perf-codex/poco-hflags-v6-reference/`, including
the completed engine log, mode/identity, benchmark passes, result and scenes.

**Focused timing, verification off:** the same frozen APK/package completed
two reversed pairs on the Snapdragon 865-class Poco F3, off/on/on/off. Each arm
ran three short LT Bench passes of five rounds. Host-PC caching, verification,
the user recorder, accounting and other diagnostics were off. All byte/guard/
writeback checks passed; four result scenes reviewed with Start profile off.
Values below are median of three pass bests / median of three pass medians,
in ns/op, not pooled round percentiles. All individual passes are retained.

| Focused timing, off → on | Pair 1 | Pair 2 (reversed) |
| --- | --- | --- |
| Syscall best / median | 867.8 / 946.2 → 763.9 / 787.7 | 856.2 / 872.4 → 775.5 / 840.6 |
| Fault best / median | 18033.5 / 18159.7 → 18653.2 / 19246.4 | 17539.0 / 18126.7 → 17545.6 / 18630.9 |
| STM aligned best / median | 6.6 / 6.6 → 6.5 / 6.6 | 6.6 / 6.6 → 6.5 / 6.6 |
| STM unaligned best / median | 1553.8 / 1578.9 → 1421.3 / 1460.2 | 1615.8 / 1733.8 → 1377.4 / 1419.6 |

Syscall best improves 11.97/9.43%, medians 16.75/3.65%. Preserved misaligned
STM best improves 8.53/14.75%, medians 7.52/18.12%. Aligned-store median stays
6.6 ns. Page-fault timing does not improve: best is 3.44/0.04% worse, medians
5.98/2.78% worse. Retain the first on pass's 24615.3/25856.5 ns page-fault
best/median; no pass was discarded. These are focused path results, not proof
of a Jetpack improvement. Game ceiling/median/low/1%low/hitch count/duration
are not applicable to the static result screens; ordinary game A/B follows.
Keep the fast calculation default off pending that comparison.

Evidence: `build/perf-codex/poco-hflags-v6-{1,2}-{off,on}/`,
`hflags-v6-focused-results.json` (all passes), reviewed
`hflags-v6-focused-scenes.png`, runner `hflags-v6-phone-run.py` and parser
`hflags-v6-phone-analyze.py`. Clean Stop/no-qemu/exact baseline restoration
confirmed; short LT Bench remains installed, serial28 verdict good. No tests
added/run. Pixel disconnected and untouched.

**Ordinary Jetpack comparison complete:** same frozen APK/package, three
60 s pairs in off/on, on/off, off/on order; title scene settled 50 s. Only
`LT_HFLAGS_V6_FAST` changed. Verification, host-PC caching, all diagnostics,
accounting and user recorder were off; client-array caching stayed on. All
12 before/end scenes reviewed as Jetpack title / Start profile off; zero
skipped publications. This uses engine `e414b9d74a`, not the later priority build.

| Ordinary Jetpack, off → on | Pair 1 | Pair 2 (reversed) | Pair 3 |
| --- | --- | --- | --- |
| Mean fps | 54.417 → 54.200 | 52.833 → 52.717 | 52.467 → 53.217 |
| Ceiling fps | 58 → 58 | 58 → 58 | 58 → 58 |
| Median fps | 54 → 54 | 52 → 52 | 52 → 54 |
| Low fps | 50 → 50 | 48 → 48 | 48 → 48 |
| 1% low fps | 28.234 → 28.430 | 28.206 → 28.432 | 28.026 → 27.912 |
| Hitches >50 ms | 6 → 6 | 8 → 8 | 10 → 10 |
| Full hitch duration, ms | 335.172 → 346.903 | 404.651 → 403.703 | 554.553 → 536.423 |
| Longest gap, ms | 67.645 → 68.849 | 51.405 → 51.028 | 68.569 → 66.054 |

Aggregate mean **53.239 → 53.378 fps (+0.26%)**, hitches **24 → 24**,
full duration **1294.377 → 1287.030 ms per 180 s per mode**. Throughput loses
two pairs; 1% low worsens in pair 3. The focused savings above do not deliver
a repeatable Jetpack throughput or hitch improvement. **Keep default off;
do not repeat unchanged.** Stable 60 fps and strong 1% lows remain unmet.

Evidence: `build/perf-codex/poco-hflags-v6-game-{1,2,3}-{off,on}/`,
`hflags-v6-game-results.json` (exact windows, identities and every hitch's
bounds/duration), reviewed `hflags-v6-game-scenes.png`, runner
`hflags-v6-game-run.py` and analyzer `hflags-v6-game-analyze.py`. Clean Stop /
no-qemu / exact baseline restoration confirmed. Pixel disconnected/untouched.

#### Destination CPU occupancy during runnable waits (existing trace analysis)

A different diagnostic question: while the host vCPU was runnable, was its
destination core executing another task or still running the idle task?
`hitchsched.py --runnable-details` now retains raw wake events, explicit wake
target versus next-running CPU, destination scheduler slices and CPU-idle events
for in-hitch runnable intervals of at least 1 ms (configurable). It reuses the
complete-state/clock/loss validation; missing destination/occupancy stays explicit.
This is offline analysis; no additional phone logging or ordinary FPS claim.

Applied to the existing complete `poco-guest-pc-delayed-1` trace (worker6912,
vCPU7069): **37 intervals / 55.582 ms** across nine hitches. In **36 intervals /
54.436 ms**, sched_waking targets CPU7, which runs only its idle task throughout
the interval; every one has an idle-exit event shortly before vCPU execution.
The remaining 1.146 ms is a preemption without a wake event, during frontend
`ReferenceQueueD` execution on CPU7. Destination coverage is complete, with no
unattributed occupancy. The decoder-free 50.057 ms hitch alone contains six
long wake intervals / 7.606 ms with CPU7 idle. An example 1.256 ms wait includes
a successful wake to CPU7, idle state1 entry and exit, then vCPU execution.

After the flags comparison stopped cleanly, read-only sysfs inspection reports
CPU7 state0 `C0` / `wfi`, latency43 us; state1 `C1` / `rail-pc`, latency1061 us.
The tracing clock is `boot`. This is consistent with costly idle/wakeup latency,
**not proof that hardware idle exit caused the entire delay**: IRQ/softirq/IPI
execution can occur while the idle task remains selected, and those events were
not enabled in the original trace. It also does not explain guest-decoder work
or establish the cause of Pixel hitches. Next capture those events together
with idle/wake/frame timestamps before selecting a mitigation. Priority is a
separate unmeasured candidate, not a demonstrated remedy for these idle waits.

Evidence: `poco-guest-pc-delayed-1/hitch-wake.json/txt` retains every selected
interval and raw destination slices/events. Existing capture metrics remain
mean51.850,ceiling56,median52,low46,1%low28.172,hitches9/488.681ms,zero skips;
these are diagnostic observations, not a before/after comparison. No tests added/run.

The same offline analysis of two earlier, less instrumented scheduler captures
also finds the pattern: `poco-hitch-sched-1` has20 selected long runnable
intervals/30.343ms and `poco-hitch-sched-2` has21/32.605ms, entirely with the
destination CPU idle task selected, zero competing-task/unknown occupancy.
Both retain the original complete scheduler/clock/loss checks; capture2 also
had CPU sampling enabled. These reuse existing captures, not new measurements
or evidence for a speedup. Reports: each directory's `hitch-wake.json/txt`.

**Interrupt-aware diagnostic completed:** `poco-hitch-wake-1`, worker19869 /
vCPU20029, frozen priority-build APK above (`vcpu-display.apk`, app7312beec /
engine6971278387), nice0/CFS verified before and after. Priority/flags/cache,
all engine diagnostics/accounting and Start profile off. The256MiB65s Perfetto
configuration captures scheduler, CPU-idle, IRQ, softirq and IPI events around
a60s Jetpack title frame window. Both scenes reviewed; clean Stop/no-qemu/exact
baseline restoration confirmed and own phone trace removed after pulling.

Trace validation: no error/data-loss counters, complete vCPU/destination
scheduler coverage, MONOTONIC alignment spread156ns. Diagnostic metrics:
mean54.183,ceiling58,median54,low50,1%low28.426;4hitches/203.097ms,zero skips.
Individual gaps:24.602s/51.252ms,32.101s/50.246ms,38.085s/50.560ms,
53.101s/51.040ms. These are **not an optimization or overhead comparison**.

Eleven selected in-hitch runnable intervals>=1ms total17.491ms. Ten are
entirely idle-task occupancy on the explicit wake targetCPU7; the remaining
interval includes1.276ms of another task and0.008ms idle. Total idle-task
occupancy16.215ms. Paired raw IRQ/softirq/IPI spans account for only
**0.013960ms inside that idle occupancy**, leaving16.201ms outside recorded
handlers. IRQ/IPI spans across all eleven waits total0.030885ms. The full
CPU7 interrupt streams have zero unmatched entry/exit records. Handler span
unions avoid double-counting nested activity. This largely rules out recorded
interrupt execution as the explanation for these selected long idle waits.

Example, relative to one sched_waking targeted atCPU7: successful sched_wakeup
at13.594us; CPU7 IPI entry/exit at1662.292/1663.438us; idle-exit event1663.958us;
vCPU runs at1668.594us. Combined with the reported1061us C1 exit latency and
three earlier traces, this strongly supports a host wake/idle-exit delay lead.
It does **not** identify the precise kernel/firmware mechanism or fix. Raw IPI
reason strings are empty and reported target masks are not decoded/validated;
no source-IPI-to-delivery attribution is claimed. Time without recorded
handlers is not proof of continuous hardware sleep. Guest audio-conversion
bursts and Pixel pauses remain separate questions. Next identify which native
waits lead into these wake delays before choosing a bounded mitigation.

Evidence: `poco-hitch-wake-1/` retains full83MiB trace, exact frames/window,
mode/build/thread identity, reviewed scenes, logs and `hitch-wake.json/txt`.
`wake-interrupts.json/txt` retains paired raw spans and selected wake intervals;
offline reproduction script `build/perf-codex/hitch-wake-interrupts.py`.
Runner `hitch-wake-run.py`, console log and cleanup marker retained.
First attempt aborted **before** any timed window because Android toybox
`ps NAME` returned the process name for every thread. Preserved under
`poco-hitch-wake-aborted-name-field`; clean Stop/baseline confirmed, no result
used. Corrected runner selects by `/proc/PID/task/TID/stat` comm and most CPU
ticks, retaining all candidate rows. Prepared priority runner corrected too;
priority performance comparison remains unstarted. Pixel disconnected/untouched.
No test suites added/run.

#### Completed native wait attribution (phone capture complete)

Engine `bd8af24c10` adds default-off `LT_NATIVE_WAIT_PROF=on`, requiring a
working `LT_FRAME_PATH`. It interposes existing BQL/ordinary mutex and condition
wait function pointers after trace initialization, timing only vCPU0 and
retaining completed calls>=10us. Off leaves the function pointers unchanged;
preexisting interposers cause refusal. Source site/object/mutex metadata and
completion counters are emitted only after vCPUs stop; each bounded trace
record stores actual call begin/end and site ID. The80MiB buffer is selected
without enabling guest context/instruction sampling. It preserves the original
wait calls, faults and audio behavior. Durations include scheduler delay and
condition-variable mutex reacquisition, not merely time holding a lock.

Linux/Android builds pass. Frozen `native-wait.apk`, app37637548 /
enginebd8af24c10, SHA256
`3b9c9cbf46f1f45c1f9023db8d0da6debbdc2ce848907579e4c2f4d6a20f2897`;
identity/symbols in `build/perf-codex/native-wait-build-identity.json`.
`nativewaits.py` validates completion, TID, sites and buffer counts, then matches
selected in-hitch runnable waits to enclosing completed native calls. Unmatched
intervals remain explicit. Direct driver/libc waits, recursive mutex entry and
direct QemuEvent futex waits are not covered. One phone attribution capture is
in progress; no runtime result, speedup or ordinary game ceiling/median/low/
1%low/hitch count/duration claim yet. No test suites added/run. User profiler
remains off; this diagnostic is separate from ordinary A/B measurements.

**Phone attribution complete:** worker22119/vCPU22278, frozen native-wait
APK/package, nice0/CFS confirmed at both boundaries. Native waits + sparse
frame milestones and scheduler/IRQ/idle trace on; all other diagnostics and
user recorder off. Both scenes reviewed as Jetpack title/Start profile off.
Clean Stop/no-qemu/exact baseline restoration confirmed; own phone trace and
milestone file removed after pulling. Serial28 verdict good.

Full sparse stream201,569 records, zero dropped/incomplete/late publications;
112,742 retained completed waits from5,707,671 calls, fifteen sites, no site
overflow and hooks unchanged. Complete scheduler coverage/no error or loss,
MONOTONIC alignment spread260ns. Diagnostic metrics: mean52.250,ceiling58,
median52,low46,1%low19.847;37hitches/2514.512ms,zero skips. This capture has
much more hitch time than the previous capture; modes/session differ, so it
supplies neither a speed comparison nor a measured diagnostic-overhead estimate.

| Completed native waits clipped to60s window | Calls>=10us | Wall ms | Running ms | Sleeping ms | Runnable ms (R+ excluded) |
| --- | --- | --- | --- | --- | --- |
| BQL acquisition | 31852 | 5630.710 | 292.056 | 788.306 | 4550.193 |
| Guest idle condition | 3070 | 3735.124 | 30.826 | 2056.199 | 1648.099 |
| Other QEMU mutex | 26 | 0.781 | 0.728 | 0 | 0.052 |

Of94 selected in-hitch runnable intervals>=1ms, **57/91.157ms** are enclosed
by BQL acquisition, **11/17.891ms** by the guest idle condition wait at
`qemu_process_cpu_events`, and **26/32.219ms** remain unmatched. BQL source sites
include `cpu_handle_interrupt` and MMIO dispatch, not one long guest GL call.
Among55 BQL matches with an immediately preceding sleeping state, the sleep
median is11.302us;38 sleep for under50us. The remaining two BQL matches do not
start exactly at a scheduler-state boundary, so no preceding-sleep statistic
is assigned. Condition calls include mutex reacquisition; unmatched intervals
can include direct library/driver waits outside the wrappers. Enclosure alone
is not a complete dependency proof, but repeated short BQL sleeps followed by
millisecond-scale idle wakes now justify a bounded BQL-acquisition experiment.

Further offline matching on the same complete capture places **all26 native-
wrapper-unmatched intervals/32.219ms inside host surface presents**, each
1.981–2.484ms long. `nativewaits.py` now retains enclosing present spans and
their context/target alongside native matches. The init log confirms the mapped
Android AHardwareBuffer path (reported generically as IOSurface). Its source
calls `glFinish` before `AHardwareBuffer_lock`; the existing outer present
markers do not distinguish those two operations. Do not call this an exact
GL-function attribution or a synchronization defect; pixel readiness must
still be preserved. It nominates stage-level present timing if BQL improvement
leaves these wake delays significant. No additional phone measurement.

Evidence: `build/perf-codex/poco-native-wait-1/`, retaining full trace, exact
frames, identities, engine/sites log, reviewed scenes, validated milestones,
`hitch-wake.json/txt`, `native-waits.json/txt` (all spans and raw input hashes)
and `native-waits-summary.json`. Runner `native-wait-run.py`; both analyzers
processed this real capture. No test suites added/run; Pixel untouched.

#### Android mapped-present wait boundaries (first capture pending)

The 26 wrapper-unmatched long runnable intervals above are enclosed by surface
presents, but existing acquire timing combines GPU completion and CPU mapping.
Engine `669353072a` adds default-off `LT_GLES_AHB_PATH=on`, requiring a configured
LT_FRAME_PATH. Begin/end pairs surround glFinish, AHardwareBuffer_describe,
AHardwareBuffer_lock and AHardwareBuffer_unlock, retaining buffer identity and
map/unmap results. Synchronization, copies and error behavior are preserved.
Disabled stages add no clock reads. Enabled producers only append to bounded
memory, with output after clean Stop; this mode reserves40MiB for milestones
(80MiB when native/audio/guest-PC tracing also requests it).

`tools/perf/ahbpath.py` rejects loss/incomplete spans and missing scheduler
coverage, retains input hashes, splits operation wall times into running /
sleeping / runnable, and matches long in-hitch runnable intervals to exact
enclosing operations. Per-hitch clipped stages remain in its report. It does
not attribute driver internals or infer a synchronization defect. Linux/Android
builds pass; first phone capture and analyzer exercise remain pending. No FPS,
ceiling/median/low/1%low/hitch count/duration improvement is claimed. User
recorder and this diagnostic remain off during ordinary BQL comparisons;
their frozen APK is unchanged. No test suites added/run.

**First stage capture complete:** Poco worker31360/vCPU31520, spin on, native
wait tracing off, AHB stages + sparse milestones/scheduler/IRQ/idle on; other
diagnostics/accounting/user recorder off. Frozen ahb-path.apk SHA256
`06a5e014e89f5c9a7ade979b07eea4fc7297e78eae5cecf82b145bce218832cd`, runtime
app66607fea / engine669353072a. Both title scenes reviewed with Start profile
off; same TID/nice0/CFS at both boundaries, good28 verdict. Clean Stop/no-qemu /
exact baseline restored; own phone trace/milestones removed after pulling.

Diagnostic metrics: mean58.167,ceiling60,median58,low54,1%low29.476;
one51.766ms hitch,zero skipped publications. These are not an A/B speedup or
diagnostic-overhead measurement. All141506 milestone records complete, no
drops/incomplete/write errors or late publications. All3527 in-window presents
and all map/unmap results succeed. Trace has no error/data loss and complete
scheduler coverage, clock spread104ns. The strict initial analysis rejects an
open first scheduler row; the explicit existing superseded-initial-state mode
accepts it only after verifying the next finite state ends before the frame
window. That row and exception remain in both scheduler reports. Initial plot
attempts lacked matplotlib in the default Python; final analyses/charts completed
successfully with /usr/bin/python3. No new device measurement for these retries.

| Stage,3527calls each | Wall ms | Running ms | Sleeping ms | Runnable ms (R; R+ separate) | Median us | p99 us | Max us |
| --- | --- | --- | --- | --- | --- | --- | --- |
| glFinish | 705.373 | 26.630 | 346.354 | 332.389 | 4.792 | 1890.365 | 2722.396 |
| AHardwareBuffer_describe | 7.344 | 7.290 | 0 | 0 (R+0.054) | 1.927 | 3.542 | 77.864 |
| AHardwareBuffer_lock | 542.473 | 284.558 | 81.579 | 176.307 (R+0.029) | 79.688 | 1874.844 | 4142.968 |
| AHardwareBuffer_unlock | 202.338 | 102.101 | 51.084 | 49.154 | 26.875 | 1640.990 | 3859.271 |

Whole-window vCPU states:47949.701ms running,5698.580ms sleeping,
6349.958ms runnable R,1.493ms preempted R+,0.268ms D. The marked stages account
for only557.851ms of R, despite their long tails. In the single >50ms gap,
all three selected>=1ms runnable intervals/4.917ms are outside surface presents.
For a separate diagnostic threshold of30ms,106 gaps contain202 selected long
runnable intervals:20/24.286ms within glFinish,6/9.219ms within mapping, and
176/279.002ms outside the entire surface present. No long interval in unlock
is selected within these gaps. This identifies some exact operations containing
waits; it does not prove a driver defect or explain most residual gaps.

Next attribute the outside-present waits while preserving spin. Engine
`77a6f34c02` adds `LT_NATIVE_WAIT_PROF=conditions`: only original condition waits
are interposed, both mutex function pointers stay intact and are checked at
cleanup. Full mode retains its refusal of preexisting interposers; off remains
unchanged. The existing80MiB trace capacity also applies to conditions mode.
Linux/Android builds pass; a separate conditions+AHB+spin capture has started,
user recorder off. No new optimization or performance gain claimed.

Evidence: `build/perf-codex/poco-ahb-path-1/` retains trace, exact frame window,
engine log/identity, reviewed scenes, completed milestones, hitch-50/30.json/txt
and charts, stages-50/30.json/txt (raw input hashes, all in-window stages and
per-gap clipping). `ahbpath.py` processed this real capture successfully.
Runner ahb-path-run.py, identity ahb-path-build-identity.json. No test suites
added/run; Pixel disconnected/untouched.

**Conditions-only attribution complete:** separate frozen condition-wait.apk,
app sourcee958b0f8 / engine77a6f34c02, SHA256
`43968d09fa14a87149780f460a8879b73b264ebcaafdc11be9363f49b5cf0ec4`,
worker1076/vCPU1544. Spin on, conditions+AHB+scheduler diagnostics on, other
diagnostics/accounting/user recorder off. Both title scenes reviewed; same
TID/nice0/CFS, good28 verdict, clean Stop/no-qemu/exact baseline restoration
confirmed. Own phone trace/milestones removed after pulling. All155752 records
complete/no drops/incomplete/late publications/write errors; all3531 in-window
presents succeed. Native wait13780 completed/retained calls, one site, no site
overflow, both native and BQL hooks unchanged. Trace has complete coverage,
no error/loss, clock spread52ns and no initial-state exception.

Diagnostic mean58.033,ceiling60,median58,low54,1%low29.022;
one51.741ms hitch,zero skips. No A/B or overhead claim. The only native site is
the guest idle condition at cpus.c:478. Its5071 in-window calls span8664.471ms:
67.633ms running,4855.758ms sleeping,3741.079ms runnable. Condition duration
includes mutex reacquisition. Whole-window vCPU is48097.611ms running,
5628.432ms S,6271.454ms R,1.015ms R+,1.488ms D.

Separate >30ms analysis finds115 gaps with238 selected>=1ms runnable intervals:
85/133.472ms inside the idle condition,41/48.746ms in glFinish,4/5.288ms in map,
3/3.393ms in unmap,105/184.173ms outside these traced calls. All85 condition
matches have immediately preceding S states: median198.334us,min0.834us,
max1043.437us;18under100us,68under250us,76under500us. The standard >50ms gap
has two condition intervals/3.366ms and two outside traced calls/3.434ms.
This establishes another source of delayed wakes while leaving substantial
unattributed waits; it does not prove all gaps share a cause.

Evidence: `build/perf-codex/poco-condition-wait-1/`, full raw inputs/identity,
reviewed scenes, milestones, hitch-50/30 and native-50/30 and stages-50/30
reports/charts, `summary.json/txt` with preceding-sleep samples and input hashes.
`condition-wait-summary.py` reproduces combined attribution; nativewaits.py and
ahbpath.py processed the real capture. No test suites added/run; Pixel untouched.

#### Blocking BQL fallbacks with spin active (capture complete)

The conditions+AHB capture above leaves105long runnable intervals/184.173ms
outside traced calls in >30ms gaps. Engine `32d6f75762` adds default-off
`LT_BQL_FALLBACK_PROF=on`, requiring BQL spin and active native conditions mode
with LT_FRAME_PATH. It times only the original blocking mutex call after the
50us spin budget expires, retaining its actual source site in the existing
bounded native wait recorder. The span excludes the preceding spin. No timers
are added to uncontended or spin-success acquisitions; off adds no clock reads.
Native and BQL hook pointers stay intact. The existing>=10us retention threshold
applies; native completion totals include both condition and fallback calls.
The deferred bql-fallback line separately counts all timed blocking calls and
confirms whether tracing was active. Refuse attribution if the trace/hooks or
completion counters fail validation.

Linux/Android builds pass. The idle-spin comparison completed separately with
its original frozen APK and was rejected below. This diagnostic changes neither
spin budget. No tests added/run.

**Combined capture complete:** Snapdragon 865-class Poco F3, Jetpack title,
worker12563/vCPU12721. Frozen bql-fallback.apk, app sourcec584e1fd /
engine32d6f75762, SHA256
`abfe277ea5897fb5ff449a58f148f17ddca6d57d0b272d3aa3b7be69c5e2ea09`.
BQL spin on, idle spin off; conditions/fallback/AHB stages plus sparse milestones
and scheduler/IRQ/idle trace on. Other diagnostics/accounting/user recorder off.
Both scenes reviewed as title/Start profile off, same TID/nice0/CFS at both
boundaries, good28 verdict. Clean Stop/no-qemu/exact baseline restoration
confirmed; own phone trace and milestone file removed after pulling.

All164832 milestones complete, no drops/incomplete/late publications/write
errors; all3489 in-window presents and map/unmap operations succeed. Native
waits21584 completed/21354 retained, eight sites, no site overflow, hooks
unchanged. Whole-worker BQL113902 contentions=106145 spin successes+7757
fallbacks; all7757 fallback calls timed with tracing active. These counts
include boot/settling, not only the60s frame window. Trace has complete coverage,
no error/data loss, MONOTONIC alignment spread104ns, no initial-state exception.

Diagnostic mean56.867,ceiling62,median58,low50,1%low28.371,zero skips;
three >50ms gaps totaling152.955ms:3.135s/50.046ms,14.251s/51.582ms,
53.251s/51.327ms. This is neither a speed comparison nor an overhead estimate.
Whole-window vCPU:48980.966ms running,4355.366ms S,6662.868ms R,
0.779ms R+,0.021ms D.

| Native waits clipped to60s | Calls>=10us | Wall ms | Running ms | Sleeping ms | Runnable R ms |
| --- | --- | --- | --- | --- | --- |
| Blocking BQL after50us spin | 2884 | 2647.332 | 37.600 | 286.281 | 2323.452 |
| Guest idle condition, including mutex reacquisition | 4503 | 6998.533 | 58.490 | 3379.732 | 3560.311 |

All ten selected>=1ms runnable intervals inside the three >50ms gaps are now
enclosed by traced native waits: four/6.465ms blocking BQL acquisitions at
`cpu_handle_interrupt` (cpu-exec.c:1331) and six/9.234ms guest idle conditions
(cpus.c:481). Every wake target runs its idle task throughout these intervals.
The BQL waits have preceding S states52.291–77.604us,median69.480us; conditions
2.344–205.833us,median166.328us. Enclosure identifies the waiting operation;
it does not establish the exact kernel/firmware wake mechanism or lock owner.

Separate >30ms analysis has180gaps/392selected runnable intervals:

| Enclosing operation | Intervals | Runnable ms |
| --- | --- | --- |
| Blocking BQL after spin | 170 | 283.959 |
| Guest idle condition | 178 | 274.262 |
| glFinish | 41 | 48.766 |
| AHardwareBuffer_lock | 2 | 3.001 |
| Outside traced calls | 1 | 2.586 |

Of170 BQL matches,151 are interrupt checks, nine MMIO loads, three MMIO stores,
seven timer register accesses.163 have exact preceding S states:median64.167us,
min1.823,max1051.719;36under50us,153under100us,158under250us. Seven matches do
not have an exact preceding-sleep boundary. Nearly all wake-target occupancy is
idle task; only0.504ms of BQL matched runnable time overlaps another task.
The single unmatched interval has no wake event/target, with the frontend
occupying its next-running CPU7 for2.586ms; retain that missing wake evidence.
The old105unassigned intervals are from a different capture, so do not claim
these are the same intervals or a measured reduction in unknown time.

**Remaining causal limit:** the three >50ms gaps contain129.017ms running
(84.35%),16.978ms runnable and6.960ms sleeping. Native wait enclosure accounts
for the long wake intervals, not the full hitch. Milestones show late engine
publication, with frontend lag0.237–0.566ms at the boundaries;2/1/3 host presents
occur within the gaps and their maximum calls are0.417/0.314/2.200ms. Different
presents, framebuffer flips and changed pixels remain distinct events. Follow
guest work and the next framebuffer flip across preceding frame intervals to
explain the execution component. A larger BQL spin budget is now a supported
candidate (many sleeps are under100us after the existing50us spin), not an
established improvement; ordinary interleaved comparisons would still be needed.
The rejected idle-spin result must not be overridden by condition attribution.

Evidence: `build/perf-codex/poco-bql-fallback-1/`, retaining identity, full trace,
exact frames, engine/sites log, reviewed scenes, hitch/native/stages/milestones
reports at50ms and separately30ms, `summary.json/txt` with source counts,
every preceding-sleep sample, wake-target occupancy and raw input hashes.
Runner bql-fallback-run.py; bql-fallback-summary.py reproduces combined results.
All four real analyzers completed successfully. No tests added/run; Pixel
disconnected/untouched. Its214.5ms gameplay gap still lacks a matching scheduler
trace and is not explained by these Poco title results. No new performance gain.

#### Guest frame cycles with BQL spin (capture complete)

A new selected-thread capture follows the execution component with the proven
50 µs BQL spin on and rejected idle spin off. Frozen bql-fallback.apk above
(app source `c584e1fd`, engine `32d6f75762`) uses the existing eight selected
instruction boundaries, delayed 80 s, context changes and scheduler/IRQ/idle
trace. Native wait/AHB/fault/arithmetic diagnostics, accounting and user recorder
are off. Worker14580/vCPU14750; both scenes reviewed as Jetpack title/Start
profile off, same TID/nice0/CFS at both boundaries, serial28 good. Same-worker
process snapshot taken after the timed window, detached before clean Stop.
No-qemu/exact baseline restoration verified; own phone trace/milestones removed.

All 1,687,113 milestone records complete with no loss/incomplete/late markers
or write errors. All 447,102 PC triplets validate (250,227 in complete frame
intervals, 196,875 outside); selected-thread transition continuity passes.
Scheduler coverage is complete, no error/data loss, clock spread104ns and no
initial-state exception. Diagnostic mean55.850,ceiling60,median58,low46,
1%low28.350,16hitches/1285.455ms,zero skips. This is not an A/B speedup or a
measurement of tracing overhead. The ordinary extended-budget comparison uses
its separate frozen APK with all these diagnostics off.

Joining actual guest framebuffer-flip cycles with selected threads changes the
next lead. The same thread0xe056aeec performs every in-window GL present, and
thread0xe0313220 performs all3351 framebuffer flips. Those identify execution
roles, not task owners. Four threads observed only with BTServer/BlueTool page
tables installed contribute300.620ms/0.688% of normal-frame execution but
314.942ms/26.996% of hitch execution. The converter thread contributes only
70.312ms/6.027% of hitch execution in this capture. This does not contradict
earlier audio-heavy examples or establish Bluetooth as a universal cause.

| Hitch end s | Gap ms | vCPU running ms | Leading selected thread / observed pmap | Its running ms | Presenting thread enqueue request → scheduled ms |
| --- | --- | --- | --- | --- | --- |
| 13.634 | 130.976 | 123.426 | 0xe02c3554 / BTServer | 48.360 | 92.119 |
| 35.351 | 115.335 | 107.427 | 0xe02c3554 / BTServer | 39.445 | 51.265 |
| 57.751 | 114.511 | 106.473 | 0xe0332eec / BTServer | 26.265 | 77.316 |
| 24.853 | 102.448 | 86.327 | 0xe0332664 / BlueTool | 24.995 | 52.509 |

The associated flip-to-flip cycles are129.105/114.734/116.063/101.974ms.
The report retains the preceding two publication intervals, selected threads
through each cycle, neighboring presents and each game enqueue-to-scheduled
interval. Enqueue observations precede dispatch and are not exact run-queue
residence; temporal neighboring presents are not proof of frame identity.
All16 hitches together contain1166.621ms running, so neither a single GL wait
nor host wake delay accounts for their full duration.

The matching post-window snapshot has BTServer PID82 and BlueTool PID83 still
present. Page-table reuse, process/thread exit/reuse and borrowed pmaps limit
owner inference: these are explicitly pmap observations, not confirmed owners
or a proved restart mechanism. Earlier retained syslog from a different worker
contains a Bluetooth boot-script hang/relaunch followed by successful bring-up;
that old backlog does not establish the state of this new capture. Next obtain
fresh guest logs and actual thread/task ownership for a repeat of this behavior,
then inspect the controller protocol if bring-up retries are confirmed.

The original converter behavior also persists:1879 matched calls of1024 scalar
values/scale1.0, aligned versus2-mod4 median selected-thread execution23.489
versus434.583us (1048/831 calls). These are within-capture observations, not a
new optimization; preserve the architectural alignment faults and sound.

Evidence: `build/perf-codex/poco-guest-spin-1/`, with raw inputs and hashes,
snapshot/detach log, reviewed scenes, hitches/milestones/guest-points reports,
decode-scheduler/insights and guest-flips.json/txt (all cycles and request
intervals), plus summary.json. Runner guest-spin-run.py, existing guest-PC
report scripts and new guest-flip-report.py retain reproduction. All real
analyzers passed. No tests added/run; Pixel untouched. No new performance gain.

#### Bluetooth owner capture (complete; retry pattern not reproduced)

Fresh same-worker capture `poco-guest-bluetooth-1/` uses the frozen extended APK
documented below, original50us BQL spin on/extended off, six selected guest PCs plus
context/scheduler/IRQ/idle tracing, and a concurrent65s guest syslog relay.
Idle spin, native/AHB/fault/arithmetic diagnostics, accounting and user recorder
are off. Worker23185/vCPU23341, sameTID/nice0/CFS, good28; both scenes reviewed
as Jetpack title/Startprofileoff. Clean Stop/no-qemu/exact baseline restored;
own phone trace/milestone files removed after pull. Runner exits0.

App `49c2d2bd` optional task-thread enumeration is exercised successfully:
matching-kernel pinned layout0x28,0x30,0x1c4,19tasks/139live threads, all139
direct thread/task/BSD-process matches, current-thread membership confirmed,
same-worker debugger detached after timing. Ownership is observed after the
window; earlier thread/task lifetime and pointer reuse remain unproven.
All1,136,449 milestones complete with no loss/incomplete/writeerror;267,476
valid PC triplets,157,968 in complete frame intervals, switch continuity passes.
Scheduler has full coverage/no error/loss/initial-state exception, clock
spread677ns. All seven real analyzers complete; no tests added/run.

Diagnostic mean55.850,ceiling60,median56,low50,1%low28.454,6hitches/330.616ms,
zero skips. This is neither an A/B gain nor an estimate of tracing overhead.
Hitch durations:52.987,65.413,50.875,50.128,50.252,60.960ms. The earlier
100–131ms periodic pattern is not reproduced. Relay preserves33,682bytes/
30chunks over65s including boot backlog: BTServerPID26 finishes initialization,
enters its message loop, and remainsPID26 in the live snapshot. No boot-script
timeout/relaunch appears in the retained text; BlueTool has exited. The two
live BTServer-owned threads have no observed scheduled execution in the timed
window. Do not transfer this healthy boot's owners to the older failing run.

The six hitches contain230.193ms running,16.485msR,0.089msR+,8.471msS and
75.377msD. At4.303/4.369/48.408s, gaps52.987/65.413/60.960ms include
32.632/11.283/29.164ms uninterruptible sleep. All selected>=1msD spans are
outside host surface presents; the trace has no blocked-function attribution.
This does not identify storage/page faults or establish diagnostic overhead.
The11.444/18.361s gaps include14.619/9.861ms of converter-thread execution.
1857converter calls still handle1024scalars/scale1.0: aligned versus2mod4
median selected-thread running26.042 versus473.178us (1053/804calls).

Next evidence: time NAND load/store/erase and nested fsync calls directly.
Source inspection finds synchronous file persistence in FMSS MMIO handling;
it is a candidate for D-state waits, not a confirmed cause. Engine `bb5132a264`
adds default-off `LT_FMSS_PATH=on` boundaries in the bounded deferred frame
trace. It preserves each fsync/flush/rename and error path, including errno
across the fsync marker. Linux/Android builds and a real capture are complete
as documented in the next section.
No new performance gain from this diagnostic. Keep the Bluetooth lead open
for fresh same-worker logs/owners when the periodic failure recurs.

Evidence retains raw traces/frames/logs/identity, exact windows and input hashes,
process enumeration/detach log, reviewed scenes, syslog raw/JSONL/text/receive
bounds, hitches/milestones/guest-points, decode/flip/owner reports and summary.
Scripts guest-bluetooth-run.py/analyze.py/summary.py and guest-owner-report.py
are in build/perf-codex. Pixel untouched and its largest hitch still unresolved.

#### NAND call attribution (capture complete; long sleeps not reproduced)

Engine `bb5132a264`, app source `642a6ae9`; Linux/Android builds pass. Frozen
fmss-path.apk SHA256
`b2e1c3c250d82dfbc4a9fd7f01c1731a9561f224f15ca5a90c062883328509b3`,
build identity and matching libraries retained. New60s capture
`poco-fmss-path-1/`, worker25549/vCPU25706, uses150us BQL spin, NAND/sparse
milestones and scheduler/frequency tracing. Guest PC/context/native/AHB/idle
spin/accounting/user recorder are off. Guest syslog is captured for3s only
AFTER the timed window, followed by clean Stop. Both scenes reviewed as title/
Startprofileoff, sameTID/nice0/CFS, good28; Stop/no-qemu/exact baseline verified.
Runner exits0; own phone trace/milestone files removed after pull.

160,596 complete milestone records, no loss/incomplete/writeerror or late
publication markers. All3556host presents succeed. Scheduler coverage complete,
no error/loss/initial exception, clockspread103ns. All34,194 NAND call IDs and
coordinates validate, nested calls balanced. No erase calls occur. Three real
analyzers complete, including new `tools/perf/fmsspath.py`; no tests added/run.

Diagnostic mean59.050,ceiling60,median60,low58,1%low30.026,0hitches/0ms,
zero skips; longest gap49.880ms. This is not A/B or tracing-overhead evidence.
The full window contains50057.345ms running,5339.017msS,4603.396msR,
0.243msR+ and **noD**. Just32NAND loads occur in the window, median9.349us,
max189.010us,528.431us total, all running; **no stores or fsyncs occur**.
Therefore this capture does not reproduce or attribute the older long sleeps.

The complete trace does exercise persistence outside the frame window:
before timing,33249loads/347stores/347nestedfsyncs, maxima43.522/28.834/
28.290ms; afterward179loads/20stores/20fsyncs, maxima0.149/27.888/27.467ms.
Those times belong to boot and post-capture activity. The after-window relay's
receive interval starts8.545s/ends11.546s after the frame window; fsyncs occur
at7.632–17.765s after it. This includes connection and Stop activity and does
not prove which guest request triggered each write. Never count these as game
hitches or add nested fsync time to its parent store. The retained log reports
successful Bluetooth startup with BTServer26, no retained timeout/relaunch.

Storage can block the vCPU, but there is no demonstrated ordinary-game benefit
from changing it here. Preserve persistence semantics; the prior75.377msD
remains unattributed. Investigate a reproduced ordinary hitch before choosing
an asynchronous storage change. Stable60/clean1%low remains unmet.

Evidence: frozen identity/libs, rawframes/trace/milestones/engine/syslog,
reviewed scenes, hitches/milestones/nand reports, summary.json with hashes,
all outside-window NAND spans and cleanup marker. Reproduce with
fmss-path-run.py/analyze.py in build/perf-codex; fmsspath.py validates pairing,
splits inclusive/exclusive host states and matches hitchD to enclosing calls.
Pixel untouched. No new optimization or measured gain from this diagnostic.

#### Residual 30–50 ms gaps (offline analysis of the NAND capture)

Reanalyzed the same `poco-fmss-path-1` data at a 30 ms threshold; no new
phone run or optimization comparison. The standard >50 ms hitch count remains
zero. There are 54 intervals over 30 ms, median 33.522 ms, maximum 49.880 ms.
Each engine publication interval contains exactly one LCD framebuffer-register
flip: 53 contain two vsync ticks with the flip after the first; one contains
three ticks with the flip after the second. These observations locate a missed
scanout opportunity before the next flip; they do not identify which game
surface or guest dependency delayed that flip.

Across the corresponding frontend intervals, the vCPU spends 1605.565 ms running
(87.23%), 118.019 ms runnable and 116.949 ms sleeping, with no uninterruptible sleep.
Engine-publication-to-frontend lag at their ends is median 0.500 ms, maximum 5.207 ms.
The largest overlapping individual host present is 4.162 ms; the median of those
per-interval maxima is 1.644 ms. Maximum vsync lateness per interval is
median 0.052 ms, maximum 8.779 ms. These stages can contribute, but no single long
host present or frontend handoff explains all 54 intervals. Whole-interval CPU
shares are not a critical-path attribution and can include unrelated guest work.

The 49.880 ms interval ending 27.251 s contains 43.442 ms vCPU running, 4.706 ms
runnable and 1.733 ms sleeping. Two vsyncs occur before the sole flip, 33.477 ms
after the earlier publication; both overlapping host presents finish in under
0.4ms. Follow the guest work preceding that flip rather than adding broad GL
totals. The 42.945 ms interval ending 18.478 s differs: 12.962 ms sleeping and a
vsync 8.779 ms late. Preserve those distinctions when selecting the next probe.

Evidence: `hitches-30.json`, `milestones-30.json` and `residual-summary.json`
under the same capture directory, joined by exact frame boundaries using
`build/perf-codex/residual-frame-summary.py`; input hashes retained. Milestone
counts use engine publication boundaries, scheduler totals frontend boundaries.
No frame identity is inferred from adjacent presents. No tests or phone activity.
Pixel remains disconnected; its 214 ms gameplay hitch still needs a matching
scheduler trace and native samples, and cannot be explained from these Poco
observations. Ordinary benchmarks keep all diagnostic/user recording off.

#### Physical surface identity (capture complete; handoff narrowed, cause still open)

Engine `2898270eb9` adds default-off `LT_GLES_SURFACE_PATH=on` alongside
`LT_FRAME_PATH`. Before each present, debug-translate every page of its bounded
destination span without faulting pages in. Record a physical base only for a
fully contiguous mapping; failed presents/unknown mappings cannot establish a
write association. Rendering, guest faults, synchronization and scanout are
unchanged. Linux/Android builds pass. Frozen surface-map.apk SHA256:
`9fe509fa0741744c8f39bbef83eba12ea8079d77e3dd2539184b8234c5afc589`,
app source `d258fa46`, matching libraries/identity retained in build/perf-codex.

New60s `poco-surface-map-1/` uses150us BQL spin, sparse surface milestones and
host scheduler/frequency tracing. User recorder, accounting, guestPC/context,
native/AHB/NAND/RAM-pointer diagnostics and idle spin are off. **No guest syslog
relay**, including after timing. Both scenes reviewed as Jetpack title/
Startprofileoff, worker3599/vCPU3910 unchanged/nice0/CFS, good28. Clean Stop,
no remaining qemu and exact debug-env restoration verified; own remote trace
and milestone files removed after pull. Runner exits0.

102,204 complete milestones/no loss/incomplete/writeerror. All three real
analyzers pass, with complete scheduler coverage, no trace errors/loss/initial
exception and clockspread156ns. New tools/perf/surfacemap.py validates mapping/
present pairing, joins physical backing to **lcd-flip.b (NEW register)** and
retains preceding buffer uses/calls and following latches. Mode explicitly
requires n72's physical LCD registers; this join is not valid for every board.

Diagnostic mean58.683,ceiling62,median58,low56,1%low29.801,1hitch/51.749ms,
zero skips. There are76publication gaps>30ms. This is neither an A/B gain nor
tracing-overhead evidence. All3540window presents succeed with known contiguous
backing. All3520flips have a completed matching write after that buffer's
previous selection:3500single-write/20multiple-write intervals. Page-walk
spans total128.099ms, median35.677us/max134.062us; these instrumented wall
durations do not measure the complete overhead of enabling tracing.

Between consecutive flips there are79repeated-scanout ticks. The matching
write completed **before76ticks**, and at/after3. Completion leads the76ticks
by median9.443ms (range0.194–28.291ms);71have at least1ms lead,51at least5ms.
These are successful writes to the actual physical buffer subsequently
selected, rather than neighboring presents with unknown identity.

| Publication gap ending | Gap ms | Matching present ms | Write completion → flip ms | Host running in that interval ms | Write relative to repeated scanout ticks ms |
| --- | --- | --- | --- | --- | --- |
| 2.319s | 51.749 | 0.304 | 12.302 | 9.599 | 6.957after first,10.248before second |
| 27.750s | 49.831 | 1.229 | 3.024 | 3.024 | 15.067after first,1.603before second |
| 58.233s | 49.782 | 0.309 | 11.456 | 9.721 | 7.571after first,9.099before second |
| 54.719s | 36.041 | 2.200 | 29.830 | 27.903 | 25.252before tick |

The longest gap has42.212ms running,5.025ms runnable,4.512ms sleeping;
its frontend end lag is0.794ms. Whole-window uninterruptible sleep is just
0.010ms: the older long D waits do not recur without log streaming. This does
not establish that streaming caused the older waits.

**Limits and next probe:** the write-to-flip median is11.809ms for76cycles
with a repeated scanout, versus12.317ms for3444without one. Thus this handoff
is not simply longer in every delayed cycle. A successful GL write is not a
CoreAnimation submission/eligibility marker, and physical backing is not
pixel-generation identity: other guest/CPU/DMA writes remain untraced. Source
GLESPresentView unlocks the IOSurface and calls the drawable's CA present
callback **after** the host write returns. Follow that notification, swap queue
and guest thread scheduling against the missed tick before changing pacing or
synchronization. Do not bypass CoreAnimation or publish buffers early based on
this trace. The result narrows the missing handoff; it is not a root-cause or
performance-improvement claim. Pixel's214ms gameplay gap still requires its
own matching host scheduler capture; Pixel remains disconnected/untouched.

Evidence: rawtrace/frames/milestones/logs/identity, reviewedscenes,
hitches/milestones/surfaces.json, summary.json with input hashes and all76gaps.
Reproduce with surface-map-run.py/analyze.py/summary.py in build/perf-codex;
PROFILING.md documents event semantics and the offline tool. No tests added/run.
Ordinary comparisons continue with user recording and diagnostics off.

#### Guest swap transactions and scheduling (capture complete; new hitch lead)

The next capture follows the same physical buffers through guest swap API
boundaries and selected-thread changes, using the **same frozen surface-map.apk**
and engine `2898270eb9`. No new engine/guest build or synchronization change.
`poco-swap-path-1/` retains a 60 s Jetpack title window, worker6494/vCPU6658,
150us BQL spin, physical mapping, context, 20 selected guest instruction points
and host scheduler/frequency tracing. User recorder, full accounting,
native/AHB/NAND/RAM-pointer/idle diagnostics off. No guest syslog streaming,
including after timing. Both scenes title/Startprofileoff; sameTID/nice0/CFS,
good28. Same-worker task/thread snapshot detached after timing; clean Stop,
no-qemu and exact debug-env restoration verified. Runner exits0.

1,384,698 complete records; 344,565 valid guest-PC triplets (210,023 inside
window); no loss/incomplete/writeerror. Complete scheduler coverage, no trace
errors/loss/initial-state exception; clockspread364ns. Diagnostic mean58.300,
ceiling60,median58,low56,1%low29.811,0hitches>50ms/0ms,zero skips.
Longest gaps49.974/49.890/45.016ms. This supplies attribution evidence, **no
before/after improvement or diagnostic-overhead measurement**.

3533 successful contiguous-mapped writes; 3498 fresh-buffer flips
(3466 single/32 multiple writes). 100 flip cycles contain 102 repeated refresh
ticks. Matching write completed before100ticks, after2. At98ticks the write
is complete but the corresponding SwapEnd entry has not occurred; at2 the
write is incomplete; at2 SwapEnd has started after a completed write.
Observed missed-tick phases:44before SwapWait,51inside SwapWait,2after wait
before SwapBegin,1before SetLayer,2write pending,2after SwapEnd before flip.
Completion of the write alone still does not prove CA submission eligibility.

**New transaction pattern:** of3588 completed window transactions,3498 contain
a nonnull game surface and90 contain only `SwapSetLayer(layer=1,surface=NULL)`
before a successful SwapEnd. Of100held-refresh cycles, **88 follow one of
these secondary-layer-only transactions**,12 follow a transaction with a
nonnull surface. **None of3398normal cycles follow a secondary-layer-only
transaction.** These counts join observations in order; they do not establish
that these transactions cause the delay or make no other state change.
All3498flip joins have a consistent observed physical-buffer/layer-ID order:
0xf6ee000→18 (1165),0xf658000→10 (1164),0xf5c2000→6 (1169). This is not an
independent address translation of the layer object.

| Completed window API | Calls | Median elapsed ms | Maximum elapsed ms |
| --- | --- | --- | --- |
| IOSurfaceUnlock, nonnull path | 3533 | 0.049480 | 4.490313 |
| SwapBegin | 3588 | 0.1309895 | 8.192187 |
| SwapEnd | 3588 | 0.095156 | 10.302864 |
| SwapSetLayer | 10584 | 0.001875 | 2.550209 |
| SwapWait | 3587 | 13.392500 | 33.452552 |

All observed epilogue r0 values are0; observations precede the actual return.
SwapWait elapsed includes execution on other guest threads, not just its own
CPU work. No SwapSurface/SwapSignal wrapper points observed; lower-level
notification paths remain possible. Surface-ID points follow
`_CoreSurfaceBufferGetID`, not IOSurfaceGetID. One initial unpaired epilogue
is before the window; no pending API entries at trace end.

**Audio contributor:** same-worker post-window ownership identifies presenter
as jetpack48, swap thread as SpringBoard21, LCD flipper as kernel_task, and
thread0xe078d444 as mediaserverd14 (lifetime continuity remains a limit).
In the45.016ms publication gap ending3.112742s, SpringBoard's enqueue request
to scheduled boundary spans15.386770ms;15.331250ms runs on that media thread,
with the host vCPU entirely running. Matching GL write→flip is27.876ms;
SwapEnd starts just after the repeated tick. Engine publication gap is34.950ms
and frontend end lag10.607ms: do not attribute all45ms solely to audio.
Two other gaps37.405/35.267ms include request→scheduled spans21.061/18.883ms,
with18.985/17.062ms on the same media thread. `thread_setrun` is an enqueue
request boundary, not proof of runnable state/exact queue residence. Selected
thread execution includes kernel/native work; the audio function is untraced.

**Next:** inspect the game→CA notification and the origin/order of the90
secondary-layer-only swaps. Source GLESPresentView calls drawable vt[4] after
host write and IOSurface unlock. Measure callback entry/exit and subsequent
CA processing before choosing a fix; preserve swaps, locks and notifications.
The audio path can be confirmed with converter boundaries separately. This
capture explains neither every long frame nor Pixel's older214ms gameplay gap.

Evidence: raw artifacts/identity and summary.json with input hashes under
`build/perf-codex/poco-swap-path-1/`; swap-report.json, swap-waits.json and
transactions.json preserve every pairing. Run/analyze/report/waits/transactions/
summary scripts retained in build/perf-codex. New reusable `swapcalls.py` and
pinned point manifest reproduce all API summaries, calls, flip execution
splits and present associations from the real capture exactly. Revised
audio-swap-timeline.png/svg visualizes the45ms example. No tests added/run;
ordinary A/B benchmarks continue with user recording and diagnostics off.

#### CoreAnimation notification and Bluetooth-owned execution (capture complete)

Follow-up `poco-ca-path-1/` uses the **same frozen surface-map.apk**, with no
new engine/guest build. Matching QuartzCore symbols/disassembly identify
`native_window_swap` at0x31bed1cc; the nativeWindow constructor stores it in
drawable+0x10. Thus selected PCs can trace the actual notification without a
guest shim update. New pinned27point manifest
`tools/perf/n72-7e18-ca-points.json` adds that callback, its IOSurfaceGetID,
CAImageQueueInsertImage, native buffer acquisition, display begin/finish and
the primary-descriptor/flag branches; removes unused SwapSurface/SwapSignal
points. `swapcalls.py` accepts observation-only points; their raw arguments
remain in guest-points.json, without treating void epilogues as success results.

Worker10898/vCPU11072, sameTID/nice0/CFS, good28, both scenes reviewed as
Jetpack title/Startprofileoff. BQL150us on, physical mapping/context/guestPC/
host scheduler tracing on, unrelated diagnostics/full accounting/user recorder
off. No guest syslog streaming during or after capture. Post-window task/thread
snapshot detached; clean Stop/no-qemu/exact baseline restoration verified.
Runner and all four initial analyzers plus API/CA/ownership analyses exit0.
1,680,547 complete records/no loss/incomplete/writeerror;438,791 valid guest
triplets (252,147 window), full scheduler coverage/no errors/loss/initial-state
exception, clockspread53ns. Diagnostic mean57.767,ceiling60,median58,low50,
1%low29.560,13hitches/1017.598ms,zero skips. No A/B gain/overhead comparison.

3488successful contiguous-mapped writes;3466fresh flips (3444single/22multiple).
Every flip has exactly one ordered same-thread native-swap callback and one
image insertion; callback IOSurfaceGetID matches the later layer ID for all3466.
All3488window callback and insert result observations are1. Native-swap
median0.305364/max20.383229ms; nested insertion0.282839/20.346459ms; native
buffer acquisition0.1661195/31.363386ms. These elapsed times include guest
switches/host scheduling, not just CPU cost of each function.

**The extra transactions are a symptom of stale primary state at the
compositor's decision boundary, not evidence for skipping a swap.** All3570
complete window transactions retain a primary descriptor:3466nonnull-layer
transactions observe flag byte+0xcc=2,104secondary-layer-only transactions
observe0. None take the descriptor-absent branch during timing. Of93held-refresh
cycles,81follow a secondary-only transaction;12follow a nonnull one;
0/3373normal cycles follow a secondary-only one. In **all81** matching cases,
the game callback finishes **after** that earlier compositor flag observation
(median6.396ms, range0.603–104.371ms), and after its begin_update/finish_update
entry. Callback entry and image-insertion entry/end are also after the
previous flag observation in all81cases. A completed GL write is earlier than
this notification. This ordering
does not establish when the server consumes the queue or why scheduling has
this phase. Do not treat these transactions as an independent bug to bypass.

There are134repeated refresh ticks:40before the matching write completes,
90after callback completion but before the new layer setup,1inside insertion,
1after layer setup starts/before SwapEnd,2after SwapEnd starts/before flip.
The next common-miss probe is the compositor's image-queue consumption and
update eligibility, with these producer timestamps retained.

**Larger hitches reproduce Bluetooth-owned execution without syslog
streaming.** Actual same-worker post-window task enumeration maps threads to
BTServer82/BlueTool83, jetpack55, SpringBoard21 (thread lifetime continuity is
still required). Across the13>50ms publication gaps, selected-thread running
time includes284.919ms BTServer and38.382ms BlueTool, versus278.119ms jetpack,
97.765ms mediaserverd,86.263ms launchd and73.081ms SpringBoard. Ten of13gaps
contain more than5ms Bluetooth-owned execution. Largest examples:

| Gap ending s | Gap ms | Host running ms | BTServer + BlueTool running ms |
| --- | --- | --- | --- |
| 23.783722 | 166.134531 | 164.008541 | 83.681718 |
| 45.116837 | 100.515469 | 98.813385 | 42.685780 |
| 55.867064 | 99.883333 | 95.059218 | 44.548126 |

These are actual observed owners, stronger than the earlier pmap-only
association. They identify a major source of guest execution during outliers,
not the precise function, a proven daemon restart, or the controller failure.
No fresh guest log was taken from this worker. The roughly10–11s clusters and
source's known boot-script retry path motivate a separate HCI/startup capture;
do not transfer an older worker's logs or suppress the daemons to claim a fix.

Evidence: raw trace/frames/milestones/guest points/identity/scenes and detached
ownership snapshot under poco-ca-path-1; swap-report.json, ca-summary.json,
ca-order.json and gap-threads.json retain API associations, branch arguments,
ordering and per-hitch owner execution with input hashes. Reproduction scripts
ca-path-run.py/analyze.py, ca-handoff.py, ca-gap-threads.py in build/perf-codex;
ca-order calculation retained in ca-order.py. Ordinary A/B measurements remain
diagnostic/user-recorder off. No new performance improvement; stable60 unmet.

#### Compositor image selection and a retained startup failure (capture complete)

`poco-queue-path-1/` uses the frozen bt-path.apk/engine `deb39789e4`.
New `n72-7e18-queue-points.json` pins32PCs to the same kernel/cache hashes.
It retains producer notification, insertion, swap transactions and guest
scheduling; adds render-queue generation checks, CopyImageInfo entry/epilogue,
selected serials and shared-queue update boundaries. SwapWait/native buffer
acquisition/descriptor-absence points are removed to stay within32points.
No new engine/guest build. BQL150us, physical mapping/context/guest-PC/HCI/
host scheduler tracing on; user recorder/full accounting/other diagnostics off.
Worker16763/vCPU16940, sameTID/nice0/CFS, good28. Both scenes reviewed as
Jetpack title/Start profile off; initial scene approved before recording.
Process snapshot detached after timing. Syslog relay only **after** the frame
window and detach, from the same worker. Clean Stop/no-qemu/exact env restored.
Runner, four raw analyzers, API/queue/order and HCI analyses all exit0.
2,012,202 complete records/no dropped/incomplete/writeerror;536,892 valid PC
triplets (271,844 window), full scheduler coverage/no errors or loss,52ns clock
spread. Exact window `[476488113449689,476548113449689)`.

| Mean fps | Ceiling | Median | Low | 1% low | Hitches >50ms/full duration | Skips |
| --- | --- | --- | --- | --- | --- | --- |
| 59.116667 | 62 | 60 | 56 | 30.032191 | 0/0ms | 0 |

This is a diagnostic capture, not an A/B improvement/overhead comparison.
There are53held cycles/54repeated ticks, despite no standard >50ms hitch.

All3590window render updates observe a changed generation, query the image
queue and return one image. CopyImageInfo median10.365us/max177.552us; this
query itself is not the long wait. The renderer compares a changed image
serial3546times and the previous serial44times; there are3546fresh physical
flips (3528single-write/18multiple-write) and3565successful mapped writes.
Every window query has one preceding producer serial candidate, and its selected
serial matches the renderer's subsequent comparison. **Serial derivation:**
the insertion probe observes the initial slot serial; matching disassembly
conditionally increments it later at0x31b84140. The pinned native caller always
sets flag bit1, so successful nested inserts predict base+1 modulo32bits.
The report retains base and derivation; this is not a direct post-increment
snapshot or independent proof of shared-memory identity across processes.

| Repeated-tick phase | Ticks | Ordering evidence |
| --- | --- | --- |
| Callback complete, image not yet selected | 48 | Insertion starts after the preceding consumer query in every case; insertion ends1.295–15.879ms after that query (median5.810ms). |
| Callback still pending | 3 | Insertion starts after preceding query; image can be selected before the native callback epilogue. |
| Host write pending | 1 | Insertion starts after preceding query. |
| Image already selected | 2 | Insertion completed before query; selection-to-flip21.157/18.372ms, with13.497/15.268ms running one mediaserverd thread. |

Thus the48common misses are not evidence that the queue refused an image
already fully inserted at the earlier query. Two post-selection delays instead
contain substantial audio-process execution, plus smaller host waits. Neither
observation identifies the exact audio function or licenses bypassing guest
faults/transactions. Negative callback-end→selection intervals are retained
(five normal/three held cycles), rather than assuming callback completion is
required before consumption. Process owners are post-window and require thread
lifetime continuity. Host runnable delay remains a separate target; measure
the prepared display-priority switch with diagnostics off.

The post-window3s relay retains30,801bytes/19chunks and **a startup failure
from this same worker**: BTServer26 reports the10s BlueTool boot-script timeout,
exits and is replaced by BTServer42, which initializes successfully before the
frame window. The HCI trace contains105commands/107reply chunks,90firmware
writes and two minidriver announcements. All commands have replies and every
reply is fully injected: no status, backpressure, overflow or pending error.
Queue-to-first-injection median2.033437/max2.310781ms. First sequence's final
baud command is8.915372s after first reset; next reset is at9.636436s; total
command span14.801124s. Log receive times do not align the watchdog execution
to a frame. This rules out a missing observed controller injection for this
failed startup, not delayed UART/DMA/userspace receipt. Boot-time tracing/TCG
translation can perturb the deadline; ordinary failure frequency remains
unmeasured. No new performance improvement established; stable60 unmet.

Evidence: raw trace/frame/scheduler/surface/guest points, build identity, scenes,
same-worker detached snapshot and syslog under poco-queue-path-1. API report,
queue-summary.json and queue-details.json retain every serial/query/tick join,
host-state/thread split and input hashes. Reproduction/analysis scripts:
queue-path-run.py/analyze.py, queue-summary.py, queue-details.py in
build/perf-codex. HCI exchange reproduced with tools/perf/btpath.py.

#### AAC/converter boundaries and the two nonaudio hitch pipelines (diagnostic)

`poco-audio-queue-1/` uses the same frozen bt-path.apk (app `4afc6e61`, engine
`deb39789e4`; APK SHA256
`9006ed229a71ae27665ec606f55f57f04fb3860c00779820e9aee12766cd37d1`).
No engine or guest update. The new `n72-7e18-audio-queue-points.json` retains
producer insertion, consumer selection/generation/serial, swap layers and guest
scheduling; replaces four transaction and two shared-queue observations with
AAC `ProduceOutputPackets` and `convertScaledFloatToS16` entry/epilogue pairs
(30 points). Entry and sole normal epilogues were checked against matching
private cache disassembly. The converter returns void; epilogue r0 is not success.

Snapdragon 865-class Poco F3, BQL150us, nice0/CFS; physical surface/context/
guest-PC and host scheduler diagnostics on. HCI, arithmetic, alignment-fault,
full accounting and user recording off; no syslog relay. Worker26551/vCPU26766,
same TID at both boundaries, guest tools good28. Both scenes visually reviewed
Jetpack title/Start profile off. Same-worker post-window task/thread snapshot
completed and debugger detached. Clean Stop/no-qemu/exact debug-env restored.
Capture and raw/joined analyses completed successfully. There are1,912,412
lossless complete records,503,383valid triplets (257,495 in complete frame
intervals), full scheduler coverage/no trace errors or loss, two clock snapshots
with0ns offset spread. Exact window `[478588923351023,478648923351023)`.

| Mean fps | Ceiling | Median | Low | 1% low | Hitches >50ms/full duration | Skips |
| --- | --- | --- | --- | --- | --- | --- |
| 58.516667 | 60 | 58 | 56 | 29.885780 | 2/101.488333ms | 0 |

Diagnostic attribution only: no before→after improvement or overhead claim.

**Audio accounts for some compositor delays.** There are1,881complete AAC calls
and1,881converter calls, all on mediaserverd14 thread0xe07d4bb8, with1024scalars,
scale bits0x3f800000 (1.0), and exactly one enclosing AAC call per converter.
No pending audio entry overlaps the window. AAC selected-thread running totals
841.785162ms (median285.677us); conversion totals387.535970ms. AAC includes
conversion: do not add these totals. Of the converters,1,075aligned output
pointers have median24.635us and total27.910872ms;806pointers2mod4 have
median420.2345us and total359.625098ms. These selected-thread intervals include
kernel/native work while that thread is selected; guest faults remain intact.
Post-window owner names require thread lifetime continuity.

| Pending framebuffer write at s | Notification→selection ms | Selection→register write ms | AAC running in delayed stage ms | Converter within AAC ms |
| --- | --- | --- | --- | --- |
| 47.566053 | 22.768438 | 1.165260 | 14.496720 before selection | 8.834006 |
| 49.053349 | — | 20.038854 | 13.281457 after selection | 7.729116 |
| 51.283404 | 22.149115 | 1.350833 | 16.217397 before selection | 10.195728 |
| 56.551383 | — | 18.396979 | 14.577603 after selection | 8.868281 |

**Most held cycles remain a separate path.** All3590render updates change
generation and successfully query the queue;3510compare a changed image serial,
80repeat. There are3511pending framebuffer register changes (boundary difference),
3547successful mapped presents,87held cycles/89repeated ticks. Of those ticks,
81have the callback complete but no selection yet,2have the callback pending,
2have the host write pending: insertion starts after the preceding query in
all85cases. Four were selected before the tick and inserted before its query.
Only6of81pre-selection cases have AAC overlap in the measured stages; this
does not exclude audio displacement before those stages. Insertion serials
still derive base+1 from the pinned caller, not a direct shared-memory identity.

**Neither >50ms gap contains AAC or converter execution.** Their downstream
paths are directly observed. Times below are milliseconds since the preceding
frontend frame; query times use entry, framebuffer writes use the pending
register marker. That marker (`lcd-flip`, LCD offset0x24) is **not** the later
vsync latch/publication. The refresh at roughly31ms has already passed before
the new pending base is written, so the next refresh obtains it around48ms.

| Gap ending s / duration ms | First query repeats | First new host present ends | New-image query | Pending base write | Earlier refresh | Next refresh begins/ends | Engine publish | Frontend end lag |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 17.717573 /50.010573 | 15.750 | 25.967 | 32.311 | 33.420 | 31.494 | 48.159/49.122 | 49.343 | 0.668 |
| 48.169143 /51.477760 | 16.023 | 20.760 | 35.203 | 36.256 | 31.380 | 48.032/50.679 | 51.030 | 0.448 |

First gap: host running46.528178ms, sleeping1.022291ms, runnable2.460104ms;
game selected running24.905365ms. Second:40.432241/3.934270/7.111249ms,
game24.188645ms. Background launchd/syslogd execution is also present. Engine
gaps49.964479/51.417760ms closely track frontend gaps. Thus late production
and the subsequent compositor/refresh phase explain the observed downstream
timing; frontend delivery does not hide a large stall. The precise reason the
game became late remains open, including work before the displayed gap. This
is not evidence to change latch/IRQ timing or skip synchronization.

**Looking before the displayed gaps narrows the late producer work.**
The previous-present-end→next-present-begin intervals for the two late presents
are47.029166/47.763698ms, beginning22.025/27.315ms **before** their frontend
gaps. Their preceding intervals are14.319844/16.007604ms. In the slow intervals,
all launchd-owned threads together run14.030158/14.007188ms and syslogd-owned
threads11.650571/11.710208ms: combined25.680729/25.717396ms. The main presenting
game thread runs10.328647/10.427031ms, versus8.970885/8.338905ms in the preceding
intervals. Host sleeping+runnable time totals only1.056979/2.098750ms in these
slow producer intervals. These wider intervals contain no AAC/converter either.

Existing `tallypath.py` then places exact `shim-tally` counts4800/6600 in these
same two producer intervals, at−21.643437/−26.939688ms relative to their visible
gaps, followed by46.647135/47.388229ms until the next present begins. Both
markers therefore precede the visible gap; hitch-only sample windows would
miss them. This is direct timestamp/actual-owner evidence for the periodic
logging lead with BQL150, stronger than the old pmap-only association. It does
not establish that all25.7ms is caused by the tally, or override the earlier
inconclusive ordinary suppression comparison. Of12candidate intervals, only
two exceed40ms; two of3534controls also exceed40ms. Most candidates are normal.
Next attribute launchd/syslogd functions and the stderr/IPC path in these wider
windows; batching log fragments while retaining output is a distinct possible
experiment. Do not simply repeat the unchanged suppression FPS experiment.
Local `producer-tallies.json` and audio-queue-producer-tallies.py retain the
exact interval joins and input hashes.

Across frame classes: normal3404frames, AAC650.912137ms/1.362% of vCPU running,
converter270.403737ms/0.566%; late104frames (18with AAC overlap),
AAC190.873025ms/6.758%, converter117.132233ms/4.147%; hitch2frames, both0.
Audio is a specific delay source, not a universal explanation for hitching.

Evidence: capture/scenes/identity/scheduler/guest snapshot under
`build/perf-codex/poco-audio-queue-1/`; tracked `queuepath.py` and `audiostages.py`
retain input hashes, serial derivation, host/thread splits, audio intervals,
preceding producer intervals and per-hitch display timelines. Local capture/raw
analysis scripts: audio-queue-run.py/analyze.py; pipeline plot generator:
audio-queue-timeline.py. `hitch-pipeline.png/svg` shows both gaps. Next join
native/JIT execution samples to the **producer interval before a missed query**,
rather than only aggregating samples inside frontend hitch windows; retain
scheduler/native waits separately. Pixel's older214ms gameplay hitch still
needs its own synchronized capture. Stable60/good1%low remains unmet.

#### Bluetooth controller exchange (diagnostic; two healthy references)

Engine `deb39789e4` adds default-off `LT_BT_PATH=on`, requiring an active
`LT_FRAME_PATH`. It records command opcode/length, reply queue occupancy and
the first eight reply bytes, the UART injection boundary, resets, backpressure
and overflow. Guest firmware command payloads are never recorded. The bounded
deferred buffer is 40 MiB in this mode and dumps after clean Stop. Controller
responses, UART delivery, timers and migration behavior are unchanged. Linux
and Android builds pass; no tests added/run. Frozen APK SHA256:
`9006ed229a71ae27665ec606f55f57f04fb3860c00779820e9aee12766cd37d1`.

First capture `build/perf-codex/poco-bt-path-1/`, worker13560, pairs the HCI
exchange with a 30 s guest syslog relay from that same worker. Context/guest-PC/
physical-mapping diagnostics, full accounting and user recording are off;
BQL150us remains on. Both scenes reviewed as Jetpack title/Start profile off,
guest tools good28. Clean Stop/no-qemu/exact debug-env restoration verified.
63,168 complete records, no dropped/incomplete records or write error. Relay:
31,505 bytes/20 chunks. This is a protocol diagnostic, **not an FPS measurement**.

BTServer26 completes startup and enters its message loop; no retained hang or
retry. All55commands have observed replies:45firmware writes, two each reset,
baud-rate and sleep-mode, and one each minidriver download/launch/address/name.
All56reply chunks complete injection, with no nonzero Command Complete status,
backpressure, overflow or pending bytes. The minidriver's ASCII `00`
announcement is queued and injected; its delay is2.037292ms. Across all chunks,
queue-to-first-injection median2.027891/max3.462448ms. First-to-last command
span8.916137s, with firmware-write commands spread over about7.3s. Long gaps
between commands despite prompt controller injection need guest/UART scheduling
evidence; injection is not proof of userspace receipt. This healthy boot does
not explain the earlier Bluetooth-heavy hitches or prove a missing reply.
Boot-time diagnostic overhead can also affect the guest's startup deadline.

Second capture `poco-bt-path-2/`, worker14229, uses the same installed frozen
APK for boot only. The helper's lock-screen color heuristic runs to its300s
bound; the guest boots and sleeps, confirmed in the reviewed final scene.
One Power wake was attempted during this wait, retaining the worker. This
supplies protocol evidence only, not a game scene or measured frame window.
30s same-worker relay retains23,120bytes/7chunks and successful BTServer26
initialization/message loop, with no retained hang/retry.102,882complete
records/no loss/incomplete/writeerror; identical55commands/56reply chunks,
all replies injected, no status/backpressure/overflow/pending error.
Queue-to-first-injection median2.034401/max2.577188ms; command span9.508527s.
Runner and analyzer exit0; good28, user recorder off, clean Stop/no-qemu/exact
baseline restoration confirmed. Neither reference captures a failing exchange.
The later compositor queue capture above retains a timeout/relaunch with all
observed replies injected. Keep the Bluetooth-execution lead open; trace
progress after injection before changing protocol delays or suppressing services.

`tools/perf/btpath.py` validates complete/lossless trace records, reconstructs
queue occupancy, compares retained queued/injected prefixes, pairs Command
Complete replies and the minidriver announcement, and reports unmatched work,
status and injection delays. It preserves unknown bytes beyond the prefix and
partial injection/reset outcomes. Real-capture analysis succeeds; JSON retains
the input hash, all commands/replies/state events and interpretation limits.
Raw evidence, identity, scenes and decoded same-worker logs remain local.
No optimization or new performance gain established; stable60 remains unmet.

#### TLB refill RAM-pointer lookup (local saving, no hitch improvement established)

Engine `54da21c185` adds independent default-off `LT_TLB_RAM_PTR_FAST=on`
and `LT_TLB_RAM_PTR_PROF=on`. Earlier native call graphs place dynamic TLS
resolver samples under `memory_region_get_ram_ptr`, called by TLB fills.
The helper takes a nested RCU read lock even though the translated section
already requires an RCU critical section. The fast path calls the same
`qemu_map_ram_ptr` for unaliased RAM/ROMD regions under that outer protection.
Alias resolution and the mapper's Xen behavior remain. No TLS storage or
coroutine accessor changes. The diagnostic times one call in 256 on CPU 0,
with exact five-second block boundaries and no sampling clocks/counters off.

Linux/Android builds pass. Frozen `tlb-ram-ptr.apk`, app `ae9d845e` / engine
`54da21c185`, SHA256
`08286fe5efa6e6809c823c39030b8c161ed6191f71d1a751b20ab54157e693bc`;
matching Android/Linux libraries and identity retained. Two focused 35 s pairs
on the Snapdragon 865-class Poco, off/on then on/off. BQL 150 us spin stays
on, idle spin off; only the RAM-pointer optimization changes. Sampling is on
in both modes, all other focused diagnostics/accounting/user recording off.
All eight before/end scenes reviewed as Jetpack title with Start profile off.
Same vCPU TID/nice0/CFS within each arm, good28 verdicts, zero skipped
publications; clean Stop/no-qemu/exact baseline restoration verified.

| Focused off → on | Pair 1 | Pair 2 (reversed) |
| --- | --- | --- |
| Sampled mean call, ns | 365.164 → 348.406 | 373.283 → 351.099 |
| Median block mean, ns | 364.645 → 343.764 | 372.070 → 351.407 |
| Timed samples | 17350 → 17374 | 16785 → 16855 |
| Calls/s in selected blocks | 148006.5 → 148178.0 | 143220.1 → 143822.4 |
| Largest sampled call, ns | 4843 → 92656 | 20521 → 7292 |
| Diagnostic mean fps | 59.057 → 59.171 | 59.257 → 58.971 |
| Ceiling fps | 60 → 60 | 60 → 60 |
| Median fps | 60 → 60 | 60 → 60 |
| Low fps | 56 → 58 | 58 → 56 |
| 1% low fps | 30.129 → 30.161 | 30.020 → 30.036 |
| Hitches >50 ms / full duration, ms | 2 / 101.475 → 1 / 50.471 | 0 / 0 → 3 / 151.552 |
| Longest gap, ms | 50.780 → 50.471 | 35.308 → 50.835 |

Six complete contiguous blocks wholly inside each frame window supply the call
timings. Means include matched clock overhead and host descheduling; maxima
are sampled calls only. The 16.759 / 22.184 ns local savings estimate just
2.480 / 3.177 ms per second at the control call rates (0.25–0.32% of wall
time), not measured CPU recovery. Diagnostic mean is effectively flat overall,
59.157 → 59.071 fps; hitches 2 → 4, full duration 101.475 → 202.022 ms per
70 s per mode. These runs establish a small local saving, not an ordinary
game-speed or hitch improvement. Keep the experiment off; no longer ordinary
comparison is justified by this alone. Stable 60 / clean 1% lows remains unmet.

Exact frame windows [begin,end), host monotonic ns:

- Pair 1 off `[471034099727447,471069099727447)`, on `[471215379706805,471250379706805)`.
- Pair 2 off `[471579197305259,471614197305259)`, on `[471397736136370,471432736136370)`.

Evidence: `poco-tlb-ram-ptr-focused-{1,2}-{off,on}/`, frozen build identity/libs,
`tlb-ram-ptr-focused-results.json`, all block/input hashes, reviewed
`tlb-ram-ptr-focused-scenes.png`, runner/analyzer and cleanup marker under
`build/perf-codex/`. Both scripts completed on real captures; no tests added/run.
Pixel disconnected and untouched. Dynamic TLS at other call sites remains open;
this small cost does not explain the delayed framebuffer flips.

#### Extended BQL spin budget (ordinary comparison complete)

The residual fallback capture above finds 153 of 163 selected BQL wake delays
with preceding sleeps under 100 µs, after the existing 50 µs spin has expired.
Engine `5547634eeb` adds default-off `LT_BQL_SPIN_EXTENDED=on`, selecting a
150 µs budget when `LT_BQL_SPIN=on`. Otherwise the original 50 µs budget stays.
Both modes retain vCPU0 eligibility, the untimed uncontended trylock and the
original blocking fallback. Other threads, guest time, IRQs and audio behavior
stay on their existing paths. The measured interval can exceed the budget if
the thread is descheduled. Startup logs requested/effective extended mode.

Linux/Android builds pass. Frozen bql-extended.apk, app source `c401dc6c` /
engine `5547634eeb`, SHA256
`e37a37773f8fcb6cd9f78a5d7a8f32edfc513331ee774b9419824997aa77c0cb`;
identity and matching libraries retained. Three ordinary 60 s pairs completed in
on/off, off/on, on/off order, after 50 s title settling. BQL spin is on in both
modes; only the extended budget changes. Idle spin, diagnostics, accounting and
the user recorder are off. This uses a separate APK from the completed guest
thread diagnostic. All twelve before/end screenshots reviewed as Jetpack title,
Start profile off. Every arm has the same vCPU TID/nice0/CFS across its window,
serial28 good verdict, zero skipped frames and unchanged BQL hook. Clean Stop,
no stray qemu processes and exact baseline restoration confirmed.

| Pair | Mean fps 50 → 150 µs | Ceiling | Median | Low | 1% low | Hitches >50ms | Full hitch time ms | Longest gap ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 58.033 → 59.117 | 60 → 60 | 58 → 60 | 54 → 58 | 29.056 → 30.143 | 2 → 1 | 100.592 → 50.094 | 50.320 → 50.094 |
| 2 | 57.033 → 58.183 | 60 → 62 | 58 → 58 | 52 → 54 | 28.940 → 29.615 | 6 → 2 | 335.978 → 134.248 | 81.642 → 84.133 |
| 3 | 56.400 → 57.783 | 60 → 60 | 56 → 58 | 52 → 54 | 28.762 → 29.292 | 8 → 8 | 455.911 → 408.657 | 84.752 → 55.841 |

Equal 180 s per mode: mean **57.156 → 58.361 fps (+2.11%)**, hitches
**16 → 11**, full hitch time **892.481 → 592.998 ms**. Every pair improves
throughput, low and 1% low; hitch count is unchanged in the third pair, and the
longest gap is worse in the second pair. This is a repeatable incremental gain
over the existing 50 µs spin, not stable60 or a result for Pixel/other games.
The remaining ~34 ms 1% low frames and 84.133 ms outlier leave the goal unmet.
Keep both spin switches experimental/default off pending broader validation.

Whole-worker (including boot/settling) contended/acquired/fallback outcomes:
50 µs 105029/98691/6338,110785/104120/6665,114877/107616/7261;
150 µs 105335/104329/1006,108561/107632/929,112059/111200/859.
These are not 60 s window counts; each tuple balances and the original hook
remains installed. The longer budget substantially reduces blocking fallbacks,
consistent with the previously observed short sleeps and delayed host wakeups.
It does not establish the lock owner or explain the guest execution bursts.

Evidence: `build/perf-codex/poco-bql-extended-game-{1,2,3}-{off,on}/`, frozen
build identity and libraries, `bql-extended-game-results.json` with exact
windows and individual hitch durations, analyzer/runner scripts, reviewed
`bql-extended-game-scenes.png` and cleanup marker. Runner completed exit0.
No tests added/run; Pixel untouched. Next capture fresh same-worker Bluetooth
logs and task/thread owners for the longer guest execution gaps.

#### Bounded vCPU0 idle spin (ordinary comparison complete; rejected)

Engine `357fc83aba` adds default-off `LT_IDLE_SPIN=on`, separate from BQL spin.
Before vCPU0 sleeps for guest work, it releases the BQL and observes a separate
atomic host-kick sequence for a250us clock budget. It then reacquires the BQL
and checks the original idle predicate. If still idle, it takes the original
condition wait while holding the BQL for that decision, preserving the existing
atomic release/wait behavior. Stop/paused/nonhalted states and other CPUs take
the original path. Descheduling can extend elapsed wall time around the budget.
No guest/CPU predicates are read outside the lock; no timer/interrupt/guest-time
or audio behavior is skipped. Guest idle/resume callbacks retain their bracket.
Off adds no clock reads or extra unlocks; kick sequence increments are guarded.

Startup confirms mode/budget; after vCPUs stop, counters retain attempts,
resumed-before-condition and fallback outcomes. They cover the whole worker,
not the frame window. Linux/Android builds pass; frozen idle-spin.apk,
app sourcea7bdb812 / engine357fc83aba, SHA256
`789db590c2de9e94a527c0176e25e293dd2a602c6b050b9fd15dc6ba6161a2ac`,
identity/matching symbols retained. Ordinary same-APK three60s pairs started
on/off,off/on,on/off after50s title settling, BQL spin on in both modes; all
diagnostics/accounting/user recorder off, hflags/cache off, arrays on, priority
off. Ceiling/median/low/1%low/hitches/durations and benefit remain unmeasured.
Keep experimental default off. No tests added/run.

**Ordinary comparison complete:** same frozen APK/package on the Snapdragon
865-class Poco F3, three60s pairs on/off,off/on,on/off after50s title settling.
BQL spin on in both modes; only idle spin changed. Native/fallback/AHB/other
diagnostics/accounting and user recorder off, hflags/cache off, arrays on,
priority off. Same vCPU TID/nice0/CFS checked at both boundaries of each arm.
All twelve before/end scenes reviewed as Jetpack title/Start profile off;
zero skipped publications, good28 verdicts, clean Stop/no-qemu and exact
baseline restoration confirmed. The later fallback build did not replace this
comparison's APK.

| Ordinary Jetpack, off → on | Pair1 | Pair2 (reversed) | Pair3 |
| --- | --- | --- | --- |
| Mean fps | 58.333 → 58.783 | 57.583 → 57.517 | 57.583 → 57.333 |
| Ceiling fps | 60 → 60 | 62 → 60 | 62 → 60 |
| Median fps | 58 → 60 | 58 → 58 | 58 → 58 |
| Low fps | 54 → 56 | 52 → 52 | 54 → 52 |
| 1% low fps | 29.183 → 29.774 | 28.790 → 28.810 | 29.028 → 28.722 |
| Hitches >50ms | 0 → 4 | 1 → 5 | 4 → 7 |
| Full hitch duration, ms | 0 → 204.620 | 68.268 → 254.319 | 205.578 → 356.920 |
| Longest gap, ms | 49.616 → 52.274 | 68.268 → 51.811 | 51.873 → 52.030 |
| Gaps >30ms (separate diagnostic count) | 97 → 67 | 141 → 145 | 136 → 148 |

Aggregate **57.833 → 57.878fps (+0.077%, effectively flat)**. Hitches **5 → 16**,
full duration **273.846 → 815.859ms per180s per mode**. Throughput improves only
in pair1; hitch count/duration worsen in every pair. The >30ms gap count falls
only in pair1,374 → 360 overall; this does not establish repeatable pacing
improvement. **Reject this250us idle-spin experiment, keep off and do not
repeat it unchanged.** Attributing delayed wakes to a condition wait did not
establish that this mitigation would improve game pacing. Stable60/good1% lows
remain unmet; the measured BQL-spin gain remains a separate completed result.

Whole-worker enabled attempts/resumed/fallback are16667/5339/11328,
15043/4797/10246,12857/4395/8462 (31.89–34.18% resumed without the condition
wait). They include boot/settling, not just the60s windows. BQL spin hooks remain
unchanged in every arm, with valid outcomes. These mechanism counters do not
override the negative ordinary-game result.

Evidence: `build/perf-codex/poco-idle-spin-game-{1,2,3}-{off,on}/`,
idle-spin-build-identity.json, reviewed idle-spin-game-scenes.png,
idle-spin-game-results.json (exact windows, every >50ms hitch and >30ms gap),
runner idle-spin-game-run.py and analyzer idle-spin-game-analyze.py. Pixel
disconnected/untouched; no test suites added/run. Next attribute the residual
blocking BQL fallbacks with idle spin off and the proven BQL spin on.

#### Bounded vCPU0 BQL spin (ordinary comparison complete)

Engine `23041226e7` adds default-off `LT_BQL_SPIN=on`. Only vCPU0 tries a
contended BQL for a50us clock budget before the original blocking call. The
uncontended path has no clock reads. Other threads keep ordinary blocking
acquisition; off leaves the original function pointer untouched. Trylock
success uses the existing ownership bookkeeping. Scheduling policy, affinity,
guest fault handling and audio semantics remain. Descheduling can extend the
wall interval around a clock budget; this is not an unbounded spin. This does
not spin on the guest's idle condition or direct driver waits.

Startup logs requested/effective mode. After vCPUs stop, counters retain
contended/acquired-during-spin/fallback outcomes and whether the hook remains
installed. Counters cover the whole worker, not the60s frame window. Native
wait attribution intentionally refuses this interposer; comparisons keep
native tracing, other diagnostics/accounting and Start profile off.
Linux/Android builds pass. Frozen `bql-spin.apk`, appd2dc1e12 / engine23041226e7,
SHA256 `b6de1bb3770f4c811e9922fbb1aea81f2fa37c06119c030123eb565d07b387b5`;
identity/symbols in `bql-spin-build-identity.json`. Same-APK ordinary Jetpack
comparison launched in on/off,off/on,on/off order,60s each after50s title
settling, other experiments fixed off and array caching on. No completed A/B
result was available at launch. Completed results follow. No test suites added/run.

**Ordinary comparison complete:** Snapdragon 865-class Poco F3, serial28 guest
tools, same frozen APK/package, three60s pairs in on/off,off/on,on/off order.
Each title scene settled50s. Only LT_BQL_SPIN changed. Native wait attribution,
all other diagnostics/accounting and the user recorder stayed off; hflags and
host-PC cache off, array cache on, display priority off. The same vCPU TID,
nice0/CFS were verified at both boundaries of every arm. All twelve before/end
scenes reviewed as the Jetpack title with Start profile off; zero skipped
publications in all six windows.

| Ordinary Jetpack, off → on | Pair1 | Pair2 (reversed) | Pair3 |
| --- | --- | --- | --- |
| Mean fps | 53.617 → 57.567 | 53.300 → 57.683 | 53.350 → 57.167 |
| Ceiling fps | 58 → 60 | 58 → 60 | 58 → 60 |
| Median fps | 54 → 58 | 54 → 58 | 54 → 58 |
| Low fps | 48 → 52 | 48 → 54 | 50 → 52 |
| 1% low fps | 28.129 → 29.247 | 27.959 → 29.033 | 28.221 → 28.997 |
| Hitches >50ms | 9 → 0 | 8 → 1 | 13 → 7 |
| Full hitch duration, ms | 476.106 → 0 | 403.070 → 50.311 | 690.576 → 360.425 |
| Longest gap, ms | 67.815 → 49.318 | 51.527 → 50.311 | 67.409 → 52.947 |

Aggregate mean **53.422 → 57.472fps (+7.58%)**. Hitches **30 → 8**, full duration
**1569.752 → 410.736ms per180s per mode**. Throughput, low and hitch count/duration
improve in every pair. This supports a repeatable benefit from avoiding short
BQL sleeps and their delayed host wakeups on this host/scene. It does not
identify the precise kernel/firmware wake mechanism. Pair3 retains seven
50–53ms gaps; even the first enabled arm's longest gap is49.318ms. The 1% lows
still correspond to roughly34ms frames: **stable60fps/good1% lows remain unmet**.
No Pixel or other-game benefit has been measured. Keep the experimental switch
default off while investigating remaining frame pacing and broader behavior.

Whole-worker enabled outcomes (contended/acquired/fallback) are
107919/101889/6030,106056/99411/6645,108190/101699/6491; all hooks unchanged,
93.73–94.41% acquired during spin. These counts include boot/settling and must
not be treated as counts for the60s frame windows. No spin outcomes in off arms.
Clean Stop/no-qemu/exact baseline restoration confirmed; serial28 verdict good
in every arm. Pixel disconnected and untouched.

Evidence: `build/perf-codex/poco-bql-spin-game-{1,2,3}-{off,on}/`, frozen
`bql-spin-build-identity.json`, `bql-spin-game-results.json` (exact windows and
every hitch's bounds/duration), reviewed `bql-spin-game-scenes.png`, runner
`bql-spin-game-run.py` and analyzer `bql-spin-game-analyze.py`. The later AHB
stage build did not replace this comparison's APK. Next use focused stage /
scheduler captures to separate remaining present waits, including ~33ms gaps;
retain the standard >50ms threshold for ordinary benchmark reporting.

#### Android vCPU display priority (ordinary comparison complete; keep off)

The scheduler captures above include host runnable delay, including 8.620 ms
in the delayed-PC capture's decoder-free 50.057 ms gap. This is distinct from
guest decoding work and sleeping/blocked intervals. Engine `6971278387` adds
default-off `LT_VCPU_DISPLAY_PRIORITY=on`: once at vCPU startup, Android asks
for nice -4, the normal display priority, using `setpriority` on the calling
thread. Existing higher priorities are preserved; CFS and affinity remain.
The original priority remains if setting it fails. Before/after values and
get/set errors are logged in both modes, with no per-frame priority calls.

Linux/Android builds pass. Frozen `vcpu-display.apk` is app `7312beec` /
engine `6971278387`, SHA-256
`7e841c9c96f5f1ec8c256b99dd9e78e7a6630a8df959ef8b279b1c6e8d4b9071`;
identity and symbols in `build/perf-codex/vcpu-display-build-identity.json`.
The ordinary phone comparison uses the newer frozen `bt-path.apk`, app
`4afc6e61` / engine `deb39789e4`, SHA-256
`9006ed229a71ae27665ec606f55f57f04fb3860c00779820e9aee12766cd37d1`.
Snapdragon 865-class Poco, three 60 s pairs launched on/off, off/on, on/off.
BQL 150 µs fixed on; idle spin, hflags, host-PC cache and RAM-pointer
experiments off. All diagnostic tracing, full accounting and user recorder
off; ordinary frame logging only. All twelve before/end scenes reviewed as
Jetpack title with Start profile off. Good guest-tools serial 28 verdicts.

Android granted nice -4 in every on arm; off arms retained nice 0. The active
vCPU retained its TID and CFS policy at both window boundaries, with existing
affinity. Full thread listings are retained: some subsequently created workers
inherit nice -4, so this measures the startup switch including that inheritance,
not an isolated change to vCPU priority alone.

| Pair | Mean fps off → on | Ceiling | Median | Low | 1% low | Hitches >50 ms / full duration ms | Longest gap ms | Skips |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 59.067 → 59.117 | 60 → 60 | 60 → 60 | 56 → 56 | 30.051 → 30.281 | 2/101.789 → 4/204.932 | 51.395 → 51.957 | 0 → 0 |
| 2 | 58.767 → 59.067 | 60 → 60 | 58 → 60 | 56 → 58 | 29.948 → 30.184 | 0/0 → 1/51.479 | 49.597 → 51.479 | 0 → 0 |
| 3 | 59.317 → 59.200 | 60 → 60 | 60 → 60 | 58 → 58 | 31.302 → 30.040 | 0/0 → 0/0 | 39.219 → 36.276 | 1 → 0 |

Equal 180 s per mode aggregate **59.050000 → 59.127778 fps (+0.13%)**,
2 → 5 hitches, 101.789 → 256.411 ms full hitch duration. Longest gap
51.395 → 51.957 ms; one skipped publication in pair 3 off, none elsewhere.
Pair 3 loses throughput and 1% low; the tiny aggregate throughput difference
and mixed pacing do not establish a repeatable benefit. Keep the switch off.
Stable 60/good 1% lows remain unmet; no Pixel gain inferred. No scheduler
diagnostic was active, so these arms do not attribute individual waits.

Runner exit 0; clean Stop, no stray qemu engines and exact baseline environment
restoration confirmed. Raw arms, APK identity, effective priority snapshots,
BQL outcomes and reviewed scenes are in `poco-priority-game-{pair}-{mode}/`.
`priority-game-results.json` retains exact windows, every >30 ms gap and
>50 ms hitch, all pacing metrics and maximum gaps; analysis is
`priority-game-analyze.py` in `build/perf-codex/`. No tests added/run.

### Measured and rejected

**Eight-bucket indexed indirect-site cache** (2026-10-18, engine `7b4a86c9f1`,
`LT_IBTC_INDEXED=on`, default off): replace the site's two recent targets with eight direct-indexed
buckets, probing one bucket in generated code and comparing the full PC/Thumb/context key.
This is a per-site experiment, distinct from the rejected global jump-cache geometries below.
The experimental layout reserved 128 bytes per TB in both modes (normally 32), an additional
96 bytes even for TBs without an indirect exit. Same frozen APK, Snapdragon 865-class Poco,
three interleaved off/on pairs; array caching on, batch fast path and focused GL timing off,
no live recorder or full accounting. All six game screenshots confirm the Jetpack title.

| Pair | LT Bench `icall` best / median ns, off → on | Jetpack mean fps off → on | Ceiling | Median | Low | 1% low | Hitches (>50 ms) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 37.5 / 43.1 → 14.3 / 16.9 | 50.97 → 50.87 | 58 → 58 | 52 → 52 | 42 → 42 | 19.94 → 20.01 | 16 (1121.4 ms) → 15 (1184.8 ms) |
| 2 | 41.7 / 45.7 → 16.5 / 20.7 | 47.10 → 51.50 | 54 → 58 | 50 → 52 | 34 → 44 | 11.89 → 20.25 | 46 (3684.7 ms) → 13 (919.9 ms) |
| 3 | 41.5 / 42.1 → 13.5 / 15.3 | 50.90 → 50.20 | 56 → 56 | 52 → 50 | 42 → 42 | 20.01 → 19.93 | 15 (1089.9 ms) → 17 (1239.9 ms) |

Each game window is 30 s, with zero skipped frames. Aggregate mean **49.66 → 50.86 fps (+2.4%)**
is dominated by pair 2's unusually hitch-heavy baseline; two of three pairs lose, with no
repeatable ceiling or pacing gain. The focused indirect-call improvement is real, but it does
not justify the added TB footprint for this game workload. Rejected and removed in `fd92f34185`,
including the enlarged slot. Do not repeat this cache unchanged.

Raw windows, engine logs, launch screenshots and LT Bench results:
`build/perf-codex/poco-ibtc-indexed-{1,2,3}-{off,on}/`; summary:
`ibtc-indexed-results.json`; patch: `ibtc-indexed.patch`. The first off arm's missed Home-to-game
tap produced static Home frames, preserved as `invalid-home-*` and excluded. Its valid LT Bench
pass was retained; the corrected title window was then captured without repeating the pass.
Linux/Android builds succeeded before measurement. Frozen APK SHA256:
`92fc00f23712d2a39af89554d5e6ebb5c1bd0d2c75f17ec1ac70c7e58293c759`.

**Inline jump-cache probe after the two-entry indirect-site cache** (2026-10-18,
`LT_INLINE_JC=on`, patch retained locally as `build/perf-codex/inline-jc-rejected.patch`): generated
TCG validates the existing jump-cache TB's PC, ASID/Thumb state, flags and cflags before jumping.
Two interleaved Pixel pairs cut LT Bench `icall` best / median from 21.1 / 22.1 → 7.0 / 8.1 ns and
20.4 / 20.7 → 7.0 / 8.5 ns. Poco pair 1: 32.3 / 33.0 → 14.9 / 15.6 ns; pair 2:
32.0 / 32.7 → 14.0 / 14.6 ns. The three same-APK Poco Jetpack title pairs below used array caching
on and `IT_GLES_PROF=0`; perf samples were taken only after the frame windows. Screenshots confirm
the title. No skipped frames.

| Pair | Mean off → on | Ceiling | Median | Low | 1% low | Hitches (>50 ms) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 53.3 → 52.2 | 58 → 56 | 54 → 52 | 48 → 46 | 28.0 → 27.9 | 5 (269 ms) → 7 (389 ms) |
| 2 | 53.5 → 53.2 | 56 → 58 | 54 → 54 | 48 → 48 | 28.4 → 27.5 | 3 (152 ms) → 7 (387 ms) |
| 3 | 52.0 → 52.4 | 56 → 56 | 52 → 52 | 46 → 48 | 28.1 → 27.8 | 10 (526 ms) → 4 (216 ms) |

Mean **52.93 → 52.60 fps (−0.6%)**; one pair ahead, no consistent pacing gain. Rejected for the
current game workload and removed from the engine. The slice evaluator and Linux/Android builds
had passed before removal. The probe did not promote successful global-cache hits into the site's
two-entry cache; promotion is a distinct possible follow-up, not evidence for keeping this version.
Raw windows/results: `build/perf-codex/poco-inline-{1,2,3}-{off,on}/`; Pixel results:
`pixel-inline-{on,off}.txt` and `pixel-inline-2-{on,off}.txt`. The attempted DWARF attach profile
produced zero samples and supplies no evidence about TLS call sites.


**Per-address-space TLB banks** (patch kept as `perf/rejected-tlb-banks.patch`): the user TLB index
set aside per ASID at a context switch and swapped back when that address space runs again (8 banks;
every invalidation also applied to the inactive banks). On the desktop it cut user TLB fills per
switch from 82 to 58 in Flight Control (77% of switches found their bank), and LT Bench's `rpc-64p`
refill cost from ~730 to ~430 ns. But Flight Control on the Poco lost: banks off 55.7/56.5/54.7/55.0
vs on 53.2/54.9/53.1/54.3 fps (55.5 vs 53.9, −3%, 4 of 4 pairs), with lower medians and more hitches. Each LCD frame's
dirty-tracking reset (`tlb_reset_dirty`) walks every TLB entry under the TLB lock, and with banks
that is nine tables instead of one (2.6% of the main loop on the desktop), contending with the vCPU.
QEMU also sizes a TLB only when it is emptied, so a bank that keeps coming back stayed at its first
size and thrashed until it was flushed once on return over 70% full.

No gain, so not kept: skipping the jump-cache clear on a single-page TLB flush (`tb_jmp_cache_clear_page`;
Poco, Flight Control 48.3 → 47.3 fps), a 64K-entry TB jump cache (slower than 4K in play), a lockless timer counter,
LTO for the Android engine, and the vCPU alone on the prime core. Unpinned, the vCPU spends only ~29%
of a game on the Poco's 3.19 GHz prime core, which looks like a win to take, but pinned there
(re-pinned each second, because core_ctl drops single-core masks) schedutil held the prime core
at 1.6–1.9 GHz and Flight Control fell to 9 fps (Pixel animation was also worse earlier).
Capturing the published frame (`ios_publish_bh`'s compare-and-copy, ~0.3 ms on the Poco) without the
BQL was tried after the LCD change and dropped: JellyCar on the desktop went from 22–28 frames over 50 ms
(two runs) to 220, at the same 30 fps mean, as its presents fell into a 50/16 ms alternation around the
vsync instead of an even 33.
Wider jump-cache entries (the lookup key and host code pointer copied into each entry, so a hit
reads no TB) showed no gain on the Pixel 10 with ADPF off, Doodle Jump menu, three interleaved rounds:
58.4/57.4/57.5 → 49.0/58.0/55.2 fps. That scene now runs near its 60 fps cap even without ADPF, so a
throughput change needs the Poco's Flight Control to judge; the patch is parked
(`perf/jump-cache-wide-entries.patch` in the development workspace) rather than shipped untested.
Keeping the jump cache when a CF_PCREL TB is invalidated (upstream empties every jump cache;
`tb_lookup` already rejects a stale entry by its `CF_INVALID` cflags, so only the indirect-branch
caches would be renewed) cut whole-cache flushes ~5 500 → ~1 200 over a desktop Flight Control run,
nearly all during loading, but lost on the Poco: 52.3/51.2/52.9/51.9 vs 53.6/53.2/54.6/54.8 fps with
the flush (52.1 vs 54.1, −3.7%, 4 of 4 pairs), with fewer windows at ≥ 55 fps (36–40% vs 48–63%).
Keeping the jump cache across a TLBIASID of the running process (patch kept as
`perf/rejected-jc-asid-revalidate.patch`): iOS 3 issues one ~60 times a second from a single
kernel routine, and on the desktop it accounted for 95% of jump-cache misses on empty slots in
Flight Control play. Each entry carried a generation in the unused upper half of its 32-bit PC and
was checked against the PC's current physical page(s) on its next hit instead of being dropped.
Desktop jump-cache misses in play fell 40% (8.3M → 5.0M per 50 s; an unsafe no-flush upper bound
gave the same). The Poco did not gain: interleaved, flush 57.7/57.0/53.6/56.5/53.7/56.3 vs
revalidate 54.3/54.1/57.4/51.9/55.6/55.0 fps (55.8 vs 54.7, behind in 4 of 6). Like the 2-way
cache, fewer misses do not show up in frame rate; a miss now costs about what the page check does.
Doing user-mode LDM/STM to unaligned addresses directly (patch `perf/rejected-user-unaligned-ldm.patch`):
Jetpack Joyride takes 3 300–5 100 alignment faults a second, all from one `STMIA r1!, {r8, r9}` in
the shared cache writing to a 2-byte-aligned heap address. An ARM1176 faults on that too (U=1 only
covers single loads and stores) and iOS's `sleh_alignment` performs the store, so the program
cannot tell; dropping `MO_ALIGN` for EL0 LDM/STM removed every fault (0/s). Frame rate did not
follow: Poco, interleaved, faults 37.5/31.6/37.4/38.2 vs direct 38.4/32.8/34.7/36.2 fps (36.2 vs
35.5). Each fault costs only a few µs here, so not worth departing from the hardware.
The upstream qemu-ios merge brings no speed by itself (its two TCG wins were already here).
Turning off the GL bridge's `glGetError` checks (`IT_GLES_STRICT=0`: up to nine per draw, a sync point
in some drivers): Poco, Jetpack title with guest package serial 27, three interleaved pairs, on
47.4/52.1/50.6 vs off 49.2/49.7/47.9 fps (50.0 vs 48.9). No gain on the Adreno driver, so the checks stay.
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
That split was taken while the GL shim still wrote to syslog on every frame (fixed in `7f744e80cc`,
above): a share of that kernel IPC and shared-cache time was syslogd and launchd handling the shim's
log. **Check the guest syslog when a profile shows system daemons**: `tools/perf/guestsyslog.py`
reads it through the app-services bridge on the desktop and counts lines by sender; look for per-frame lines.

**Ceiling versus dips.** A mean fps hides the shape: Flight Control's 37 fps on the Poco (engine
`ad729e45fe`) was not a steady 37 but frames at a full 60 Hz (610 of 1119 at 16 ms) broken by stalls of
3–8 refreshes (121 frames over 50 ms cost 8.9 of 30 s). The per-frame trace (`IT_FRAME_TRACE`, engine
`983fb3e6e1`) split them: an on-time frame was 10.1 ms of guest code, 2.7 ms GL and 4.0 ms BQL wait; a
hitch 51 ms guest code, 6.3 GL, 9.1 BQL wait. The vCPU was never idle (≤0.3 ms a frame) and never
off-CPU, so slow frames were frames in which the guest did ~5× the work (~5.7× the instructions), not
host preemption or the guest waiting on a device. Faster guest code and less BQL waiting lift both.
`[acct] main-cb` lines (same commit) time every timer and bottom-half callback that holds the BQL;
that is how the LCD refresh (5.7% of wall) and I2S pacing (4.7%) were found as the holders the vCPU
waited behind.

### Guest benchmarks

[LT Bench](GUEST-BENCHMARKS.md) times one guest path at a time inside iOS (best of 5 rounds, ns per
operation). Baselines, engine `16c09e7fe3` (Poco: ic8 APK with `LT_TLB_BANKS=off`; desktop: Ryzen 7
3700X VM, headless):

| Test | Poco F3 | Desktop | Note |
| --- | --- | --- | --- |
| `loop` | 4.1 | 3.5 | translated code baseline: about as fast on the phone as on the desktop |
| `fib` (call + return) | 9.6 | 7.8 | |
| `icall` (indirect call + return) | 30.7–62.7 | 28.2 | 3–6× a direct call: a site with more targets than the 2-entry branch cache misses to `helper_lookup_tb_ptr` |
| `memcpy` (per KiB) | 612 | 441 | 1.6 / 2.3 GB/s |
| `syscall` (`getppid`) | 972 | 937 | |
| `time` (`mach_absolute_time`) | 975 | 715 | a trap on this OS |
| `thread-switch` (pipe round trip) | 65 600 | 26 600 | 4× a Mach RPC to another process; the vCPU is busy (~95%, <1% halted) and the profile flat, so guest kernel work (TODO) |
| `rpc` (Mach RPC to launchd) | 13 200–16 600 | 5 700 | two address-space switches |
| `rpc-64p` minus `rpc` | ~600–800 | ~1 350 | refilling 64 pages after the switches (desktop with the rejected TLB banks: ~120) |
| `touch-64p` (per 64 pages) | 345 | 218 | the same pages with a warm TLB |
| `fault` (anonymous page) | 17 500 | 11 100 | |
| `mmap` + `munmap` | 11 800 | 4 700 | |
| `tlb-32p` / `tlb-1024p` | 5.8 / 11.3 | 5.7 / 12.7 | |
| `sleep` (`usleep(1000)`) | 1.56 ms | 1.15 ms | |

The same Poco and APK measured 13.2 µs and 16.6 µs `rpc` on two boots, and `icall` 31 and 63 ns:
absolute numbers move between boots, so compare interleaved runs as with games.

## Current state and next targets

Game frame rates, from the measurements above (full speed from the desktop runs; "—" was not
measured at that point):

| Game (scene) | Phone | Full speed | v0.0.2 | Before cross-page chaining | Before the BQL work | After it (`974c213352`) | LCD blit unlocked | `a62f05bae3` | Now (`16c09e7fe3`) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Flight Control 1.9.2 (first 30 s of play) | Poco F3 | 60 | ~18 | 19.8 | 25.3 | 35.4 | 40.7–43.2 | 44.7 (ceiling 52–58) | **55.5** (55.0–56.5; ceiling 60, median 56–58, ≥55 fps 61–76% of windows, 13–22 hitches per 30 s) |
| Doodle Jump (animated main menu) | Pixel 10 | 60 | — | 15.3 | 18.1 | 32.2 | 37.9–38.5 | 55.5 (ceiling 58–62, no hitches) | — |
| JellyCar (live-physics main menu) | Pixel 10 | 30 | — (5.7 before ADPF) | 13.7 | 14.7 | 23.3 | — | — | — |

With the quiet GL shim (`7f744e80cc`, the real guest package serial 27, engine `64d111110b`, Poco):
**Flight Control 59.0** (median 60, 1 hitch per 30 s, ≥55 fps 98–100% of the time) and **Jetpack
Joyride's title 50.5 of 60** (was 39.4; ceiling 56, 1% low 21–28, 6–7 hitches). Flight Control is
now at its ceiling, so Jetpack is the Poco's game benchmark from here. Doodle Jump holds 58–60 on both
phones and is dropped too (a game that holds a steady 60 on both phones leaves the benchmark set). Doodle Jump
and JellyCar on the Poco were already 57.2 of 60 and 29.8 of 30 before it.
JellyCar now holds its 30 fps target on both phones and has also left the benchmark set.

Absolute rates drift a few fps between sessions (the LCD-blit column was 43.2 in one A/B and 40.7 in the
next), which is why every change is judged by interleaved pairs.

| Workload | Pixel 10 | Poco F3 |
| --- | --- | --- |
| Boot to lock screen (in app) | ~21 s | ~37–45 s |
| Springboard Home ↔ Spotlight | ~50 fps | ~47–51 fps |

Beside the frame counter the app draws the guest's fps on a fixed 0–60 scale for the last 60 s, a point
per 500 ms so dips stay visible, with the current rate at the trace head and the 10 s average and low in
the corner (`71624bd7`); `fcbench.py` with `FC_FRAMES=chart.png` gives the same view, with percentiles,
from a measurement run.

Games are the main gap; see [TODO.md](TODO.md).
