# Open work

Tick items off here as they land (with the commit), so nothing is lost between sessions.
Performance results go in [PERFORMANCE.md](PERFORMANCE.md).

## Performance (current focus)

- [ ] Android: steady ~60 Hz guest UI. Poco F3, engine 64d111110b, Home ↔ Spotlight swipes
  (`inappfps.py --swipes 10`, two runs): 55.3 / 56.3 fps, 94% of frames on the 16.7 ms cadence (was
  44.8–49.2). Pixel not re-measured.
- [ ] Android: steady target frame rate in common period games. Now (engine 4de4ae5ba1): Flight Control
  55.8 of 60 fps on the Poco (six runs 53.6–57.7; ceiling 60, median 56–60, ≥55 fps 58–86% of the
  time, 7–28 hitches per 30 s; 56.1 with 1997c1a488); Jetpack Joyride title 35 fps (ceiling 42–46,
  guest-code bound: GL is ~12% of the vCPU; the desktop runs it at ~54). With the quiet GL shim
  (engine 7f744e80cc, real guest package serial 27, kept by the verdicts in 995ce258): Flight Control
  59.0 (median 60, 1 hitch per 30 s, ≥55 fps 98–100% of the time: at its ceiling, dropped as a
  benchmark), Jetpack 50.5 (ceiling 56, 1% low 21–28, 6–7 hitches per 30 s; was 39.4 with 114
  hitches), desktop Jetpack 59; 52.2 with engine 0addc3e156 + 2df170ca78 (+6%, three pairs). Jetpack is the Poco benchmark now; a game that holds a steady 60 on
  both phones leaves the set (Flight Control, Doodle Jump, JellyCar). Pixel with serial 27: JellyCar menu 30.0
  of 30 (8–45 hitches per 30 s), Doodle Jump menu 57.6–58.0. Poco, g3 = 64d111110b: Doodle
  Jump menu 57.2 of 60 (median 58, 0 hitches), JellyCar menu 29.8 of 30; Doodle Jump menu 55.5 of 60 on the Pixel (a62f05bae3); JellyCar menu
  23 of 30 (974c213352, not re-measured).
