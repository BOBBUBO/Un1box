# Open work

Tick items off here as they land (with the commit), so nothing is lost between sessions.
Performance results go in [PERFORMANCE.md](PERFORMANCE.md).

## Performance (current focus)

- [ ] Locate the first missed deadline in the frame path. Two Poco Jetpack title
  scheduler traces now align exact 60 s frame windows with vCPU states; user recorder,
  full accounting and focused engine timing off. In 8 / 7 long gaps, execution is
  83.1 / 84.2% of gap time; runnable waits 8.8 / 10.8%, sleeping 8.1 / 4.9%.
  Capture 2's 312 in-hitch CPU samples are spread across native engine and guest
  work; no single culprit established. GL-driver self share 11.06% ordinary vs
  8.33% hitch, not evidence for one dominant GL stall. Three hitches execute on
  CPU 7 entirely at max reported 2.7456 GHz, so frequency variation is not a
  complete explanation. `float32_mul` enrichment is only 13 samples; do not
  repeat rejected VFP experiments on that alone. Capture metrics (diagnostic,
  not before/after): mean 54.50 / 53.817, ceiling 60 / 58, median 54 / 54,
  low 50 / 48, 1% low 28.438 / 28.359, hitches 8 (409.964 ms) / 7 (371.120 ms).
  One skipped capture-2 publication is outside its hitch gaps. Scenes reviewed,
  cleanup confirmed. `hitchsched.py` / `hitchcpu.py` processed real captures;
  exact windows and limitations in PERFORMANCE.md. Next: sparse default-off
  guest-present/LCD/publication timestamps to determine where cadence first
  breaks, before changing another broad hotspot. Pixel untouched; these Poco
  observations do not identify the earlier Pixel user-capture pauses.
  Sparse default-off milestones now implemented (`78eaa87497`), Linux/Android
  builds pass, one completed Poco capture processed by `framepath.py`. All six
  ~50 ms gaps already exist at engine publication; frontend lag stays under
  0.635 ms. Each contains three timely LCD ticks (max lateness 0.076662 ms),
  one late guest framebuffer flip and one publication. Two surface presents
  finish ~41–42 ms before the flip; no single overlapping host present exceeds
  2.936 ms. One hitch has 25.535 ms host runnable delay, so retain scheduler
  attribution per gap. Diagnostic metrics: 52.683 fps, ceiling 56, median 52,
  low 48, 1% low 28.042, 6 hitches/301.566 ms, zero skips. 83,047 milestones,
  no drops/incomplete records; scenes/cleanup reviewed. No speedup claimed.
  Next: guest address-space residency through the present-to-flip interval.
  Completed one context capture (`5511f5466d`), 302,468 records with no drops:
  all 3,248 presents under CIDR 7 / TTBR0 0xdb6e018; all eleven hitch flips
  under CIDR 6 / TTBR0 0xa802018. Unknown CIDR 5 / TTBR0 0xa7ba018 consumes
  35.37% of hitch execution versus 15.45% in normal frames (same capture).
  The 44.799 s hitch includes 20.674 ms execution under key 5 in a 24.268 ms
  latest-present-to-flip interval. Identify the process and its work next;
  register residency includes kernel/native work and does not prove causation.
  Diagnostic metrics: 53.200 fps, ceiling 58, median 54, low 48, 1% low 27.863,
  11 hitches/616.772 ms, zero skips. Host runnable delays remain a separate
  lead. One old publication marker is late outside every hitch boundary;
  explicit legacy analysis preserves it. `c0347336e6` orders future markers
  inside the frame lock; Linux/Android builds passed, subsequent phone captures
  below contain zero late publication markers.
  Full evidence/limitations in PERFORMANCE.md; no speedup claimed.
  Same-worker process mapping now works (`6d87d27dc8`, opt-in loopback guest
  debugger attached only after capture). Two new captures identify the recurring
  third task as mediaserverd: normal/hitch execution shares 16.47/39.89% and
  14.65/27.74%. One present-to-flip interval includes 25.612 ms under its pmap.
  Captures: mean 52.683/55.067, ceiling 58/58, median 52/56, low 48/50,
  1% low 28.047/28.539; 9 hitches/473.829 ms and 5/257.218 ms, zero skips.
  Diagnostic modes differ; this is not an improvement comparison. Capture 2's
  61 mediaserverd hitch samples nominate stereo sample-rate conversion, native
  float arithmetic and guest alignment handling. Next: focused timing of float
  paths and fault PCs, preserving audio and exception semantics; sparse samples
  alone do not justify retrying rejected experiments. Named guest code and
  process-aware analysis tools now exist. Full provenance/data caveat in
  PERFORMANCE.md (one superseded initial scheduler row outside the window).
  Engine `6743f6a81b` adds default-off `LT_AUDIO_PATH_PROF`: sampled original
  f32 add/sub/mul operands/status/call timing with instruction PCs, and all
  CPU 0 AArch32 alignment fault PCs. Linux/Android builds pass. Use the
  same-worker process snapshot and `audiopath.py` to compare frame classes;
  one real capture/analysis complete on `6b9bca678a`, which provides a bounded
  80 MiB diagnostic buffer. All 227,042 faults belong to mediaserverd's
  `convertScaledFloatToS16`; 223,494 at `STMIA r1!, {r8,r9}` PC0x303f4e2c.
  41 fault bursts (10 ms grouping gap), median spacing 1494.644 ms; 23/30
  hitches overlap faults. Media normal/hitch execution share 14.37/55.48%.
  Its 51,246 sampled FP operations have no special inputs, only seven fail
  status eligibility, largest hitch sample 1.198 us. Concentrated conversion/
  AAC transform work is the stronger lead than a rare slow FP operation.
  Diagnostic FPS52.150, ceiling56, median52, low46, 1%low20.050;
  30 hitches/1742.444 ms, zero skips. This is not an A/B or overhead result.
  Next: lighter fault-only confirmation, full architectural handler round-trip
  timing and guest scheduling/IO cause of batching. Keep audio/exception
  semantics; do not repeat the rejected store bypass. Full exact evidence and
  timeline in PERFORMANCE.md. No new optimization or gain claimed.
  `c5cc5be3b9` now implements fault-only mode and optional matched, sampled
  architectural fault/return markers (`LT_ALIGNMENT_PATH_PROF=on|returns`).
  `alignmentlatency.py` splits same-worker intervals by scheduler state while
  retaining failed/outside-window matches. Clean final Linux/Android builds pass.
  Fault-only confirmation (`LT_AUDIO_PATH_PROF=off`): 227,047 conversion faults,
  41 bursts / median spacing 1497.628 ms, 22/25 hitches overlap faults.
  Media normal/hitch execution share 14.58/53.58%; zero VFP samples/counters.
  Diagnostic mean52.500, ceiling58, median52, low48, 1%low20.158;
  25 hitches/1448.322 ms, zero skips. Same-worker process mapping, complete
  scheduler coverage and clean Stop verified. Three fault-free hitches remain;
  host runnable delays also contribute. Full provenance in PERFORMANCE.md.
  Return timing capture/analysis now complete: 1665/1665 matching samples,
  916 wholly inside window, 749 outside; no abandoned/pending/cross-frame rows.
  Normal/hitch median3.125/3.178 us, p99 3.750/5.625 us; largest40.990 us
  includes host waits. Many small operations in batches, no sampled ms-scale
  fault. Same frozen APK, not optimization/overhead A/B. Diagnostic mean51.117,
  ceiling56, median52, low46, 1%low20.057; 28 hitches/1642.669 ms, zero skips.
  235,490 faults; 23/28 hitches overlap, media execution14.37/55.85%.
  Next: guest audio buffer/decode cadence and scheduling dependencies, keeping
  sound/architectural faults. No new optimization or measured gain claimed.
  VFP helper-boundary experiment rejected/removed (`3cc8fa6127`): three
  same-APK ordinary pairs, best/median ns7.7/8.9,7.8/10.6,7.6/8.3 →
  7.6/9.1,7.6/9.3,8.2/8.9. FPS53.933/53.800/54.000 →53.567/52.500/52.767
  (aggregate53.911→52.944,−1.8%, all pairs lose); ceiling58/58/58→58/56/58,
  median54/54/54→54/52/52, low48/48/48 unchanged,
  1%low28.075/28.198/28.225→28.417/28.416/28.058;
  hitches4/3/3→2/2/4, durations203.204/172.970/153.216→
  101.145/100.932/221.575ms, zero skips. All scenes/cleanup reviewed,
  recorder/diagnostics off. Focused guest benchmark retained; do not repeat
  the same helper combination. Full evidence in PERFORMANCE.md.
  A converter-entry debugger inspection yielded zero argument samples before
  its deadline (detached/stopped/restored cleanly); it supplies no buffer-size
  or cadence evidence. Next cadence probe must confirm that its entry hook runs.
  Instead, the existing sampled alignment hook now records fault-time R0–R3,
  LR/SP/CIDR without debugger or guest memory reads. The analyzer validates and
  retains paired snapshots. Linux/Android builds pass; one capture confirms
  1670/1670 matched pairs,926 window samples all with paired registers from
  one thread and AAC `ProduceOutputPackets` caller. Scale is1.0; fault-time R3
  is a changing remainder, not entry buffer length.237,803 faults /41 bursts,
  median spacing1504.614ms;26/28 hitches overlap. Media normal/hitch share
  14.70/53.60%. Handler medians3.281/3.281us; max1.760052ms includes execution
  across several pmaps, so guest thread scheduling attribution is still needed.
  Diagnostic mean51.950,ceiling58,median52,low46,1%low20.049;
  hitches28/1570.464ms,zero skips. Complete scheduler/events, scenes and clean
  Stop/baseline verified. No optimization gain claimed. VFP A/B completed
  separately on its frozen APK with diagnostics off.
  Next approach: selected guest PC snapshots (`ab08a5d277`, default-off
  `LT_GUEST_TRACE_PC`), observing scheduler transitions and decode boundaries
  with faults/arithmetic diagnostics off. Initial and clean final Linux/Android
  builds pass; runtime confirmation pending. `guestpoints.py` validates paired
  observations against actual frame/context/process identity. A local
  `Switch_context` boundary after the TPIDRPRW write can identify scheduled
  threads; select actual scheduler enqueue/block points only after disassembly.
  This will distinguish runnable displacement from guest waits; it is not yet
  a proven dependency or a measured speedup.
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
- [ ] Confirm the TB lookup hit-path experiment (`164ec930cc`, `LT_TB_LOOKUP_INLINE=on`, default off).
  Fresh Poco Jetpack samples put `tb_lookup` at 5.19% self (46.2% call-chain unwind errors).
  The fully validated hit is now inlined; misses/accounting use the existing lookup, and register
  hardening remains enabled. Preliminary Linux/Android builds pass; disassembly confirms inlining.
  Three same-APK Poco Jetpack title pairs: mean 52.03/53.60/53.80 → 54.13/54.73/54.43 fps
  (53.14 → 54.43, +2.4%, 3/3 ahead). Ceiling 56/58/58 → 58/60/58, median 52/54/54 →
  54/54/54, low 46/50/50 → 50/50/50, 1% low 27.94/28.61/28.80 → 28.27/27.99/28.50.
  Hitches 8/5/3 → 6/8/2; durations 419.0/301.9/153.4 → 304.5/425.1/116.9 ms per 30 s.
  `icall` best/median 27.1/28.8, 31.2/33.0, 31.6/32.7 → 26.8/28.5, 26.8/27.5, 26.5/27.1 ns.
  Useful throughput candidate, no consistent pacing gain. Keep off pending reverse-order
  confirmation and a combined baseline. Exact windows/screenshots in PERFORMANCE.md and local results.
  Confirmation completed on current engine `9fd2a85805` / `lcd-stages.apk`, with
  all diagnostics off, order on/off, off/on, on/off. Three more pairs: means
  52.60/52.00/53.63 → 53.23/53.30/53.87 (52.74 → 53.47, +1.4%, 3/3 ahead).
  Ceiling 58/58/58 → 58/58/58, median 52/52/54 → 54/54/54, low 46/46/48 →
  48/48/48, 1% low 27.88/28.09/22.14 → 25.85/28.13/28.28. Hitches 6/7/9 →
  12/6/7, durations 305.3/534.4/683.3 → 822.6/334.5/385.9 ms per 30 s.
  All six final scenes reviewed, recorder off, zero skips. Six of six pairs across
  both individual sets favor throughput; no consistent pacing gain.
  Combined cache+lookup comparison complete on current engine `f43fbe4986` /
  frozen `vfp-mul-zero.apk`, three 30 s pairs off/on, on/off, off/on, all diagnostics
  and other experiments off. Means 53.63/53.57/52.97 → 53.07/54.17/53.83 fps
  (53.39 → 53.69, +0.6%, within drift). Ceiling 58/58/58 → 58/58/58;
  median 54/54/52 → 54/54/54; low 50/50/50 → 48/50/50;
  1% low 28.33/28.26/28.43 → 28.16/28.24/28.14. Hitches 3/1/9 → 7/3/7,
  durations 150.7/50.6/503.1 → 360.9/151.9/371.8 ms per 30 s. Total hitches
  13 → 17 (704.4 → 884.6 ms/90 s). All six title/recorder-off scenes reviewed,
  zero skips. No repeatable combined or pacing benefit; keep lookup off, array
  cache on for local host-call saving. Clean Stop/no-qemu/baseline readback
  confirmed; handed phone to masked-IRQ focused comparison. Exact bounds in PERFORMANCE.md.
