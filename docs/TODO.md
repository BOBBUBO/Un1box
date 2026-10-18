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
  both phones leaves the set (Flight Control, Doodle Jump). Pixel with serial 27: JellyCar menu 30.0
  of 30 (8–45 hitches per 30 s), Doodle Jump menu 57.6–58.0. Poco, g3 = 64d111110b: Doodle
  Jump menu 57.2 of 60 (median 58, 0 hitches), JellyCar menu 29.8 of 30; Doodle Jump menu 55.5 of 60 on the Pixel (a62f05bae3); JellyCar menu
  23 of 30 (974c213352, not re-measured).
- [ ] Indirect branches with many targets (`objc_msgSend`'s final jump goes to every method): LT Bench
  `icall` is 3–6× a direct call, because a site's 2-entry cache misses and `helper_lookup_tb_ptr` runs
  (TB lookup is still ~12% of the vCPU in Flight Control on the Poco, ~4% in `qht` after jump-cache
  misses, 57% of them on empty slots). Candidates: probe the jump cache inline in the generated code.
  What empties the slots (desktop, Flight Control play): 95% are one kernel routine's TLBIASID of the
  current ASID (~60/s, each empties the 16K-entry cache); revalidating entries instead cut misses 40%
  and lost 2% on the Poco (rejected, PERFORMANCE.md). Misses are not the cost any more.
- [ ] LT Bench `thread-switch`: a pipe round trip between two threads takes 65 µs on the Poco (26 µs on
  the desktop), 4× a Mach RPC to another process. Not a wait: during the test the vCPU runs ~95% of
  the time and is halted <1%, and a desktop profile is flat (no emulator hotspot), so it is guest
  kernel work: four BSD syscalls and two scheduler switches per round trip, against two Mach traps
  with a direct handoff for the RPC. Left open only to compare with a real device.
- [ ] Ship the quiet GL shim (engine `7f744e80cc`, guest package serial 27). Built 2026-10-18 on the Mac
  with Xcode 27.1 (`~/Documents/LightTouch-Development/guest-package-27/`, not uploaded; how: qemu-ios
  `contrib/armv6-toolchain/README.md`, "Setting up a Mac to build the guest package"). The apps do not
  bundle guest tools: a record keeps the `armv6.itpack` it was imported with (pinned by
  `guest_package_sha256` in `record.json`), so users get it only through a new Guest tools file and the
  update below. Installed by hand on the Poco and Pixel records (backups in `files/pkg-backup-*`).
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
- [ ] Fewer host GL calls per draw (the next lead for Jetpack on the Poco). For each draw,
  `gles_bind_all_arrays` enables and points every client array, and `gles_unbind_arrays` disables them
  again. Strict mode adds 4–5 `glGetError` checks. A two-array sprite is ~12 host GL calls, and on Adreno
  each pays `validateShadowState` plus a context lookup (`pthread_getspecific`). These were ~8% of the
  vCPU in a Poco `gl-sprite` profile, with `glGetError` alone ~4%. Idea: track the host's
  client-array enables and pointers and skip calls that change nothing. Strict mode off alone did not
  move Jetpack (PERFORMANCE.md, rejected), so measure `gl-sprite` first.
- [ ] Dynamic TLS in the engine. `libqemu-arm.so` is `dlopen`ed, so each `__thread` access in QEMU's
  hot paths (`current_cpu`, the RCU reader) goes through `tlsdesc_resolver_dynamic`: 1.2% of the vCPU
  in Jetpack, 3.2% in `gl-sprite` on the Poco. Find the hot accessors (a call-graph profile), then cache
  them or move them out of TLS.
- [ ] Texture upload on the Pixel: `gl-upload` (16 KiB `glTexSubImage2D`) is 51–57 µs against 12 µs on
  the Poco and 3–4 µs on the desktop. It looks like the Pixel's GL stack (ANGLE or the IMG driver).
  Profile it before testing a game that streams textures.
- [ ] The guest kernel is 22% of the vCPU in Jetpack on the Poco (half the time in translated code).
  It is spread thin: locks, Mach IPC, the scheduler, timers, IOKit work loops. `0addc3e156` made its
  lock and interrupt-mask instructions cheaper. Next to look at: how many exceptions and Mach messages
  the game makes per frame, and which of them the emulator makes expensive.
- [ ] Desktop IPA install rejects archives Android accepts: Jetpack Joyride 1.3.5 (Legacy Store copy
  5997) has an entry whose central-directory name differs from its local header (an OpenFeint
  `.norm` file aliasing `Default-Portrait~ipad.png`), and Python's `zipfile` refuses it ("File name in
  directory ... and header ... differ"). Skip or tolerate such entries like the Android installer.
- [ ] Pixel: check the AHardwareBuffer present (engine 4de4ae5ba1) renders correctly and which path
  it takes (the GL init line in the engine log names `IOSurface` or `readback`).
- [ ] The vCPU still waits ~9% of wall time for the BQL, behind main-loop holds. What the main loop
  does under it: the PL080 moves audio to the I2S FIFO one 4-byte element per full
  `address_space_read`/`write` dispatch (~44K/s); the LCD converts lines and resets TLB dirty
  tracking. Remaining vCPU takes: interrupt delivery (cpu-exec.c), timer IRQ ack writes, VIC, LCD.
  Measure with the `[acct]` BQL tables and `tools/perf/bqlsites.py`.
- [ ] Pixel: the user sees Doodle Jump's menu swing between fast and stuttering. Per-second fps
  (`FC_SERIES=1`, 60 s, 974c213352 + b11ef28b): ADPF on 31.3 and 29.5 mean, each second 19–44; ADPF off
  17.3 and 18.6, 11–29. The swing is the same proportion (about ±25%) either way and no second
  reaches 60, so ADPF is not causing it (it is worth 1.7× here). Next: per-frame gaps against the
  BQL hold log, to see whether the slow seconds line up with main-loop work (audio DMA, LCD).
- [ ] Try the engine's upstream merge (`qemu-ios` `dev/upstream-sync`, 7ab545390b) on a phone;
  verified on Linux only.
- [ ] Desktop frontends poll for frames (Linux 10 ms loop, Windows `SDL_Delay(8)`); move them to
  frame-published wakeups like Android.
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