- [ ] Indirect branches with many targets (`objc_msgSend`'s final jump goes to every method): LT Bench
  `icall` is 3–6× a direct call, because a site's 2-entry cache misses and `helper_lookup_tb_ptr` runs
  (TB lookup is still ~12% of the vCPU in Flight Control on the Poco, ~4% in `qht` after jump-cache
  misses, 57% of them on empty slots). Candidates: probe the jump cache inline in the generated code.
  Inline jump-cache probing was tried in three Poco pairs and removed: `icall` best / median
  32.3 / 33.0 → 14.9 / 15.6 ns, but Jetpack mean 52.93 → 52.60 fps. Pairwise ceiling
  58/56/56 → 56/58/56, median 54/54/52 → 52/54/52, low 48/48/46 → 46/48/48,
  1% low 28.0/28.4/28.1 → 27.9/27.5/27.8; hitches 5/3/10 → 7/7/4 (full durations in
  PERFORMANCE.md). Do not repeat the same probe. A possible new idea is promoting inline hits
  into the site's two-entry cache; this version did not do that.
  Eight-bucket indexed per-site caching (`7b4a86c9f1`, `LT_IBTC_INDEXED`) was also measured and
  removed (`fd92f34185`): `icall` best/median 37.5/43.1, 41.7/45.7, 41.5/42.1 →
  14.3/16.9, 16.5/20.7, 13.5/15.3 ns. Jetpack means 50.97/47.10/50.90 → 50.87/51.50/50.20,
  ceiling 58/54/56 → 58/58/56, median 52/50/52 → 52/52/50, low 42/34/42 → 42/44/42,
  1% low 19.94/11.89/20.01 → 20.01/20.25/19.93; hitches 16/46/15 → 15/13/17,
  durations 1121.4/3684.7/1089.9 → 1184.8/919.9/1239.9 ms per 30 s.
  Aggregate 49.66 → 50.86 fps is dominated by one hitch-heavy baseline; two pairs lose.
  No repeatable game gain to justify +96 bytes/TB. The extra memory was removed too.
  Keep the patch/results locally; do not retry unchanged. Full evidence in PERFORMANCE.md.
  What empties the slots (desktop, Flight Control play): 95% are one kernel routine's TLBIASID of the
  current ASID (~60/s, each empties the 16K-entry cache); revalidating entries instead cut misses 40%
  and lost 2% on the Poco (rejected, PERFORMANCE.md). Misses are not the cost any more.
- [ ] Measure the TB lookup hit-path experiment (`164ec930cc`, `LT_TB_LOOKUP_INLINE=on`, default off).
  Fresh Poco Jetpack samples put `tb_lookup` at 5.19% self (46.2% call-chain unwind errors).
  The fully validated hit is now inlined; misses/accounting use the existing lookup, and register
  hardening remains enabled. Preliminary Linux/Android builds pass; disassembly confirms inlining.
  No speedup claimed yet. Compare direct LT Bench costs and unrecorded same-APK Jetpack pairs.
- [ ] LT Bench `thread-switch`: a pipe round trip between two threads takes 65 µs on the Poco (26 µs on
  the desktop), 4× a Mach RPC to another process. Not a wait: during the test the vCPU runs ~95% of
  the time and is halted <1%, and a desktop profile is flat (no emulator hotspot), so it is guest
  kernel work: four BSD syscalls and two scheduler switches per round trip, against two Mach traps
  with a direct handoff for the RPC. Left open only to compare with a real device.
- [x] Install the quiet GL shim in place (engine `7f744e80cc`, guest tools serial 27).
  Serial 27 is installed on both benchmark phones. Linux supports
  `lighttouch-linux --library LIB --record ID --set-guest-package ITPACK`; Android has "Guest tools"
  on a stopped device (details below). A new record is not required.
- [ ] Prepare guest tools for public distribution. Serial 27 remains private:
  `contrib/appsync/appsync_cert.h` embeds an Apple certificate, and the `contrib/` guest sources
  lack license headers. Do not publish the package or firmware before resolving those issues.
- [x] Guest package verdicts on Android and Linux. The portable runtime never told the guest's loader
  (qemu-ios `contrib/it-boot`) that a package was good, so `it_boot` reverted any package newer than
  the record's after two boots (`MAX_TRIES`) and refused that serial from then on. That is why serial
  27 installed by hand lasted two boots. `Portable/src/GuestPackageJudge.hpp` now judges each boot as
  the macOS app's `GuestPackage.verdict` does: good after 10 s with the agent alive and a loader
  report, bad after 300 s of running time without that. Time paused does not count. The verdicts go to
  `state/guest-verdicts`, and each offer carries them as `verdict good N` / `verdict bad N` lines.
- [x] Update a record's guest tools in place, keeping the device. `lt_library_set_guest_package(library,
  id, path, ...)` refuses while the record is open (`state/.lease`) or when the file is not an
  `ITPACK01` package, then stages and swaps `assets/guest.itpack` and rewrites `guest_package_sha256` in
  `record.json`. The guest's `it_boot` installs a newer serial over the existing system at the next
  boot, keeping apps and data. Linux: `lighttouch-linux --library LIB --record ID --set-guest-package
  ITPACK`. Android: "Guest tools" on a stopped device applies the file to the selected device. Tested:
  desktop (serial 26 record → 27, two boots judged good) and the Pixel (26 ↔ 27 through the picker).
- [ ] Guest tools update, follow-ups: check that the package has a family for the record's board and
  build before swapping (today an older or mismatched package is accepted; `it_boot` then keeps the
  installed serial), and add the action to the Windows and macOS apps.
- [x] LT Bench GL tests (`gl-get`, `gl-state`, `gl-draw`, `gl-sprite`, `gl-upload`, `gl-present`), run on
  the desktop, the Poco and the Pixel (docs/GUEST-BENCHMARKS.md, PERFORMANCE.md).
- [x] Cache host client-array enables, pointers and client-active texture unit (engine `6762325419`,
  `LT_GLES_ARRAY_CACHE=off` restores the old path). Guest array contents still refresh every draw;
  failed or disabled arrays cannot leave stale host enables. Poco `gl-draw` best µs, three pairs:
  7.81/8.92/7.93 → 7.49/7.38/7.05; Pixel 1.40/1.49 → 1.28/1.32. `gl-sprite` stays within the spread.
  Direct Pixel setup plus cleanup: 0.619 → 0.228 µs/draw; host pointer/enable/unit calls
  2/4/4 → approximately 0/0/0 per draw. Poco Jetpack, three 30 s pairs: mean 51.6 → 52.5 (+1.7%,
  two of three ahead), ceiling 56–58 → 56–58, median 52 → 52–54, low 44–48 → 46–48,
  1% low 20.9–28.1 → 27.8–28.5, hitches 6–7 → 1–8. A useful local saving; game-level gain and
  pacing remain inconclusive (full pairs in PERFORMANCE.md). Strict pointer validation stays on.
- [x] Measure GL batch-buffer RAM copying in Jetpack (engine `d99e32c8eb`, default still off).
  `LT_GLES_BATCH_FAST=on` is experimental, default off. Pixel, two interleaved pairs: the direct
  steady 12-byte batch copy is 0.206 → 0.043 µs and 0.199 → 0.044 µs (all RAM hits).
  `gl-sprite` best / median: 4.34 / 4.40 → 3.96 / 4.21 µs and 4.14 / 4.23 → 3.93 / 4.31 µs;
  end-to-end draw/upload medians do not improve. Two Poco Jetpack pairs: mean 53.07 → 53.50 fps
  (+0.8%, within session spread), ceiling 58/58 → 58/58, median 54/52 → 54/54,
  low 46/48 → 48/50, 1% low ~28/28.3 → 28.5/28.8, hitches 12/3 → 7/5
  (840/152 → 387/283 ms). No consistent pacing gain; keep it off for ordinary use.
- [ ] Dynamic TLS in the engine. `libqemu-arm.so` is `dlopen`ed, so each `__thread` access in QEMU's
  hot paths (`current_cpu`, the RCU reader) goes through `tlsdesc_resolver_dynamic`: 1.2% of the vCPU
  in Jetpack, 3.2% in `gl-sprite` on the Poco. Find the hot accessors (a call-graph profile), then cache
  them or move them out of TLS.
- [ ] GL context selection: focused timing (`c2e06923de`, `LT_GLES_CONTEXT_PROF=on`, default off)
  on the Poco Jetpack title puts viewport init/make-current/dispatch at 0.565/110.474/4.039 µs
  per call (3396 calls, 1752 selected-context changes over six five-second blocks); present surface
  is 0.726/20.837/1049.176 µs (1625 calls). The viewport cost is largely before dispatch.
  Inspect context switching and explicit synchronization before changing viewport state forwarding.
  These are focused diagnostic timings; no before/after optimization or FPS gain is claimed.
- [ ] Texture upload on the Pixel: `gl-upload` (16 KiB `glTexSubImage2D`) is 51–57 µs against 12 µs on
  the Poco and 3–4 µs on the desktop. It looks like the Pixel's GL stack (ANGLE or the IMG driver).
  Profile it before testing a game that streams textures.
- [ ] The guest kernel is 22% of the vCPU in Jetpack on the Poco (half the time in translated code).
  It is spread thin: locks, Mach IPC, the scheduler, timers, IOKit work loops. `0addc3e156` made its
  lock and interrupt-mask instructions cheaper. Next to look at: how many exceptions and Mach messages
  the game makes per frame, and which of them the emulator makes expensive.
  `42395839f3` adds a default-off `LT_EXCEPTION_PROF` diagnostic for exception counts, raw SVC
  `r12`, short-descriptor fault statuses and Mach-message trap attempts, with host setup timings
  separate from guest handlers. Linux/Android builds pass; phone capture and overhead are pending.
  `tools/perf/exceptions.py LOG --last 30 [--frames FULL_FRAME_LOG]` summarizes complete snapshots.
- [ ] Dirty-state query hot path: the fresh Poco Jetpack profile puts `physical_memory_is_clean`
  at 1.25% inclusive and `physical_memory_get_dirty_flag` at 0.59% self / 0.89% inclusive.
  It queries three one-bit flags through general bitmap-range scans, entering/exiting RCU three
  times. Consider a single RCU guard and direct bit checks for this specific query; retain ordinary
  coroutine TLS access elsewhere. This is an unimplemented lead, with no speedup claim.
- [ ] Desktop IPA install rejects archives Android accepts: Jetpack Joyride 1.3.5 (Legacy Store copy
  5997) has an entry whose central-directory name differs from its local header (an OpenFeint
  `.norm` file aliasing `Default-Portrait~ipad.png`), and Python's `zipfile` refuses it ("File name in
  directory ... and header ... differ"). Skip or tolerate such entries like the Android installer.
- [x] Pixel AHardwareBuffer present uses `IOSurface`: confirmed in the debug engine GL init log
  during the client-array A/B runs (PowerVR D-Series DXT-48-1536, guest-api=1). LT Bench renders
  correctly through that path.
- [ ] Revisit the remaining BQL waits with current scene captures and recheck the earlier ~9%
  wall-time figure. The PL080 FIFO burst path (`a62f05bae3`) and LCD conversion outside the BQL
  (`d7c4927685`) are already implemented. Inspect the remaining LCD dirty tracking,
  interrupt delivery (cpu-exec.c), timer IRQ ack writes, VIC and LCD register access.
  Measure with the capture's BQL wait/hold intervals, `[acct]` tables and `tools/perf/bqlsites.py`.
- [ ] Pixel: the user sees Doodle Jump's menu swing between fast and stuttering. Per-second fps
  (`FC_SERIES=1`, 60 s, 974c213352 + b11ef28b): ADPF on 31.3 and 29.5 mean, each second 19–44; ADPF off
  17.3 and 18.6, 11–29. The swing is the same proportion (about ±25%) either way and no second
  reaches 60, so ADPF is not causing it (it is worth 1.7× here). Next: per-frame gaps against the
  BQL hold log, to see whether the slow seconds line up with main-loop work (audio DMA, LCD).
- [ ] Try the engine's upstream merge (`qemu-ios` `dev/upstream-sync`, 7ab545390b) on a phone;
  verified on Linux only.
- [ ] Desktop frontends poll for frames (Linux 10 ms loop, Windows `SDL_Delay(8)`); move them to
  frame-published wakeups like Android.
- [x] Manual Start/Stop scene profiling above the Android FPS graph (engine `50a08eb2d2`).
  Saves the frontmost app name/bundle ID, per-frame GL totals and slow calls, BQL wait/hold intervals,
  vCPU wall/CPU counters and Android buffer/scale/pacing/post stages. Pixel Battle Bears identity,
  and DoodleJump identities, saving/pulling, Stop while paused and text/JSON/PNG reports work; this is diagnostic instrumentation, with no FPS
  improvement or overhead claim. [PROFILING.md](PROFILING.md) explains capture/retrieval and limits.
- [ ] Characterize recording overhead with interleaved on/off pairs before treating a captured rate
  as an uninstrumented baseline. Use manual recordings from the user's hitching scenes to rank
  slow calls and correlate engine gaps, BQL intervals and Android presentation stalls.
  Original recorder, two Poco Jetpack pairs: mean 54.0/53.8 → 50.0/50.2 (−7.1%), ceiling
  58/58 → 56/54, median 54/54 → 50/50, low 50/50 → 46/46, 1% low 28.3/28.3 → 26.9/20.8,
  hitches 6/3 → 8/8 (303/189 → 459/420 ms). Updated writer, two new pairs: mean
  52.7/52.0 → 48.6/49.6 (−6.2%), ceiling 58/58 → 54/56, median 54/54 → 50/50,
  low 46/44 → 38/40, 1% low 20.6/20.7 → 20.0/20.1, hitches 11/13 → 17/14
  (917/1038 → 1255/1124 ms). Recording overhead remains significant; reducing it is still open.
- [ ] Investigate the user's Pixel audio looping during recording. Capture file writes are now off
  the BQL and Android PCM-read lock (engine `9b38a2ffd3`); new metadata retains start/end audio
  underrun and PCM drop counters. Underrun warnings also occurred between captures, so the cause
  and audible recovery remain unconfirmed. User recordings and detailed reports for five games
  are local under `build/perf-codex/pixel-user-{captures,reports}/`; use their scene timestamps
  to guide focused profiles, particularly COD Zombies' vCPU-heavy long frames.
  Two updated Poco Jetpack captures saved complete files, no discarded rows, zero AudioTrack
  underruns, PCM drops and read/write failures. This does not establish audible Pixel recovery.
  Five new Pixel captures on the updated recorder also saved complete files without recorder errors.
  The user reports no audible looping this time. AudioTrack underruns increased by 0/1/0/1/5 in
  DoodleJump/Home Screen/Bike or Die!/Jetpack/Gravity Guy; PCM drops and read/write failures are zero.
  Detailed charts/text/JSON: `build/perf-codex/pixel-new-reports/README.md`. Jetpack's 214 ms gap
  near 6.30 s includes 206 ms execution wall time but only 83 ms vCPU CPU; obtain host scheduling
  or blocking evidence. Gravity Guy's early 1.55/1.22 s gaps consume 1.44/1.16 s vCPU CPU;
  the user identified them as loading/menu transitions, not active-play stutters. Its later
  201 ms gap at 15.37 s uses 191 ms CPU; identify that scene and profile a confirmed gameplay hitch.
- [x] Document every performance change and its measurement: [PERFORMANCE.md](PERFORMANCE.md).
- [x] Guest fps readout and 60 s graph in the Android app (b11ef28b).

## Firmware and assets

- [x] Rewrite [ASSETS.md](ASSETS.md) easiest route first: `tools/get-assets.py` (downloads the
  upstream app ZIP and unpacks it on any OS, including guest.aar's LZFSE Apple Archive), then by
  hand, then IPSW preparation. `--verified-tools` makes the folder IPSW preparation needs
  (Linux `lighttouch-prepare` verified with it).
- [ ] Owner decision: attach `LightTouch-n72ap-7E18.zip` (itbase + armv6.itpack + bootrom.bin,
  built locally in `~/Documents/LightTouch-Development/release-assets/`) to a release. It is
  Apple's copyrighted firmware; upstream and devos50/qemu-ios `n72ap_v1` host the same kind of
  files, but rehosting carries DMCA-takedown risk. If attached, add it as route 1 in ASSETS.md.
- [ ] Android: download and unpack the upstream files in-app (port the get-assets.py decoder), so
  phone-only users need no computer and nothing is rehosted.

## Cleanup

- [x] Prune the upstream Mac app code (branch `dev/cleanup`; the macOS port still builds with SwiftPM
  from the root `Package.swift` via `macOS/build-shared.py`):
  1. [x] Xcode project/workspace, xctestplans, `Packages/BuildTools`, `Packages/ReleaseChecks`,
     `Models/`, the upstream `scripts/` (kept `check-android-package.py`, `measure-android-session.py`),
     `tests/offline`, `tests/sessions` and the Xcode-only `Configuration/` files (b40cb574).
  2. [x] `firmware-catalog.json` now lives in `Portable/firmware/` (2a9a518f). `CActivation` stays in
     `Packages/FirmwareKit/Sources`: SwiftPM cannot take a target path outside its package and FirmwareKit
     stays for the macOS port; `Portable/firmware` already compiles it from there.
  3. [x] `LightTouchMac/`, `LightTouchServices/`, `macOS/build-native.py`, the `macos-arm64` dispatcher
     target and the `LightTouch`/`CLockdown`/`LightTouchServices` targets (57bb9aaa).
     `Packages/DeviceServices` stays: `LightTouchCore` (linked by `LightTouchDesktopHost`) depends on
     `HostServiceWire`/`HostServiceClient`.
  4. [x] `CONTRIBUTING.md` rewritten without Xcode; stale handoff/branch notes and the stopped TCG
     profiling proposals removed.
- [ ] Verify the SwiftPM macOS build after the cleanup on a Mac (`macOS/build-shared.py`, or at least
  `swift build --product LightTouchDesktopHost --product LightTouchDevice`, then
  `swift test --package-path Packages/LightTouchCore`). `Package.resolved` lost its Sparkle pin by hand;
  SwiftPM will refresh its origin hash.
- [ ] Further pruning, needs a Mac to compile-check: most of `Packages/LightTouchCore` is upstream Cocoa-app
  logic (App, Apps, UI, Capture, Catalog models) that `macOS/SharedHost` does not import; trim it to what
  `NativeSession`/`Import`/`NativeAppServices` use, then drop `Packages/DeviceServices` with it.
  Also unreferenced since `scripts/` went: `build-support/{sources.json,developer-disk-images.json}`.

## Android bugs seen, not yet fixed

- [x] The first touch after the guest wakes was often ignored: the "Guest screen asleep" label sat
  above the guest view and the view jumped down on wake. It is now an overlay on the guest view.
  (Harness note: the guest re-sleeps ~5 s after a Power wake; a second Power tap before the wake
  shows puts it back to sleep.)
- [ ] Stop was seen disabled once after Store use; not reproduced.
- [x] Store overlay lacks `testTagsAsResourceId`, so UI automation cannot find its controls.
- [ ] Some catalog apps marked compatible need iOS 4 APIs (Doodle Jump 3.x: GCD). Consider flagging
  apps that weak-link iOS 4 symbols, or surfacing Last crash automatically after a quick quit.

## Guest graphics

- [x] Untextured white or grey geometry on Android (engine 4ae00eaca8): Jetpack Joyride's lab
  background, Battle Bears' zombies and menu art, Real Racing's opponent cars and scenery. Cause:
  ES `glTexImage2D` does not convert between formats, and the bridge uploaded opaque PVRTC (decoded
  to RGBA) as `GL_RGB`, so each such upload failed with `GL_INVALID_OPERATION` (engine log:
  `REFUSED upload-error:0xde1:0x502`) and the texture stayed empty. Desktop GL converts, which is
  why Linux and macOS never showed it. The upload now does the conversion itself. Confirmed on the
  Poco F3 (Jetpack: white before, correct after, same NAND) and by the user on the Pixel 10
  (Jetpack, Battle Bears, Real Racing).