- [ ] Measure cache in the indirect miss helper (`6a71c1842f`, `LT_IBTC_L2=on`,
  default off). The existing two-entry generated path and TB layout are unchanged;
  a 384 KiB table allocated only when enabled checks source-site address plus
  complete generated key and promotes hits into the existing site cache. Invalidation
  generation changes prevent old hits; no retention/geometry change. A key without
  the site is unsafe because source-TB flags are implicit. Fill only PC-relative targets,
  whose invalidation renews that generation (all ARM targets qualify). Single-vCPU machines only;
  accounting/breakpoints/execution logging retain full lookup. Independent
  `LT_IBTC_L2_PROF` counts hits/attempts and samples one in 256 helper calls.
  Linux/Android builds pass, frozen `ibtc-l2-pcrel.apk`/symbols/identity retained. Focused
  runner/analyzer completed after exact IRQ-delivery process exit and successful
  clean-stop marker. Both 35 s pairs complete, off/on then on/off. All four title
  scenes reviewed, recorder off, zero skips, six complete contiguous timing blocks
  per arm. Hit rate 67.166/66.670%; sampled mean 526.62/522.40 → 497.65/500.65 ns
  (−5.5/4.2%), median block mean 522.45/522.89 → 497.42/500.19 ns. Enabled hit /
  fallback 438.86/618.30 and 448.13/604.52 ns. Max 200.31/13.23 → 60.47/27.14 µs
  retained. Diagnostic mean 51.46/51.06 → 52.09/53.09 fps; ceiling 56/56 → 56/58,
  median 52/52 → 52/54, low 44/44 → 48/48, 1% low 19.98/20.10 → 28.11/28.47.
  Aggregate diagnostic 51.26 → 52.59 (+2.6%); hitches 34 → 14,
  2275.9 → 761.4 ms/70 s per mode. Focused local saving repeats; ordinary results follow.
  All three ordinary 30 s pairs complete, off/on,on/off,off/on; diagnostics and
  user recorder off, array cache on. Mean 53.70/51.50/51.97 → 54.03/52.30/54.27;
  ceiling 58/56/58 → 58/56/58, median 54/52/52 → 54/52/54, low 50/42/44 → 48/48/50,
  1% low 28.10/20.32/20.75 → 28.38/26.83/28.59. Aggregate 52.39 → 53.53 (+2.2%),
  all three ahead; hitches 28 → 21, 2046.9 → 1303.1 ms/90 s. Pair 1 nearly flat,
  hitch duration worse. All six scenes reviewed, zero skips, cleanup confirmed.
  Keep default off pending longer independent confirmation; no steady 60 claim.
  Exact bounds/raw evidence in PERFORMANCE.md.

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
  Android surfaceless bindings tried in `e9e13d795e`, default-off switch; all three
  Poco ES1 contexts lack `GL_OES_surfaceless_context` and used pbuffer fallback.
  Further pairs stopped, cleanup confirmed, both scenes reviewed, zero skips.
  Requested off/on diagnostic FPS 53.26 → 52.34; ceiling 58 → 58, median 54 → 52,
  low 46 → 40, 1% low 28.18 → 20.28; hitches 11 (873.8 ms) → 15 (1205.5 ms).
  No context used the change; no speed comparison established. Experiment removed
  in `2e79c09b2e`; exact contiguous context markers retained. Offline
  `tools/perf/contextstages.py` parsed real logs and preserves complete blocks.
  Do not retry on this driver without new capability evidence. Inspect other
  context selection/synchronization leads before changing viewport forwarding.
