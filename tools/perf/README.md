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
- `gamefps.py SECS` reads delivered guest fps while a game is on screen;
  `fcbench.py APK SECS` installs an APK, boots, unlocks, starts Flight Control
  (first icon on Home page 2) and measures it. `FC_FRAMES=chart.png` also saves
  the exact frame window as `chart.png.frames.log`. See docs/PERFORMANCE.md.
- `gameprof.sh OUTDIR [SECONDS]` attaches simpleperf to the running engine worker.
  `SP_CALLGRAPH=fp` or `dwarf` adds call stacks; `SP_FREQ=1000` changes the sampling
  frequency (default 4000 Hz). It retains recorder diagnostics and rejects empty
  recordings. Resolve samples with the exact matching unstripped engine library.
- `guestpc.py PERF_DATA perf-PID-after.map [--before-map perf-PID-before.map]` maps attached
  self samples to guest instruction PCs, using the NDK's simpleperf reader. Conflicting/stale
  mappings are excluded; unmapped samples include native work and missing JIT map entries.
  See PROFILING.md for capture setup and limitations.
- `hitchsched.py TRACE FRAMES --engine-pid PID --json OUT` aligns Perfetto scheduler
  states with published frame gaps; `--frequency` also correlates CPU frequencies.
  `hitchcpu.py PERF_DATA FRAMES SCHEDULER_JSON ...` compares timestamped vCPU self
  samples inside ordinary and long frames. Both operate offline; capture setup and
  matching-symbol requirements are in PROFILING.md.
  An explicit `hitchsched.py --allow-superseded-initial-state` can report/exclude
  one open trace-initial state superseded entirely before the frame window;
  it still requires complete in-window coverage and preserves the evidence.
  `hitchcpu.py --milestones FRAMEPATH_JSON --guest-processes SNAPSHOT_JSON`
  groups samples by installed address space and a same-worker proc/pmap snapshot.
  Add `--guest-kernel KERNEL --guest-cache DYLD_CACHE` for named guest TB PCs.
- `guestprocs.py` is an LLDB command module: `lt-guest-procs` reads the guest's
  proc/task/pmap list through an opt-in loopback QEMU debugger, then detaches.
  Attach after measurement; the debugger pauses the guest. Bind the snapshot
  to its host engine PID before using it for attribution.
- `guestsymbols.py --kernel KERNEL --cache DYLD_CACHE --pc ADDRESS` names local
  ARMv6 Mach-O/dyld v1 cache code ranges, preserving hashes and ambiguous results.
  Keep firmware inputs local. These names do not unwind guest stacks.
- `framepath.py MILESTONES FRAMES --json OUT` joins default-off `LT_FRAME_PATH`
  engine milestones to frontend publications by serial, retaining guest presents,
  LCD flips/ticks and frontend lag for each hitch. Pull milestones after clean Stop.
- `frameresidency.py FRAMEPATH_JSON SCHEDULER_JSON --json OUT` joins optional
  guest address-space markers to vCPU scheduler states during hitches. Generate
  inputs with `framepath.py --residency-intervals` and `hitchsched.py --all-states`
  to compare residency during normal, late and hitch intervals as well.
  Register values do not establish guest process identity or userspace CPU time.
- `guestsyslog.py` (desktop) runs a headless guest and reads its syslog through the
  app's USB bridge, then counts messages by sender: a guest that logs every frame pays
  for launchd and syslogd handling each line (found this way: engine `7f744e80cc`).
- `build-apk.sh ENGINE_DIR NAME` builds an arm64-only debug APK from this
  working tree. Paths are this workstation's; adjust before reuse.

## Manual hitch captures

Use **Start profile / Stop profile** above the Android FPS graph. Each recording saves the
frontmost iOS app name, engine frames, GL totals/outliers, BQL waits/holds and Android presentation
stages. `profile.py --pull build/profiles` retrieves them; `profile.py CAPTURE --chart report.png
--json report.json` reports the longest frames and expensive calls. See
[PROFILING.md](../../docs/PROFILING.md) for capture workflow, file formats and timing limitations.

## Results (n72ap-7E18, boot to lock screen / Settings fling fps)

| Host | Original engine, unpinned | dev/perf, big cores |
| --- | --- | --- |
| Poco F3 (Snapdragon 865-class) | 55 s / 18-22 | 36-40 s / 25-28 |
| Pixel 10 (Tensor G5) | 57-74 s / 15-24 | 19-20 s / 27-29 |
| Ryzen 3700X | 23-24 s / 40-42 | 21 s (19-20 s with LTO) |

In-app on the Poco: v0.0.1 50-52 s, big-core build 39-41 s.

Measured and rejected: a 16K jump cache;
lock-free timer counter reads; isolating the vCPU on the prime core (worse
animation on the Pixel); LTO on Android (no measurable phone gain).