- [ ] Measure exact-zero single-precision VFP multiplication (`f43fbe4986`,
  `LT_VFP_MUL_ZERO_FAST=on`, default off). The recent Poco profile includes `float32_mul`
  1.07% self and software multiply 0.62%; the host-FPU underflow predicate also sends
  mixed zero/normal multiplication to software. Return the exact XOR-signed zero
  directly for zero times normal/zero; retain softfloat for all other operands and
  preserve exception flags. Translation selects helpers; ordinary baseline unchanged.
  `LT_VFP_MUL_ZERO_PROF=on` separately counts calls/eligible/mixed operations and samples
  one in 256 mixed calls with exact five-second block bounds. Linux/Android builds pass;
  frozen `vfp-mul-zero.apk`/symbols. Both focused 35 s pairs complete, second reversed;
  other diagnostics off, array cache on. Mixed zero/normal ~269–280K/s (30–31% of calls);
  sample means 357.05/368.14 → 343.42/333.79 ns, block medians 356.20/368.63 →
  336.83/333.71 ns, including clocks/descheduling (enabled max 284 µs retained).
  Local saving repeats, but diagnostic FPS 52.94/53.77 → 54.03/52.09 (53.36 →
  53.06, −0.6%). Ceiling 58/58 → 60/58, median 54/54 → 54/52, low 46/48 → 48/44,
  1% low 20.17/28.22 → 28.49/20.28; hitches 16/7 → 5/15, durations
  1077.4/356.0 → 300.5/1004.2 ms per 35 s. All four title/recorder-off scenes
  reviewed, zero skips. No consistent pacing/game gain; keep off. Ordinary
  diagnostic-off pairs remain open after combined/masked-IRQ work. Raw bounds,
  samples and retained outliers in PERFORMANCE.md / `vfp-mul-zero-results.json`.
  Runner stopped/no-qemu/baseline readback confirmed, handed phone to combined queue.
- [ ] Texture upload on the Pixel: `gl-upload` (16 KiB `glTexSubImage2D`) is 51–57 µs against 12 µs on
  the Poco and 3–4 µs on the desktop. It looks like the Pixel's GL stack (ANGLE or the IMG driver).
  Profile it before testing a game that streams textures.
- [ ] Measure masked physical-interrupt BQL avoidance (`e82b9e770b`,
  `LT_MASKED_IRQ_FAST=on`, default off). The first two BQL-enabled captures place
  6.1/5.8% wall wait at the interrupt handler, 2043/1691 acquisitions/s; not all
  this time is recoverable. The precheck reuses ARM routing/mask rules, preserves
  pending levels/EXITTB and bypasses replay/stepping/special events. Independent
  `LT_IRQ_CHECK_PROF=on` counts/times CPU-0 masked candidates and actual no-op checks
  without full accounting/BQL tables. Linux/Android builds succeeded; frozen
  `masked-irq.apk`/symbols/identity retained. Both focused 35 s pairs complete,
  off/on,on/off; IRQ profiling on both, other diagnostics/experiments off, array
  cache on. Masked mean 7.985/8.072 → 0.422/0.426 µs, wait 7.108/7.183 → 0 µs,
  masked wall 0.883/0.891% → 0.047/0.048%; ~1104–1134 masked candidates/s.
  All acquisition wait 5.036/5.036% → 4.199/4.201% wall: most wait remains.
  Diagnostic FPS 51.97/54.20 → 52.97/53.03 (53.09 → 53.00, −0.2%).
  Ceiling 56/58 → 58/58, median 52/54 → 54/54, low 44/50 → 44/48,
  1% low 27.15/28.82 → 27.85/28.29. Hitches 13/3 → 9/10, duration
  972.7/151.8 → 838.5/526.4 ms per 35 s; total 16 → 19 (1124.6 → 1364.9 ms/70s).
  All four title/recorder-off scenes reviewed, zero skips. Local saving repeats;
  no consistent game/pacing gain, keep off. Clean Stop/no-qemu/baseline readback
  confirmed. Ordinary runner prepared, not queued; delivery experiment below
  (also covers masked no-ops) has priority. Exact bounds in PERFORMANCE.md.
- [ ] Measure CPU-only physical ARMv6 IRQ delivery (`5b956fc6ae`, default-off
  `LT_IRQ_DELIVERY_FAST=on`). Physical entry uses the same CPU-owned path already
  unlocked for synchronous exceptions; device MMIO acknowledgement still locks.
  Single-current/maximum-vCPU only, physical IRQ/FIQ optionally EXITTB, no EL hooks;
  v7/newer/M-profile, virtual/special/unknown requests, replay and stepping stay locked.
  Atomic request snapshot preserves levels, EXITTB and new-request kicks. Independent
  `LT_IRQ_CHECK_PROF` adds unlocked counts and delivered-check time/wait subsets.
  Linux/Android builds succeeded; clean frozen `irq-delivery.apk`/symbols/identity.
  Both focused 35 s off/on,on/off pairs completed, all four title scenes reviewed,
  zero skips, six complete contiguous timing blocks per arm. Array cache on;
  masked-fast/L2/other diagnostics off, IRQ profiler on both, no recorder/accounting.
  Delivered check µs 87.205/74.900 → 1.946/2.168; wait 83.265/71.263 → 0/0.
  All check/wait wall 5.765/5.448% and 5.135/4.829% → 0.293/0% and 0.189/0%.
  Diagnostic means 53.06/54.43 → 49.54/53.83 fps; ceiling 58/60 → 58/58,
  median 54/54 → 52/54, low 46/50 → 34/48, 1% low 28.25/28.78 → 14.85/28.61.
  Hitches 10/2 → 42/8, durations 819.4/101.4 → 3234.8/764.8 ms per 35 s.
  Aggregate 53.74 → 51.69 (−3.8%), hitches 12 → 50 (920.8 → 3999.6 ms/70 s).
  First enabled arm has two IRQ-spike blocks (~1907/1666 deliveries/s), other
  enabled blocks normal; reverse pair also loses. Retain all outliers. No proved
  recovered total vCPU time: device MMIO still locks, more guest-handler work can
  offset local savings. Keep off; ordinary comparison deferred, prepared only.
  Exact bounds in PERFORMANCE.md, raw/analyzer/results/montage retained. Runner
  clean Stop/no-qemu/baseline confirmed; serialized L2 runner has now started.
- [ ] The guest kernel is 22% of the vCPU in Jetpack on the Poco (half the time in translated code).
  It is spread thin: locks, Mach IPC, the scheduler, timers, IOKit work loops. `0addc3e156` made its
  lock and interrupt-mask instructions cheaper. Next to look at: how many exceptions and Mach messages
  the game makes per frame, and which of them the emulator makes expensive.
  `42395839f3` adds a default-off `LT_EXCEPTION_PROF` diagnostic for exception counts, raw SVC
  `r12`, short-descriptor fault statuses and Mach-message trap attempts, with host setup timings
  separate from guest handlers. Poco Jetpack: 3368.7 SVC/s (64.68/frame), 3973.4 data aborts/s
  (76.29/frame, 3907.1/s alignment), Mach send/receive attempts 1432.5/1597.9 per second.
  All instrumented host dispatch totals 222.1 ms / 30.009 s (0.74% wall), excluding guest handlers.
  Focus on guest handler work rather than host entry dispatch; do not retry the rejected alignment
  shortcut. Full raw windows/counts in PERFORMANCE.md. Instrumentation overhead remains pending.
  `tools/perf/exceptions.py LOG --last 30 [--frames FULL_FRAME_LOG]` summarizes complete snapshots.
  `tools/perf/guestpc.py` now reads self samples against the same worker's before/after perfmaps
  to rank guest instruction PCs. It excludes ambiguous/reused mappings and has no process or
  guest-stack attribution. First real runtime use succeeded: Poco Jetpack diagnostic, 13,355 vCPU samples,
  44.44% of event weight mapped (5,942 samples), zero ambiguous/unstable sampled mappings;
  7,413 unmapped samples include native work and missing JIT mappings. Native lookup remains
  prominent (`tb_lookup_legacy` 4.95% self); 46.13% call-chain unwind errors. The enabled-arm profile also resolves: 13,428 vCPU samples, 44.11% of event weight mapped,
  zero ambiguous/unstable sampled mappings; 46.08% unwind errors. Matching guest symbols
  remain pending. Full bounds/evidence in PERFORMANCE.md.
- [ ] Dirty-state query hot path: the fresh Poco Jetpack profile puts `physical_memory_is_clean`
  at 1.25% inclusive and `physical_memory_get_dirty_flag` at 0.59% self / 0.89% inclusive.
  It queries three one-bit flags through general bitmap-range scans, entering/exiting RCU three
  times. Engine `cc6063a40f` implements direct bit checks and one guard for the combined query,
  behind default-off `LT_DIRTY_QUERY_FAST`. Bitmap/RCU lifetimes are retained; no coroutine TLS
  pointer is cached. Linux/Android builds pass. Three same-APK Poco Jetpack pairs:
  mean 51.47/54.10/54.43 → 53.30/52.80/54.90 fps (53.33 → 53.67, +0.6%, within drift).
  Ceiling 58/60/58 → 58/58/60, median 52/54/54 → 54/54/54, low 44/50/50 → 48/44/50,
  1% low 19.81/28.33/28.75 → 20.72/28.03/28.65. Hitches 16/5/4 → 10/11/3,
  durations 1152.3/256.1/217.3 → 621.6/701.9/153.3 ms per 30 s. LT Bench mixed too.
  No repeatable game/pacing gain; keep off. Focused sampler timings, two pairs with second
  reversed: 502.74 → 427.29 and 495.71 → 419.93 ns/query (−15.0% / −15.3%).
  A local saving only; diagnostic FPS also loses: 54.54/55.51 → 53.14/51.80 fps, ceiling
  58/60 → 58/58, median 56/56 → 54/52, low 50/50 → 48/42, 1% low 28.29/28.67 →
  28.32/20.16. Hitches 5/2 → 5/14, durations 265.7/117.2 → 253.2/969.0 ms per 35 s.
  Sampler and perfmaps on in both modes; these are separate diagnostic windows, not ordinary
  FPS baselines. Full query/FPS bounds and raw blocks in PERFORMANCE.md.
  `4d06b57f50` adds default-off `LT_DIRTY_QUERY_PROF`: CPU 0 directly times one in 256 combined
  queries, with exact block boundaries and query/sample counts. Linux/Android builds pass;
  focused timing capture is complete above, separate from uninstrumented FPS windows.
  Timings include timer overhead and descheduling; call rates vary, so do not infer an
  end-to-end saving from the sampled mean alone.
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
  A separate default-off `LT_GLES_PRESENT_PROF` diagnostic (`5b38fec1df`) splits
  GL surface-present acquire/GPU wait from readback/conversion/copy/tail. It keeps
  exact complete five-second blocks, fault retries and the slowest call's phase
  breakdown without per-row clocks. Linux/Android builds succeeded; frozen
  `present-prof.apk`/matching symbols/identity retained. `tools/perf/presentstages.py`
  selects exact windows and preserves outliers. Both 30 s profiler off/on,on/off
  pairs complete; user recorder off, all four scenes reviewed, zero skips, cleanup
  confirmed. Diagnostic FPS 54.30/54.17 → 50.87/52.33; ceiling 58/58 → 56/58,
  median 54/54 → 52/52, low 50/48 → 40/46, 1% low 28.71/28.60 → 19.88/20.50.
  Aggregate 54.23 → 51.60 (−4.9%); hitches 7 → 28, 388.8 → 1973.2 ms/60 s.
  All presents mapped, no errors/retries. Mean acquire 644/708 µs, copy 173/174 µs,
  tail 104/100 µs; slowest calls 6.70/6.73 ms dominated by acquire (6.43/6.40 ms).
  Preserve required GPU completion; these calls do not explain 50–100 ms gaps.
  Dedicated diagnostic results only, no optimization gain (details in PROFILING.md).
  Measure with the capture's BQL wait/hold intervals, `[acct]` tables and `tools/perf/bqlsites.py`.
  Engine `f64fb44e3c` adds default-off `LT_BQL_PROF` to collect lock tables and outer vCPU
  timings without per-TB instruction/lookup/TLB-fill accounting; full accounting remains
  separate. `f9d7faec8c` scopes MMIO attribution so lockless reads cannot mislabel later
  interrupt locks, and adds exact dump bounds/completion markers. `bqlsites.py` uses
  actual selected intervals, omits incomplete new dumps and saves all sites to JSON.
  Linux/Android builds pass; frozen `bql-prof.apk`/symbols, engine `f9d7faec8c`.
  Two 30 s pairs completed (off/on, on/off), all other diagnostics off; no live
  recording/full accounting. Means 53.10/53.53 → 49.43/50.93 fps (53.32 → 50.18,
  −5.9% diagnostic overhead). Ceiling 56/58 → 56/58, median 54/54 → 50/52,
  low 50/50 → 36/40, 1% low 28.48/28.61 → 15.00/20.04; hitches 5/3 → 31/15,
  durations 252.0/166.8 → 2370.6/987.5 ms per 30 s. First off has one publisher
  skip; others zero. All four title/recorder-off scenes reviewed. Exact bounds
  in PERFORMANCE.md and `bql-prof-results.json`; do not use enabled FPS as baseline.
  BQL wait 9.08/8.74% wall, dominated by interrupt handler 6.09/5.84%,
  2043/1691 acquisitions/s (29.8/34.5 µs mean wait). Real new logs parsed by
  `bqlsites.py`; all sites/bounds retained. Runner stopped/restored baseline cleanly.
  Holder spans can include condition waits and callbacks can unlock internally; use
  actual vCPU waits before attributing those durations to lock stalls.
  Engine `9400ab1b68` adds default-off `LT_LCD_FORCED_SNAPSHOT_SKIP` and independent
  `LT_LCD_SNAPSHOT_PROF` timing. First establish linear-path/forced-update frequency in Jetpack;
  composed/rotated scanouts bypass it. Retained dirty bits may add subsequent repaint/uploads.
  Linux/Android builds succeeded. Poco Jetpack uses the path: final six five-second diagnostic
  blocks have 1803 updates / 1566 forced, 882.20 ms snapshot time / 760.40 ms forced time;
  mean 489.29 µs (forced 485.57 µs), max 3216.35 µs. Three same-APK Jetpack pairs, second reversed, reject the skip: means
  51.33/54.03/52.83 → 48.90/54.63/51.77 fps (52.73 → 51.77, −1.8%). Ceiling 56/58/58 →
  58/60/58, median 52/54/52 → 52/54/52, low 44/50/48 → 34/50/42, 1% low
  20.01/28.66/28.58 → 14.89/29.18/20.40. Hitches 15/4/4 → 36/5/13; duration
  1086.8/219.3/202.7 → 2747.4/302.5/1017.8 ms per 30 s. Aggregate snapshot mean
  512.46 → 76.42 µs/update across separate approximate timing blocks, but pacing worsens.
  Removed from current engine in `197f75d7ae`; do not retry unchanged. Every snapshot
  clears again; independent diagnostics remain, skip log fields are zero. Linux/Android
  builds pass. Frozen measurement APKs/symbols unchanged. Full evidence in PERFORMANCE.md.
  A separate follow-up: `physical_memory_snapshot_and_clear_dirty` resets TLB write tracking
  unconditionally, even for an empty snapshot. `physical_memory_test_and_clear_dirty` already
  resets only when it actually cleared bits. Count empty snapshots; consider retaining the clear
  but skipping only empty-snapshot TLB resets, avoiding retained-dirty-bit side effects. Unimplemented.
  Engine `42d50e5d2e` separately adds default-off `LT_TLB_DIRTY_FAST`: check compact-entry flags
  and host address range before reading full-entry metadata, retaining every reset and the same
  lock/flag semantics. Linux/Android builds pass; Android disassembly confirms the skipped loads.
  Three off/on pairs complete (second reversed), LCD snapshot timing on in both modes:
  mean 52.13/53.43/51.57 → 53.13/52.37/53.50 fps (52.38 → 53.00, +1.2%, mixed).
  Ceiling 58/58/58 → 58/56/58, median 52/54/52 → 54/52/54, low 46/50/44 → 48/48/48,
  1% low 28.32/28.43/20.52 → 28.39/28.26/28.49. Hitches 6/4/8 → 4/2/5,
  durations 302.9/203.6/568.7 → 221.5/100.7/271.4 ms per 30 s.
  Snapshot means 521.03/511.41/494.90 → 545.40/473.65/507.85 µs/update;
  weighted 509.11 → 508.97 µs, no confirmed targeted cost reduction. All six scenes
  reviewed, recorder off, zero skips. Keep off; no repeatable ordinary game gain.
  The snapshot timing also includes the TCG after-sync `run_on_cpu` barrier, not just the scan.
  Split bitmap/reset/barrier costs before attributing the full ~0.5 ms or pursuing more changes;
  retain the ordering between dirty flag clearing and guest writes.
  Engine `9fd2a85805` adds default-off `LT_LCD_SNAPSHOT_STAGES` to split presync,
  allocation/setup, bitmap copy/clear, TLB reset, dirty-clear listeners and the TCG
  ordering barrier; it also counts requested-range dirty pages and empty snapshots.
  All operations/order are retained, logging follows the LCD unlock. Linux/Android
  builds succeeded; four focused stage arms complete, second pair reversed. Reset µs/update
  82.29/108.22 → 56.03/56.23, but the ordering barrier dominates at 443–477 µs;
  outer means 535.67/564.20 → 543.52/512.15. Empty requested-range snapshots
  213/358 of 1805/1804 off, 208/163 of 1803/1803 on. This excludes alignment padding;
  a zero requested-range count does not alone justify skipping full bitmap resets/barriers.
  Diagnostic FPS 52.80/48.23 → 53.17/54.57; ceiling 56/58 → 58/58, median 54/50 →
  54/54, low 48/32 → 48/48, 1% low 28.78/11.93 → 28.35/28.49; hitches 5/44 →
  3/3, durations 286.4/3513.6 → 151.8/168.8 ms per 30 s. The second off arm is
  hitch-heavy, cause unknown; retain it. No repeatable ordinary game benefit or diagnostic
  overhead established. All four scenes reviewed, zero skips. Full separate stage/FPS
  bounds and timing breakdown in PERFORMANCE.md. Queue clean-stop/env restoration succeeded.
  Another source lead, unimplemented: under the TLB lock, a clear `tlb.c.dirty` MMU-mode
  bit guarantees that its main/victim tables were flushed and no insertion has occurred.
  Count active modes and measure reset cost before considering skipping these empty modes;
  do not use `n_used_entries` alone to infer emptiness without accounting for victims.
- [ ] Pixel: the user sees Doodle Jump's menu swing between fast and stuttering. Per-second fps
  (`FC_SERIES=1`, 60 s, 974c213352 + b11ef28b): ADPF on 31.3 and 29.5 mean, each second 19–44; ADPF off
  17.3 and 18.6, 11–29. The swing is the same proportion (about ±25%) either way and no second
  reaches 60, so ADPF is not causing it (it is worth 1.7× here). Next: per-frame gaps against the
  BQL hold log, to see whether the slow seconds line up with main-loop work (audio DMA, LCD).
- [ ] Try the engine's upstream merge (`qemu-ios` `dev/upstream-sync`, 7ab545390b) on a phone;
  verified on Linux only.
- [ ] Desktop frontends poll for frames (Linux 10 ms loop, Windows `SDL_Delay(8)`); move them to
  frame-published wakeups like Android.
- [x] Measure publisher notification preservation (`LT_PUBLISH_WAKE_EARLY=on`, default off).
  The old wait snapshots notifications after copy/listener/audio work, potentially overlooking a
  new frame during that work until another notification or its 10 ms timeout. The experiment
  snapshots before work. Independent `LT_PUBLISH_WAKE_PROF` counts notifications during work,
  subsequent waits and frame timestamp age (including LCD/pacing work). Linux/Android builds
  pass. Two diagnostic off/on/on/off pairs: mean 52.51/54.46 → 53.71/53.06 fps
  (53.49 → 53.39, −0.2%), ceiling 58/58 → 58/56, median 54/54 → 54/54,
  low 46/50 → 48/48, 1% low 20.53/28.61 → 28.15/28.53. Hitches 16/6 → 6/3,
  duration 1037.4/305.0 → 338.0/153.0 ms per 35 s. Mean frame age 1.987/2.047 →
  1.959/2.031 ms. No throughput/age gain; fewer hitches do not yet establish pacing
  improvement. Keep off. Final off had extra settling after runner interruption;
  full evidence, exact bounds and this limitation are in PERFORMANCE.md.
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
- [ ] Evaluate Android API 35 `ProfilingManager` system traces alongside manual recordings to
  distinguish host scheduling delays from blocking in long frames. The API is in the current
  compile SDK; cancellation stops a continuous capture and returns available data. Requests can
  be delayed or rate limited, so retain actual trace timing/results separately from engine logs
  and handle callbacks asynchronously. No integration or device capture yet; source/API inspection only.
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
