# Recording hitching during play

With a guest running on Android, **Start profile** appears above the FPS graph. Open the iOS app
and scene you want, press Start profile, play through the hitching, then press **Stop profile**.
The label shows the iOS app's localized name. Start again for another scene or game; each recording
gets its own directory. The app name and bundle ID are queried at the start of each recording.
If the guest agent cannot identify the app, Start reports an error and does not begin recording.
Switch apps between captures so each capture's label describes its contents.

Recording continues in fullscreen. Return to the normal view to stop. It also stops when the guest
worker stops. Captures are limited to 15 minutes or about 128 MiB each. Older captures are retained;
there is no automatic deletion. A killed worker may leave a partial capture with its final buffered
events missing; the analysis tool flags a missing stop footer. Pausing/backgrounding the guest while
recording leaves the pause in the elapsed timeline.

## Retrieve and analyze

For a debug APK, with wireless ADB connected and `ANDROID_SERIAL` set:

```sh
tools/perf/profile.py --pull build/profiles
# Choose one of the timestamped directories under build/profiles/profiles/:
/usr/bin/python3 tools/perf/profile.py build/profiles/profiles/CAPTURE \
    --chart build/profile.png --json build/profile.json
```

Pull after Stop so all buffered files have been flushed. This copies only the profiles, not guest
state or firmware. Captures stay in the app's private `files/profiles/` directory on the phone.
The optional chart uses matplotlib; the text and JSON report need only Python's standard library
(Python 3.12+ for safe tar extraction when pulling).

The report provides ceiling, median, low, 1% low and hitches, a frame-time histogram, a timeline,
GL calls ranked by total wall time, and the longest individual engine and Android presentation
intervals. The JSON retains the full frame samples and details of overlapping slow GL calls and
BQL waits/holds for the longest frames. `--top 30` increases the number of detailed frames/calls.

## What is captured

- `metadata.json`: frontmost iOS app response, localized name, bundle ID, start/end timestamps,
  Android model/build, LightTouch version, worker PID and performance environment switches.
  New captures also include audio underrun, PCM queue/drop and output counters at start/end.
- `engine-*-manifest.json`: the APK's build and engine notice/identity manifests.
- `engine-start.log`: the first 64 KiB of engine setup diagnostics, including graphics initialization.
- `engine.log`: up to 8 MiB of engine diagnostics produced during the capture.
- `events.tsv`: engine frame publications, cumulative vCPU wall/CPU time and BQL waits, GL call
  count/total/max duration per publication interval, and individual GL calls/BQL waits/holds of
  at least 0.5 ms. GL timing covers the host bridge, context selection and driver call.
- `presentation.tsv`: Android post completion timestamps and separate durations for control mutex
  waits, frame copy, native-window buffer acquisition, scaling, frame pacing, posting and commit.

No guest package update is needed. Profiling is activated dynamically by the button; it does not
require `IT_VCPU_ACCT` or `IT_GLES_PROF`, and does not enable per-instruction instrumentation.
`LT_PROFILE=off` (or `0`) disables the engine recording API for comparison. The additional timers and
buffered writes run during a capture, so captures are diagnostic runs, not uninstrumented performance
baselines. Use this recorder for user-selected obvious hitches; ordinary performance
measurements keep Start profile off. Recording performance is not an optimization target.
Use narrow engine counters only for a specific investigation; confirm improvements in
interleaved A/B pairs with all diagnostics off.

For a focused context-switch investigation, `LT_GLES_CONTEXT_PROF=on` (default off) writes
`[gles-context]` totals/maxima to the engine log every five seconds. It separately times initialization,
make-current and dispatch for viewport, framebuffer bind and surface present calls, without requiring
Start profile. `switches` counts changes in the engine's selected context, not driver-internal work.
Dispatch includes strict error checks. Divide summed nanoseconds by `n` for the mean per call.
Engine `e9e13d795e` adds `[gles-context] end begin-ns=... end-ns=...` markers.
`python3 tools/perf/contextstages.py ENGINE_LOG --start-ns BEGIN --end-ns END --json OUT`
selects complete contiguous blocks wholly inside exact frame bounds and preserves all maxima.
This parser has processed real Poco logs; the surfaceless-binding experiment from that engine
was unsupported by its ES1 driver and removed in `2e79c09b2e`.

Capture records now pass through bounded memory queues to background file writers (8 MiB for
engine events, 1 MiB for Android presentation). Storage writes during recording do not hold the
BQL or the JNI lock used by audio reads. If a writer falls behind and fills its queue, recording
fails with an explicit error instead of blocking playback. Presentation output is capped at
16 MiB in addition to the engine's approximate 128 MiB cap. `LT_PROFILE_SYNC=on` restores
synchronous writes for controlled comparisons. Stop drains the queues before reporting Saved.

## Reading the timings

Engine publication and Android post completion are different events. A late engine publication
points toward guest/engine work or main-loop delay. A timely engine frame followed by a long Android
buffer wait points toward presentation/backpressure. Post completion is not physical display scanout.
The engine also retains the intended LCD timestamp, so publication delay can be inspected separately.

Counters overlap: GL runs inside vCPU execution, and GL wall time includes host descheduling and
synchronous driver waits. GL totals are charged when a call finishes; a call crossing a frame boundary
can exceed that frame's duration. Individual slow-call intervals allow overlap to be calculated.
The vCPU snapshots are approximate, sampled from another thread, and can lag by one execution slice.
The text report prints vCPU execution wall time and CPU time separately. Their difference can include
host descheduling or blocking inside execution; it does not identify a cause by itself.
BQL wait intervals are stronger evidence than a lock hold alone; correlate their timestamps.
Static screens and game pauses also produce long gaps; interpret them using the scene you recorded.
Outside-vCPU time includes guest idle, queued work and waits; it should not all be called scheduler
stall time. These captures do not identify every guest function or measure GPU execution time.
Use an attached simpleperf call-graph profile for a CPU-heavy scene, or a supported GPU/system trace
when the remaining question is asynchronous GPU work or precise host scheduling.

## Scheduler and CPU samples aligned to individual frame gaps

For an unresolved hitch, first separate execution from sleeping and runnable
delay using a system scheduler trace. Keep Start profile and engine accounting
off. `tools/perf/hitch-sched.pbtxt` is the 65 s configuration used on the Poco:
64 MiB trace buffer, scheduler/wakeup/blocking events, thread names and CPU
frequency events/counters. Check for stray qemu bench engines before launching.
Use the ordinary `LT_FRAME_LOG` publication log and bracket a 60 s frame window
with the trace. For example, while the game is already running:

```sh
adb shell perfetto --txt -c - \
    -o /data/misc/perfetto-traces/lighttouch-hitch.pftrace \
    < tools/perf/hitch-sched.pbtxt
adb pull /data/misc/perfetto-traces/lighttouch-hitch.pftrace build/hitch.pftrace
# Run framestats.py --device 60 --save build/hitch.frames.log concurrently,
# starting about two seconds after the trace starts; retain its exact output.
```

Analyze offline with an official Perfetto `trace_processor_shell` binary:

```sh
/usr/bin/python3 tools/perf/hitchsched.py build/hitch.pftrace build/hitch.frames.log \
    --engine-pid PID --trace-processor /path/to/trace_processor_shell \
    --frequency --json build/hitch-sched.json --chart build/hitch-sched.png
```

The tool rejects trace error/data-loss counters, overlapping/incomplete vCPU
states and incomplete state coverage. It aligns MONOTONIC clock snapshots with
trace time and rejects offset spread over 1 ms. Keep the phone awake: startup
snapshots alone cannot rule out a later suspend. Default thread selection is the
busiest `CPU 0/TCG` in the engine process; driver workers can inherit the same
name. Check the reported TID; `--vcpu-tid` overrides selection. JSON preserves
exact gap intervals, scheduler segments and wakeup metadata. It also separates
sequence-gap publications from ordinary/late/hitch frames. Frequencies are
reported clock values, not measured effective clock or retired cycles.

One real trace contained an open initial R row superseded by a complete S
state entirely before the frame window. Default analysis rejects it. Only for
that situation, `--allow-superseded-initial-state` excludes the first row while
preserving it, its successor and the inferred outside-window end in JSON.
Complete coverage and nonoverlap within the measured window remain required;
this option does not repair missing in-window scheduler data.

If the vCPU is running during the hitch, capture timestamped CPU samples in the
same trace window. Enable `QEMU_PERFMAP_DIR` before launching that worker and
retain its before/after maps as described below. Attach under run-as:

```sh
adb shell run-as org.lighttouch.android simpleperf record \
    -p PID -m 8 --cpu-percent 25 -e cpu-clock --clockid monotonic \
    -f 1000 --duration 65 --call-graph fp -o files/perf-hitch.data
```

Retain simpleperf's recorder output, including sample/loss counts, pull the data
and both maps before stopping the worker, and use the exact frozen build's
unstripped engine library. Then:

```sh
python3 tools/perf/hitchcpu.py build/perf.data build/hitch.frames.log build/hitch-sched.json \
    --before-map build/perf-PID-before.map --after-map build/perf-PID-after.map \
    --engine-symbols build/symbols/libqemu-arm.so --build-identity build/identity.json \
    --nm /path/to/llvm-nm --json build/hitch-cpu.json
```

`identity.json` must contain `library_sha256` from the frozen build; the operator
must retain the APK/build identity and confirm that this is the captured worker's
build. The tool checks that hash and both map PIDs, requires `cpu-clock` and an
explicit monotonic clock, and selects the exact vCPU TID from the scheduler
report. It compares self-sample distributions across <=20 ms, late and hitch
intervals, retaining individual-frame results and overlapping inclusive native
chains. Anonymous PCs require identical unambiguous guest mappings in both maps;
unstable/unmapped mappings stay unattributed. Guest address regions do not
identify processes or unwind guest stacks. At 1 kHz a 50 ms gap has only roughly
40 running samples: preserve counts and avoid declaring a culprit from a few
samples. Running state also does not prove that thread is the frame path's
critical dependency. Correlate guest-present/LCD/publication milestones next
when execution samples remain spread thin.

Both tools processed the real Poco captures recorded in PERFORMANCE.md. These
are focused diagnostic captures, not FPS comparisons. Confirm any resulting
optimization in interleaved A/B pairs with all diagnostics off. Pull first, then
remove only this capture's phone trace/perf data/maps, stop cleanly, check for
stray engines and restore the baseline environment.

### Identify guest processes and code

#### Follow the delay across frame boundaries

When the vCPU samples are spread across many functions, follow the delayed
game thread backward from its framebuffer flip. Retain at least the preceding
two frame intervals: an audio burst or enqueue-to-scheduled delay can begin
before the long publication interval. Compare actual guest-present, LCD tick,
flip, engine publication and Android post timestamps first. Then split each
relevant interval into host running/runnable/sleeping states and the selected
guest thread. A pmap alone does not identify the selected thread.

For a guest scheduling lead, pair an observed enqueue request with the next
scheduled-thread boundary and identify the intervening work. These intervals
include enqueue/dispatch processing; do not label them exact queue residence.
An overlap or large CPU share alone does not establish causation. Compare
nearby ordinary frames, preserve decoder-free gaps, and confirm any resulting
change with diagnostics off. Existing Poco examples and their limitations are
recorded in PERFORMANCE.md; they do not explain the earlier Pixel captures.

#### Choose the next capture from the unexplained time

Use the longest confirmed gameplay gap to choose instrumentation. Keep loading,
menus and ordinary gameplay labeled separately. Broad CPU or GL totals can
nominate work without explaining why a particular frame missed its deadline.

| Observation in the delayed interval | Next evidence |
| --- | --- |
| Long vCPU execution wall time but much less vCPU CPU time | Align a Perfetto scheduler trace with the frame log. Split runnable delay from sleeping/blocked time before choosing a CPU optimization. |
| Mostly host running time, game thread displaced | Observe guest enqueue and scheduled-thread boundaries; identify the intervening guest threads and calls. Include earlier frame intervals. |
| Game finishes presenting promptly but the flip/publication is late | Follow the compositor and framebuffer flip, then LCD publication and Android posting. Nearby presents alone do not prove which surface a flip needed. |
| A long individual GL or present operation | Sample its native call stack and host scheduling interval; distinguish driver CPU work from blocking. |

For example, the existing Pixel Jetpack 214.5 ms gap has approximately
205.9 ms inside vCPU execution but only 83.2 ms of vCPU CPU time, with 3.2 ms
of completed GL work. The roughly 123 ms difference nominates host scheduling
or blocking, not a particular GL call or guest function. Counter snapshots can
lag an execution slice; use the aligned scheduler trace to resolve the interval.
No matching scheduler trace was captured in that manual recording, so its
cause cannot be recovered from the existing counters alone.

For long runnable delays, add `--runnable-details` to `hitchsched.py`.
It examines in-hitch runnable intervals of at least1ms (`--runnable-min-ms`
changes the threshold), retaining wake events and their target CPU separately
from the next-running CPU. Destination scheduler slices distinguish another
task from the idle task; raw CPU-idle events are preserved. Missing target or
occupancy stays explicit. Selected intervals are not all-frame statistics.
An idle task can execute interrupt handlers, so idle occupancy alone does not
prove hardware sleep or idle-exit latency. If this pattern repeats, capture
IRQ/softirq/IPI activity alongside wake/idle events. The diagnostic-only
`tools/perf/hitch-wake.pbtxt` adds these events with a256MiB buffer; check event
availability and reject any trace loss on the target phone. Existing Poco analysis
and read-only cpuidle latency observations are recorded in PERFORMANCE.md;
A subsequent interrupt-aware capture confirms that recorded handlers explain
only0.014ms of16.215ms idle occupancy in selected hitch waits. Exact wake/idle
mechanism and Pixel behavior remain unproven. This offline analysis adds no
runtime instrumentation; the expanded trace is a separate diagnostic.

The scheduler report retains waker identities and blocked functions when the
kernel supplies them. A null blocked function is missing evidence; a waker name
does not identify the lock owner or prove why the thread went to sleep. If the
trace establishes blocking but cannot name the wait, narrow the next diagnostic
to that interval and capture the relevant native wait entry/return stack.
Keep Start profile off during ordinary A/B performance measurements.

### Native wait source locations

`LT_NATIVE_WAIT_PROF=on` (engine `bd8af24c10`, default off) requires
`LT_FRAME_PATH=/private/path/events.tsv`. It interposes the existing QEMU BQL /
ordinary mutex and condition-wait function pointers after the frame buffer is
ready. With it off, those pointers remain unchanged. Only vCPU0 is timed;
completed calls >=10us produce one `native-wait` record: a=begin_ns,b=end_ns,
c=site ID. The record timestamp is after completion. Source file/line, wait
kind, object and associated mutex addresses are printed as `[native-wait-site]`
only after vCPUs stop. Condition durations include mutex reacquisition.

This allocates the80MiB bounded frame buffer; it does not enable context or
instruction sampling. Existing QSP/interposed wait hooks cause refusal.
`[native-wait] finished` records the vCPU TID, total/retained calls, site overflow
and whether hooks remained installed. Reject missing completion, site/buffer
loss, replaced hooks or a mismatched TID. It clocks every selected QEMU wait
call, so it is for attribution only. It does not cover direct driver/libc waits,
recursive-mutex entry points or QemuEvent's direct futex waits. Short calls and
incomplete calls are not represented as spans. Keep it and Start profile off
for ordinary A/B measurements. Linux/Android builds pass; the first complete phone capture mapped57 selected
long runnable intervals to BQL acquisition and11 to the guest idle condition,
with26 unmatched by the wrappers. All26 are enclosed by host surface presents;
the analyzer retains those context/target spans too. Outer present markers do
not name the exact driver operation. Full limitations in PERFORMANCE.md; no
speedup claim.

After a clean Stop and a matching scheduler analysis:

```sh
python3 tools/perf/nativewaits.py events.tsv engine.log hitch-wake.json \
    --engine-pid PID --json native-waits.json
```

Use `hitchsched.py --all-states --runnable-details` first. The tool validates
completed spans/sites and matches selected in-hitch runnable intervals to
enclosing native calls, retaining unmatched intervals. Durations include
scheduling; a slow mutex call need not have slept, and a source location alone
does not prove why the kernel delayed a wake. Preserve the exact APK identity,
scenes, full frame window and scheduler trace.

`LT_VCPU_DISPLAY_PRIORITY=on` (engine `6971278387`, default off) is a separate
Android scheduling experiment. At vCPU startup it requests ordinary display
nice -4 on that thread, retaining CFS and the existing CPU mask; an existing
higher priority is preserved. `[vcpu-priority]` records before/after nice values
and get/set errors in both modes. Confirm the effective priority during the
frame window, not just the requested switch. Failed requests supply no priority
A/B. This cannot directly remove guest decoder work or identify a sleeping
thread's wait. Keep other optimization switches fixed and use one frozen APK.
Retain the engine's complete thread/nice listing too: driver workers can inherit
the vCPU name and newly created threads can inherit its nice value. Select the
active vCPU by CPU time and confirm its TID and policy at both window boundaries.

#### Map the same worker

Address-space values can change between boots. Use a snapshot from the same
worker before identifying an installed page table as a guest process.
Engine `6d87d27dc8` provides default-off `LT_GUEST_GDB_PORT=PORT` (1024–65535),
binding its existing guest debugger to `127.0.0.1` only. Set it before launch,
but attach **after** all timed capture windows: attaching pauses the guest.
Forward the selected port over ADB, then use LLDB with the matching unslid
ARMv6 kernel and the engine's existing XNU helper:

```text
command script import /path/to/LightTouch/tools/perf/guestprocs.py
lt-guest-procs --kernel /local/kernel.macho --xnu /path/to/qemu-ios/imgtools/lldb/xnu.py --connect connect://127.0.0.1:43101 --json /new/processes.json
```

For example, `adb forward tcp:43101 tcp:43100` reaches a worker started with
`LT_GUEST_GDB_PORT=43100`. LLDB may run on another host through an SSH tunnel.
`guestprocs.py` derives proc/task/pmap offsets from kernel accessors, checks
active/kernel translation registers, records input hashes, and attempts detach
even on failure. Require `detached:true` before Stop; preserve the command log.
The guest cannot supply the Android host PID: add `engine_pid` to the JSON
from the capture's retained worker identity, recording how it was bound.
Remove forwards and the debugger environment setting after clean Stop.

For selected guest thread addresses observed in that same worker, append
repeatable `--thread 0xADDRESS` arguments to `lt-guest-procs`. The helper reads
`thread->task` using the matching kernel accessor's offset, reads that task's
BSD process pointer, and checks it against the process/task snapshot. The JSON
retains raw pointers, owner candidates and an explicit match/unreadable/mismatch
status. A matched task owner is stronger evidence than the currently installed
pmap, which a kernel thread may borrow. Ownership is observed at snapshot time;
applying it to earlier events requires that thread to have remained alive.
Keep unknown and mismatched rows. The optional lookup and enumeration below
were exercised on the Poco in `poco-guest-bluetooth-1`:139live threads across
19tasks all matched, with current-thread membership and detach confirmed.

To enumerate live threads when their addresses are not known before attachment,
append `--task-thread-layout HEAD,COUNT,NEXT` and
`--task-thread-kernel-sha256 HASH`. Supply byte offsets in decimal or hex from
the matching kernel's `task_threads` disassembly: task's first thread, task's
thread count, and thread's next link. There are no default offsets. The helper
rejects a kernel hash mismatch before connecting, bounds enumeration to 4096
threads, rejects repeated/unreadable pointers and task-owner disagreements,
and requires the current thread in the resulting lists. JSON retains the
layout, each task's list and every thread's owner lookup. These are post-window
observations; exited/reused threads remain a limitation when joining earlier
events. A successful post-window enumeration does not validate earlier owners
from another worker or recover threads that exited before attachment.

#### Time NAND operations within host sleeps

Engine `bb5132a264` adds default-off `LT_FMSS_PATH=on`, requiring `LT_FRAME_PATH`.
The trace uses the existing40MiB diagnostic capacity and deferred output after
clean Stop. `fmss-begin`/`fmss-end` fields are: call ID; operation in the upper
32bits plus chip select in the lower32bits; page number (block for erase).
Operations1/2/3/4 are load/store/erase/fsync. Fsync spans are nested in their
store/erase; do not add overlapping durations. End means the call returned,
not that I/O succeeded. Require balanced IDs/coordinates, the full trace footer
and no I/O errors before joining calls to scheduler D/S/R/running intervals.
Off takes no diagnostic clocks; flush/sync/rename/error behavior is preserved.
Linux/Android builds and a Poco capture completed; PERFORMANCE.md records the
no-write/no-D frame window separately from boot/post-capture persistence.
After producing `hitchsched.py --all-states` JSON, analyze the same worker:

```sh
python3 tools/perf/fmsspath.py fmss-path.tsv hitches.json --engine-pid PID --json nand.json
```

The tool requires matching worker identity, balanced nested IDs/coordinates and
a lossless trace. It retains calls outside the frame window, per-operation
inclusive/exclusive scheduler states and enclosing calls for>=1ms hitchD spans.
This tests a storage lead; a D-state interval alone does not identify storage
or page faults. Keep guest PC/context/native/
user profiling off unless independently required, and confirm any later
optimization in ordinary interleaved comparisons.

Generate framepath JSON with `--residency-intervals` and context tracing, then
add these options to the `hitchcpu.py` command above:

```sh
    --milestones build/milestones.json --guest-processes build/processes.json \
    --guest-kernel /local/kernel.macho --guest-cache /local/dyld_shared_cache_armv6
```

The tool checks matching windows/engine PID/detach and the snapshot's kernel
hash. It groups samples by raw CIDR/TTBR0 pairs, retaining transitional values;
samples within 10 µs of register changes form a separate boundary group.
Process matches use page-table alignment from the same snapshot. Residency
includes kernel and native work under that pmap, and is not guest-thread or
userspace-only accounting. Process exit/page-table reuse can invalidate a
post-capture match; keep captures short and preserve unknown keys.

`guestsymbols.py` reads code-section symbols and indirect stubs from the local
kernel and ARMv6 dyld v1 cache, uses canonical dylib names rather than aliases,
and excludes ambiguous overlapping ranges. `--kernel KERNEL --cache CACHE
--pc 0xADDRESS` resolves an individual address; `--json OUT` exports ranges
and hashes. Both guest inputs are required for hitchcpu naming. Symbol extents
are approximated to the next symbol/section boundary. These are sampled TB
starting PCs, not exact sampled guest instructions or unwound guest stacks.
Keep firmware/symbol inputs private. Both tools have processed the real Poco
captures documented in PERFORMANCE.md; sparse hitch counts remain leads.

### Focused audio arithmetic and alignment paths

For the host-PC tree lookup within architectural fault restoration,
`LT_HOST_PC_CACHE_PROF=on` samples one lookup body per256 calls and reports
five-second blocks. `LT_HOST_PC_CACHE=on` separately enables the default-off
exact-address cache. Use one APK in both modes and keep Start profile off.
The long STM benchmark gives enough complete blocks for direct timing:

```sh
python3 tools/perf/hostpclookup.py ENGINE_LOG --json lookup.json
```

The parser checks mode/count/time bounds and contiguous per-region blocks,
selects one completed LT Bench pass, and conservatively excludes its first
block per region because its beginning can precede the start marker. All
blocks and sampled maxima are retained. Its mean includes clock overhead and
descheduling, excludes the region mutex acquisition, and is not a distribution
of every call. The complete benchmark timing includes diagnostic bookkeeping;
confirm gains again with this diagnostic off before a game comparison.

`LT_HFLAGS_V6_FAST=on` selects a separate, guarded pre-v7 flag calculation.
`LT_HFLAGS_V6_VERIFY=on` additionally compares both result words against the
generic builder on every eligible rebuild and asserts on a mismatch. Its
`[hflags-v6] verified=N` lines report every 1,048,576 comparisons, so the last
line is a lower bound on comparisons completed. Unsupported CPU configurations
use the generic builder. A completed capture covers only the states executed
there; it does not establish equivalence for every possible register value.
Keep verification off for timing and use one frozen APK for both fast modes.
Both switches default off. See PERFORMANCE.md for build identities and the
completed desktop boot capture.

`LT_AUDIO_PATH_PROF=on` (engine `6743f6a81b`, default off) selects diagnostic
helpers during AArch32 VFP translation. Keep Start profile off. Enable
`LT_FRAME_PATH` and `LT_FRAME_PATH_CONTEXT` to retain the results. CPU 0 samples
each f32 add/sub/mul every 1021 calls, recording raw operands/results, pre/post
exception flags, rounding/flush settings, read-only unwound instruction PC, and
the original operation's wall duration. PC-relative translations are resolved
using the same page base as architectural restore, without modifying guest state.
`0xffffffff` means PC unwind failed. Short-vector operations produce one call
per element; compound VMLA/VMLS forms retain their separate arithmetic steps.
This diagnostic selects original arithmetic even if the separate zero-multiply
experiment is enabled. Use it with that experiment off.

Every CPU 0 AArch32 alignment fault also records its restored instruction PC,
faulting virtual address, access type and MMU index. The architectural fault and
guest handler still run. Ordinary arithmetic uses the original helpers when
the switch is off. Half/double precision and other translators are not sampled.
The deferred milestone buffer remains bounded: reject any dropped records.
Engine `6b9bca678a` uses 2,097,152 entries (80 MiB) only for this diagnostic,
enough for boot plus a minute of fault bursts in the completed Poco capture.
`vfp-sample` packs pre-call metadata/PC in `a`, two operands in `b`, duration
in `c`; the following `vfp-result` has PC, post-flags/result and measured begin
time. Both records are appended after the measured call. The analyzer uses
that explicit interval, not the later append timestamps. Alignment records
contain PC, virtual address and access-type/MMU index.

After clean Stop, process the same-worker snapshot and complete milestone trace:

```sh
python3 tools/perf/framepath.py events.tsv frames.log --residency-intervals --json milestones.json
python3 tools/perf/audiopath.py events.tsv milestones.json --engine-pid PID \
    --guest-processes processes.json --guest-kernel /local/kernel.macho \
    --guest-cache /local/dyld_shared_cache_armv6 --json audio.json
```

Both symbol inputs are optional together. The analyzer verifies the milestone
context timeline against the raw trace, retains individual samples/faults and
compares normal/late/hitch frame classes by installed page table. It reports
status/input hardfloat eligibility and tiny-result candidates; these classify
observations and do not count actual internal softfloat branches. Timings
include clocks and host descheduling. Sampling is systematic, so preserve
sample counts and do not treat samples × 1021 as exact per-PC call counts or
extrapolate these durations into a proven whole-game saving. Use the scheduler
trace to distinguish descheduling in a slow sampled call.
The first completed Poco capture identifies fault bursts in mediaserverd's
`convertScaledFloatToS16` alongside AAC transform work, without exceptional
FP operands or a large per-call arithmetic delay. See PERFORMANCE.md for
counts/timings and diagnostic limits; this is not a speedup or justification
to bypass architectural faults.

### Alignment faults without floating-point instrumentation

Engine `c5cc5be3b9` adds default-off `LT_ALIGNMENT_PATH_PROF=on` for alignment
markers alone. Keep `LT_AUDIO_PATH_PROF=off`: generated arithmetic then uses
the ordinary helpers, with no VFP sampling counters. Use the same context,
frame and scheduler capture/process snapshot workflow above. `audiopath.py`
can analyze a trace containing faults and zero VFP samples.

`LT_ALIGNMENT_PATH_PROF=returns` additionally samples one in 257 CPU 0 EL0
ARM-state alignment faults and brackets the architectural handler. Each
`alignment-begin` stores a unique ID, privileged thread register (low two bits
masked) plus fault PC, and raw TTBR0. A translation-selected exception-return
wrapper first performs the original CPSR/PC/hflags update, then matches thread,
TTBR0, CIDR, ARM state and exact PC+4. Its `alignment-return` has the same ID,
thread/return PC and TTBR0. A bounded 64-entry pending table allows guest
context switches; failed returns/new faults/table exhaustion emit explicit
`alignment-abandon` reasons 2/1/3. Thumb faults remain counted but untimed.
No pending table or return wrapper runs with the switch off or in fault-only mode.

```sh
python3 tools/perf/alignmentlatency.py events.tsv audio.json milestones.json scheduler.json \
    --json alignment-latency.json
```

Generate `audio.json` with `audiopath.py`, milestones with
`framepath.py --residency-intervals`, and scheduler JSON with
`hitchsched.py --all-states` from this same worker/window. The latency analyzer
checks hashes/identities, preserves unmatched/outside/cross-frame samples, and
splits observed durations into target-pmap execution, all execution, runnable
and sleeping time. The interval begins **after** restoring fault state and
ends after the original exception-return helper: it excludes the triggering
memory probe/state unwind and includes diagnostic/wait time. Target-pmap time
can still include other guest threads; a pmap is not a thread execution trace.
Fault-only mode has reproduced the conversion bursts with zero VFP counters:
22/25 hitches overlap faults in one 60 s Poco capture; see PERFORMANCE.md for
the exact window, scheduler split and limitations. Return timing is also
confirmed on real data: 1665/1665 pairs match, 916 inside the timed window;
normal/hitch median3.125/3.178 us. No abandoned/pending pairs or sampled
millisecond-scale fault. These are diagnostics, not optimizations.

New return-mode captures also append `alignment-args` (a=sample ID,
b=R0<<32|R1, c=R2<<32|R3) and `alignment-caller` (a=ID, b=LR<<32|SP,
c=raw CIDR) immediately after the sampled beginning. These read the already
restored fault state without a debugger or guest memory access. Registers are
at the faulting instruction, **not** at function entry. The analyzer validates
and retains paired snapshots; older captures have zero snapshots. This helps
identify callers and buffer cursors after the entry-breakpoint attempt yielded
no samples. New snapshot Linux/Android builds pass; device capture pending.

## Sparse frame milestones

`LT_FRAME_PATH=/absolute/host/path.tsv` (engine `78eaa87497`, default off)
records a bounded 131,072-entry memory trace independently of Start profile.
Keep the user recorder, full accounting and other diagnostics off when answering
a frame-path question. No guest package update is needed. The embedded entry
allocates about 5 MiB at startup and writes the records **after clean engine
shutdown**, once vCPU/device activity has stopped. Producers only timestamp and
reserve a record; they perform no file writes, allocation or mutex acquisition.
Tracing is diagnostic work and can still affect scheduling/cache behavior.

Pull the file after Stop, retaining the matching `LT_FRAME_LOG` frontend log,
engine startup/completion log and frozen APK identity. A killed worker leaves a
header without a completion footer; a full buffer counts dropped events instead
of overwriting earlier records. Neither is a complete capture. The engine log
reports final counts and write errors. `framepath.py` rejects dropped/incomplete
records or a missing footer:

```sh
python3 tools/perf/framepath.py frame-path.tsv frames.log --json frame-path.json
```

Each line is `monotonic_ns event a b c`. Events are:

| Event | a | b | c |
| --- | --- | --- | --- |
| `present-begin` | guest GL context | destination surface base | width << 32 \| height |
| `present-end` | same context | same base | return code (negative encoded as uint64) |
| `lcd-flip` | old framebuffer register | new framebuffer register | 0 |
| `vsync-begin` | previous scanout base | pending framebuffer register | virtual deadline lateness, ns |
| `vsync-end` | latched scanout base | 0 | 0 |
| `scan-begin/end` | latched scanout base | 1 for vsync scan, 0 for display poll | 0 |
| `capture-begin/same` | current engine serial | 0 | 0 |
| `publish` | new engine serial | intended LCD timestamp, ns | 0 |

`present` covers the GL bridge's explicit surface-present operation. It does not
trace every draw, guest timer callback or other legacy presentation path.
The analyzer joins engine and frontend publication by serial and preserves each
hitch's engine gap, frontend lag, all overlapping milestones, and nearby presents
per context/target. Present destinations and LCD scanout bases can differ; don't
assume they identify one surface. Unchanged captures can be suppressed. These
timestamps locate cadence changes but do not alone prove a causal dependency.
Use the scheduler trace alongside them when a thread ran late.

Engine `c0347336e6` stamps publication inside the existing frame-copy lock,
before unlock. Earlier builds emitted the marker after unlock and can therefore
produce a frontend observation earlier than its marker if that producer is
descheduled. The analyzer rejects this ordering by default. For an existing
legacy capture only, `--allow-late-publication-markers` reports and preserves
each anomaly, and still rejects any hitch with an affected boundary. Do not
edit raw timestamps or treat old markers as exact publication linearization
times. One such anomaly in `poco-frame-context-1` is outside every hitch.

Engine `5511f5466d` adds optional `LT_FRAME_PATH_CONTEXT=on` alongside the path.
It records changed AArch32 CONTEXTIDR and TTBR0 values, selecting the active
secure/nonsecure bank. This mode raises the bounded allocation to 1,048,576
records (about 40 MiB); without it the original 131,072-record capacity remains.
`context-switch` records new CONTEXTIDR, active TTBR0 and old CONTEXTIDR;
`ttbr0` records active CONTEXTIDR, new TTBR0 and old TTBR0. Timestamps follow the
register update and any associated TLB flush. This is not per-instruction
accounting or a guest thread-switch trace. The active address space can remain
unchanged during kernel execution and host bridge/driver calls.

With the same capture processed by both `framepath.py` and `hitchsched.py`:

```sh
python3 tools/perf/frameresidency.py frame-path.json hitch-sched.json \
    --json residency.json
```

The tool joins actual vCPU running/runnable/sleeping segments to address-space
intervals inside each hitch and the portion between the latest completed present
and flip. That present is a temporal neighbor, not a proven dependency.
Present-call context summaries list their observed address-space keys. Do not
label these as process names or mistake residency for userspace-only CPU time.
Kernel work and native work can execute with a game's address space installed.
To compare hitches against ordinary frames in the same captured window, retain
the complete timelines during offline analysis:

```sh
python3 tools/perf/framepath.py frame-path.tsv frames.log \
    --residency-intervals --json frame-path.json
python3 tools/perf/hitchsched.py trace.pftrace frames.log --engine-pid PID \
    --trace-processor /path/to/trace_processor_shell --all-states --json scheduler.json
python3 tools/perf/frameresidency.py frame-path.json scheduler.json --json residency.json
```

Classes match the scheduler analyzer: normal ≤20 ms, late >20 to ≤50 ms,
hitch >50 ms (or its configured threshold), with skipped publications separate.
Intervals use frontend timestamps; whole-window output ends at the last observed
frame, without inventing a final frame at the requested window end. Compare
running-time shares as well as milliseconds per frame: hitches last longer.
This reuses existing traces and adds no phone instrumentation.

Markers do not capture TTBR1, changes of security state, migration restore,
AArch64 regimes or every translation-control register; this diagnostic is for
the single-vCPU ARMv6 iPod workload. Run only a narrow capture and restore both
variables afterward.

### Join presents to physical scanout buffers

Engine `2898270eb9` adds default-off `LT_GLES_SURFACE_PATH=on`, requiring
`LT_FRAME_PATH`. This focused diagnostic resolves every page in the present's
destination span immediately before the call. It performs debug translations
without faulting pages in or changing the present's original permission/fault
handling. Only a successful present with a completely contiguous mapping is
eligible for a physical-buffer association. Invalid, unmapped, overflowing or
noncontiguous spans remain unknown; the span is bounded to 16 MiB. Stride padding
is included, so even an unresolved padding page makes the observation unknown.
With the switch off there are no diagnostic page walks or clock reads.

This selects the existing 1,048,576-record capacity (about 40 MiB), with deferred
output after clean Stop. Keep user recording and unrelated diagnostics off;
the page walks and timestamps add work and their overhead is not established.
No guest tools update is required.

| Event | a | b | c |
| --- | --- | --- | --- |
| `surface-map-begin` | GL context | guest virtual destination | span bytes, 0 for invalid dimensions/format |
| `surface-map-end` | same context | same virtual destination | physical base, or UINT64_MAX for unknown |

`tools/perf/surfacemap.py` validates the complete trace and consumes each mapping
for the immediately following matching present. It retains calls and flips
before/after the selected window, joins successful writes to the **new** LCD
register (`lcd-flip.b`), and reports subsequent latching and repeated scanouts.
The explicit mode below is only valid for n72's physical LCD registers. Other
boards can have an IOMMU or virtual register addresses and need a different join.

```sh
python3 tools/perf/surfacemap.py surface-map.tsv frames.log \
    --lcd-address-mode n72-physical --hitch-ms 30 \
    --scheduler scheduler.json --engine-pid PID --json surfaces.json
```

Use a matching `hitchsched.py --all-states` report for the optional scheduler
arguments. The analyzer splits write-completion-to-flip and entire flip cycles
into host states, clipping scheduler coverage explicitly at the window boundary.
It preserves unknown mappings, initial uses without prior history, multiple
writes and writes crossing the previous selection. A fresh association requires
a completed observed write after that buffer's previous selection. Repeated
scanout ticks distinguish a write completed before the tick from one completed
later. This supplies buffer identity, not pixel-generation identity: other guest,
CPU and DMA writers are untraced. A completed write before a tick does not alone
prove that frame was eligible for display. Follow the responsible guest handoff
before changing scheduling or synchronization.

## Lock diagnostics without instruction accounting

`LT_BQL_PROF=on` (engine `f64fb44e3c`, default off) enables the existing cumulative
`[acct]` vCPU lock totals, per-region/call-site waits, wait/hold histograms and main-loop
callback timings every five seconds. It also times vCPU execution/halt/queued-work windows,
but leaves `it_acct_on` false unless `IT_VCPU_ACCT` is separately set. Guest instruction,
TB lookup, context-switch and TLB-fill counters therefore stay disabled and their printed
zeros must not be interpreted as measured rates. `[acct-mode] full=off bql=on` identifies
this mode. Leave `IT_VCPU_ACCT` unset and the Android `files/debug-vcpu-accounting`
marker absent for BQL-only captures. No manual recording is needed. Two same-APK
Poco Jetpack off/on pairs, second reversed, measured **53.32 → 50.18 fps (−5.9%)**;
hitches over 50 ms rose 8 → 46 (418.8 → 3358.1 ms / 60 s per mode). This mode
still materially perturbs pacing. Full metrics/settings are in PERFORMANCE.md.

Engine `f9d7faec8c` scopes MMIO region attribution to each dispatch, restoring the previous
region when a lockless callback never takes the BQL and clearing bypassed scopes after a
vCPU longjmp. Previously a counter read could leave a stale region for a later interrupt
lock. Waits include mutex acquisition overhead and descheduling. Existing holder spans
can include a condition wait that temporarily releases the BQL; callback timings can
include sections that unlock it. Correlate actual vCPU waits before attributing stalls
to either table. This mode does not correct those existing hold/callback limitations.

New dumps include exact cumulative `bounds` and an `end-ns` completion marker. Summarize
two snapshots (seconds since accounting began):

```sh
python3 tools/perf/bqlsites.py engine.log 90 120 --json bql-sites.json
```

The tool prints the snapshots actually selected and divides by their actual interval;
it previously divided by the requested interval even when different snapshots were
selected. New logs use exact nanosecond bounds and omit incomplete dumps; older logs
use their rounded timestamps. JSON retains the selected bounds and all sites. BQL
diagnostics remain off for ordinary optimization FPS comparisons. The completed captures
put actual vCPU waits at 9.08/8.74% wall, dominated by interrupt handling at
6.09/5.84%; these are instrumented observations, not uninstrumented cost estimates.

## Focused indirect miss-helper cache

`LT_IBTC_L2=on` enables a default-off, site-keyed cache reached after generated
two-entry site misses (engine `6a71c1842f`). It allocates 384 KiB on the current
64-bit builds and leaves TB layout/generated code unchanged. Only single-vCPU
machines with maximum vCPUs also one can enable it. Full instruction accounting,
breakpoints and execution logging use the original lookup. Every hit checks the
source site's address plus full generated key; invalidation generation changes
prevent old entries from matching. Only `CF_PCREL` targets populate the table:
that invalidation path always renews the generation; the non-PCREL path need not.
ARM always selects PC-relative TBs. The generated key alone is insufficient.

Independent `LT_IBTC_L2_PROF=on` logs CPU-0 `[ibtc-l2] on=MODE` blocks with exact
`begin-ns`/`end-ns`, attempted `calls`, `hits`, completed `samples`, summed `ns`
and `max-ns`, plus `hit-samples`/`hit-ns` and `miss-samples`/`miss-ns` subsets.
One in 256 attempted helper calls is timed. Longjmps bypass sample completion;
the attempt counter is not decremented. Timings include clock overhead and host
descheduling. Blocks are emitted after at least five seconds, checked every 1024
attempts, with explicit contiguous boundaries. Select complete blocks wholly
inside each exact frame window and preserve outliers. The diagnostic does not
enable full accounting, BQL tables or the manual recorder.

Compare hit rate and direct helper costs in interleaved off/on pairs with profiling
on in both, then use separate diagnostic-off ordinary FPS pairs. Do not extrapolate
sample timings to whole-game savings or treat diagnostic FPS as an ordinary baseline.
Both focused pairs complete, second reversed: hit rate 67.166/66.670%, sampled
mean 526.62/522.40 → 497.65/500.65 ns (−5.5/4.2%). Enabled hit/fallback means
438.86/618.30 and 448.13/604.52 ns. Aggregate diagnostic FPS 51.26 → 52.59;
hitches 34 → 14 (2275.9 → 761.4 ms/70 s per mode). All four title scenes reviewed,
recorder off, zero skips. Three ordinary profiler-off 30 s pairs now complete,
including reverse order: 52.39 → 53.53 fps (+2.2%); hitches 28 → 21,
2046.9 → 1303.1 ms/90 s. All six scenes reviewed, zero skips, cleanup confirmed.
Keep default off pending longer independent confirmation. Exact per-arm metrics
and windows in PERFORMANCE.md; no steady 60 or eliminated 1% low frames claimed.

## Focused GL presentation stages

For exact Android mapped-buffer wait attribution, engine `669353072a` adds
default-off `LT_GLES_AHB_PATH=on` together with `LT_FRAME_PATH=PATH`.
It appends begin/end markers for `glFinish`, `AHardwareBuffer_describe`,
`AHardwareBuffer_lock` and `AHardwareBuffer_unlock`, retaining buffer identity
and returned map/unmap status. GPU completion and pixel readiness are preserved.
There are no added clock reads with this switch off. Enabled producers append
only to the bounded memory trace; disk output waits until clean Stop. This
mode reserves 40 MiB for milestones (80 MiB if native/audio/guest-PC tracing
also requests it). A failed or overflowing trace must be discarded.

Run `hitchsched.py --all-states --runnable-details` for the same worker and
frame window, then:

```sh
python3 tools/perf/ahbpath.py MILESTONES ENGINE_LOG SCHEDULER_JSON \
    --engine-pid PID --json OUT
```

The analyzer rejects incomplete/lost stages and missing scheduler coverage,
retains input hashes and per-operation running/sleeping/runnable time, and
matches selected long in-hitch runnable intervals to their enclosing operation.
It also retains stages clipped to each long frame. Wall times include clock
overhead and descheduling. Enclosure locates a wait; it does not prove a driver
defect. Keep this diagnostic and the user recorder off for ordinary benchmarks.
Linux/Android builds pass; first real capture/analyzer complete. See PERFORMANCE.md:
26 of202 selected long runnable intervals in >30ms gaps are in glFinish/map;
the other176 are outside surface presents. These remain diagnostic thresholds,
not a change to ordinary >50ms hitch reporting. Remaining waits need attribution.

`LT_NATIVE_WAIT_PROF=conditions` (engine `77a6f34c02`) can coexist with the
BQL-spin experiment and these stages. It instruments only original condition
waits, preserves both mutex hook pointers and checks them again at shutdown.
Existing condition interposers cause refusal. Use nativewaits.py for the same
worker and scheduler report; durations still include mutex reacquisition.
This mode uses the80MiB bounded trace. Full native mode retains its refusal of
preexisting mutex hooks. First combined capture/analyzers complete: the only
condition site is guest idle,85long in-gap runnable intervals/133.472ms;
105/184.173ms remain outside condition/present stages. Source/limits and all
diagnostic frame metrics are in PERFORMANCE.md.
The resulting250us idle-spin experiment completed three ordinary pairs:
57.833→57.878fps,5→16hitches,273.846→815.859ms/180s per mode. It is rejected;
keep off and do not repeat unchanged. Wait enclosure is not proof that a
proposed scheduling mitigation improves the game.

With conditions mode and BQL spin active, `LT_BQL_FALLBACK_PROF=on` (engine
`32d6f75762`) additionally times only blocking BQL acquisition after the spin
budget expires. Its native kind1 spans retain original source sites and exclude
time spent spinning. The same nativewaits.py report handles these spans. Both
hook pointers remain intact. Validate the deferred bql-fallback call count and
trace status, native completion and hook state; the10us retention threshold
still excludes short calls. No clocks are added to common acquisitions. Builds
pass; the combined capture and all analyzers completed. In its three >50ms gaps,
all ten selected>=1ms runnable intervals are inside blocking BQL acquisition
or the guest idle condition. These gaps still contain84.35% running time;
wait enclosure does not explain the full hitch. Full counts, preceding sleep
samples, missing evidence and diagnostic frame metrics are in PERFORMANCE.md.
Keep off for ordinary FPS. For the remaining running component, follow guest
work and the next framebuffer flip across preceding frame intervals. The
earlier Pixel gameplay gap still requires its own matching scheduler trace.

`LT_GLES_PRESENT_PROF=on` (engine `5b38fec1df`) independently times surface presentations. It defaults
off and needs neither the manual recorder, `IT_GLES_PROF`, nor full accounting.
It adds clocks at stage boundaries, never inside the row loop. Every five seconds,
`[gles-present]` emits per-path rows and an `end` marker with contiguous exact
`begin-ns` / `end-ns` boundaries. Paths are `mapped` (IOSurface/AHardwareBuffer),
`readback`, `invalid`, and the existing diagnostic `test` pattern.

Each row retains attempts, returned errors (including separately counted pending
guest-fault retries), total wall time, per-stage totals/maxima, and the exact
components/end timestamp of the slowest whole call. Stages are preparation;
acquire (flush/GPU completion/map, or fallback finish); readback; fallback
conversion; row copying (including mapped platform byte swapping); and tail
(unlock, bookkeeping and optional existing diagnostics). Early validation failures
and the test pattern are in tail. Only returned attempts count; a longjmp would
bypass completion. Clock overhead/descheduling are included; logging is excluded.
This covers the surface-present call, not direct panel presents or LCD publishing.

`python3 tools/perf/presentstages.py ENGINE_LOG --start-ns BEGIN --end-ns END --json OUT`
selects complete blocks wholly inside the supplied frame window, rejects gaps,
and preserves all selected blocks/outliers. Without bounds, `--last 30` selects
the last complete interval. Compare dedicated profiler-on captures separately
from ordinary FPS. Diagnostic overhead and phase results are recorded below.
Both 30 s profiler off/on,on/off pairs complete, user-facing recorder off:
54.23 → 51.60 fps (−4.9%), hitches 7 → 28 (388.8 → 1973.2 ms/60 s). These are
focused diagnostic captures. Mapped acquire averages 644/708 µs; copy 173/174 µs,
tail 104/100 µs. Slowest calls ~6.7 ms, predominantly acquire. Full metrics,
exact windows and scope in PERFORMANCE.md; preserve the synchronization.

## Focused interrupt checks

`LT_MASKED_IRQ_FAST=on` enables the default-off ARM physical IRQ/FIQ precheck
(engine `e82b9e770b`). `LT_IRQ_CHECK_PROF=on` independently records CPU-0 checks;
it does not enable full instruction accounting, BQL tables or the manual recorder.
For focused off/on pairs, keep profiling on in both modes and other diagnostics off.
For ordinary FPS comparisons, leave profiling off in both modes.

`[irq-check] fast=MODE` blocks contain exact `begin-ns`/`end-ns` boundaries,
`checks`, `masked` candidates, actual `skipped` checks, `delivered`, `unhandled`
locked no-op outcomes, and `special` debug/halt/reset/replay checks. `masked-delivered`
counts delivery after a masked snapshot; a newly arriving request before the lock
can explain it. Only completed checks count; target longjmps bypass the record.

`check-ns` sums completed check wall time from before the precheck through unlock
(or the skip); `wait-ns` measures BQL acquisition, including mutex/timer overhead.
`masked-ns` and `masked-wait-ns` are the corresponding masked-candidate subsets;
`max-check-ns` is the largest completed check. Timings include descheduling and clock
overhead, exclude logging and the outer interrupt-flag clear. Blocks are emitted
after unlock at the first completed check at least five seconds after the previous
boundary. Select contiguous complete blocks wholly inside each exact frame window.
Instrumentation overhead must be measured separately; diagnostic FPS is not an
ordinary baseline. Both focused masked pairs completed, second reversed: mean
7.985/8.072 → 0.422/0.426 µs, masked wall 0.883/0.891% → 0.047/0.048%; all
acquisition wait 5.036/5.036% → 4.199/4.201%. Diagnostic mean FPS 53.09 → 53.00;
no consistent game/pacing gain or ordinary baseline established. Keep off; delivery
comparison below has priority over ordinary masked-only FPS work.

`LT_IRQ_DELIVERY_FAST=on` (engine `5b956fc6ae`, default off) separately permits
CPU-only physical ARMv6 IRQ/FIQ entry without BQL. It requires a machine with
one current/maximum vCPU and no EL-change hooks; newer/M-profile targets,
virtual/special/unknown requests, replay and stepping keep the locked path.
IRQ/FIQ levels and EXITTB/new-request kicks are preserved; guest MMIO device
acknowledgement still takes BQL. It uses the existing target entry code.

In this build, `[irq-check]` adds integer `unlocked` completed checks (including
no-ops), `delivered-ns` and `delivered-wait-ns`. Delivered subsets can overlap
masked candidates if the request changes before lock acquisition. `unhandled`
now includes eligible unlocked no-ops too; `fast=MODE` still names the masked
precheck switch, so use the separate `[tcg] irq-delivery-fast=MODE` init line and
saved environment to identify delivery mode. Compare with IRQ profiling on both,
masked fast/L2/other diagnostics off; then use separate profiler-off ordinary
FPS pairs. Both optimizations remain default off. Both focused delivery pairs are
complete, second reversed: delivered check 87.205/74.900 → 1.946/2.168 µs and
wait 83.265/71.263 → 0/0 µs. All check/wait wall 5.765/5.448% and 5.135/4.829%
→ 0.293/0% and 0.189/0%. Diagnostic mean 53.74 → 51.69 fps; hitches 12 → 50
(920.8 → 3999.6 ms/70 s). First on arm has two interrupt-spike blocks; reverse
on arm has normal frequency but also loses. Removed check wait need not be
recovered total time: device MMIO still locks. Keep off; no game/pacing benefit
or ordinary FPS baseline established. Profiler-off ordinary runner is prepared
only, deferred, and needs a new serialized dependency before any later launch.

## Focused exact-zero VFP multiplication

`LT_VFP_MUL_ZERO_FAST=on` enables the default-off AArch32 single-precision VFP
zero-multiply experiment (engine `f43fbe4986`). `LT_VFP_MUL_ZERO_PROF=on` independently
selects diagnostic helpers; ordinary off/on helpers contain no profiling clocks or
counters, and the ordinary off path retains the original helper.

`[vfp-mul-zero] fast=MODE` blocks report `begin-ns`, `end-ns`, `calls`, `eligible`,
`mixed`, `samples`, `ns`, `max-ns`. CPU 0 owns the counters. Eligible operands are
an exact zero and a normal finite value or another zero; mixed excludes two-zero
pairs. Only mixed operations are timed, one in 256, including clock overhead and
host descheduling. Mean sampled ns/call is `ns / samples`; frequency is `mixed`
divided by the actual block interval. Select complete contiguous blocks inside
the frame window and retain their separate bounds. These diagnostic FPS windows
do not establish uninstrumented throughput or profiler overhead.

The switch preserves softfloat for nonzero, subnormal, infinite and NaN operands.
It does not change rounding/flush modes or cumulative exception flags. Both
switches are fixed for the process and selected during translation; restart the
engine after changing debug-env. No runtime measurement yet.

## Focused exception counts

`LT_EXCEPTION_PROF=on` (engine `42395839f3`, default off) logs complete cumulative snapshots
every five seconds from CPU 0 in AArch32 mode. It counts exceptions, raw SVC `r12` values and
short-descriptor data-abort status codes. For iOS 3, SVC `r12=-31/-32` also counts Mach-message
send/receive **attempts** from option bits; these are not successfully delivered messages.
Counts cover every guest process. The switch does not enable full vCPU accounting.

Host dispatch wall time is instrumented separately for each exception class. It includes
diagnostic bookkeeping and host descheduling during dispatch, excludes the guest kernel handler,
and excludes any BQL waits before `arm_cpu_do_interrupt`. Maximum durations are cumulative since
the diagnostic started; they cannot be subtracted to obtain an interval maximum. Snapshot logging
is outside these duration totals, and the diagnostic's recording overhead has not been measured.

After pulling the record's engine log, summarize the last approximately 30 seconds:

```sh
python3 tools/perf/exceptions.py engine.log --last 30 --json exceptions.json
```

`--start S --end S` selects seconds since the first profiled exception. The report prints the
actual snapshot boundaries used. Add `--frames frames.log` with a full `LT_FRAME_LOG` covering
both boundaries for counts per published frame. Incomplete final snapshots are ignored.
This is a focused diagnostic for finding the next optimization; no FPS improvement is implied.

## Focused LCD dirty snapshots

`LT_LCD_SNAPSHOT_PROF=on` (engine `9400ab1b68`, default off) emits `[lcd-snapshot]` five-second
blocks: `n`, `forced`, `skipped`, summed `ns`, `forced-ns` and `max-ns`. Divide summed times by
their respective counts for means. Timing covers the dirty snapshot stage of linear scanout;
composed/rotated paths bypass it. Log output follows the LCD unlock. Host descheduling is included.

The `LT_LCD_FORCED_SNAPSHOT_SKIP` experiment was rejected: saving snapshot time worsened
Jetpack throughput and hitches. Engine `197f75d7ae` removes it and always clears the snapshot;
that environment variable is no longer read. Older frozen APKs retain the switch for their
recorded comparisons. Current logs retain `skip-forced=off` and `skipped 0` for compatibility.
Snapshot diagnostics do not enable the live recorder or full accounting.

## Focused dirty-state queries

`LT_DIRTY_QUERY_PROF=on` (engine `4d06b57f50`, default off) samples one in 256
`physical_memory_is_clean` calls on CPU 0. `[dirty-query]` logs give exact `begin-ns`/`end-ns`
block boundaries, total `queries`, timed `samples`, summed `ns` and `max-ns`. Divide `ns` by
`samples` for mean sampled query time. Timings include timer overhead and host descheduling;
they exclude diagnostic output and counter bookkeeping. Non-vCPU/other-CPU queries are untimed.

Compare interleaved `LT_DIRTY_QUERY_FAST=off/on` runs with this diagnostic enabled in both.
The diagnostic can perturb scheduling, so keep it off for ordinary FPS comparisons. It does
not enable the live recorder, full accounting or exception/LCD diagnostics.

## TLB refill RAM-pointer timing

Engine `54da21c185` adds independent default-off `LT_TLB_RAM_PTR_FAST=on`
and `LT_TLB_RAM_PTR_PROF=on`. The fast path avoids a nested RCU lock/unlock
when a TLB refill maps an unaliased RAM/ROMD region. The caller's existing RCU
critical section still protects the translated section; the original RAM mapper
and alias fallback remain. It does not change RCU TLS storage or coroutine accessors.

The diagnostic samples one call in 256 on CPU 0. `[tlb-ram-ptr]` blocks retain
exact `begin-ns`/`end-ns`, `calls`, `samples`, summed `ns` and `max-ns`;
`ns / samples` is sampled mean call wall time. Blocks finish at the first sample
after five seconds. Select complete contiguous blocks inside the frame window.
Times include clock overhead and descheduling; maxima cover sampled calls only.
Initialization logs both switches. No sampling clocks or counters run with the
diagnostic off. Keep Start profile and other diagnostics off for this focused
comparison, and turn this diagnostic off too for any ordinary game comparison.
Builds and two reversed focused pairs completed: sampled call means
365.164/373.283 → 348.406/351.099 ns. This small local saving supplies no
established game or hitch benefit; keep off. Full capture metrics and limits
are in PERFORMANCE.md. No profiler-off ordinary comparison was run.

## Guest PCs in an attached CPU profile

For a focused run, set `QEMU_PERFMAP_DIR=/data/user/0/org.lighttouch.android/files` in
`files/debug-env` before starting the guest. This enables per-instruction translation maps and
can add work while new code is translated; use it separately from ordinary FPS comparisons.
Attach with `tools/perf/gameprof.sh OUT 15`. Pull `files/perf-PID.map` from that same worker before
and after recording, retaining the PID in the filenames. Map output is buffered; the newest
entries may be missing. Do not use a map from a different worker.

```sh
python3 tools/perf/guestpc.py OUT/perf.data OUT/perf-PID-after.map \
    --before-map OUT/perf-PID-before.map --json OUT/guest-pcs.json
```

The tool uses the NDK simpleperf Python reader (`--simpleperf-dir` overrides the default SDK
path), weights self samples by their event periods and reports guest instruction PCs. Reused
translation-buffer addresses with conflicting mappings are excluded. With `--before-map`, only
the same unambiguous mapping in both snapshots is accepted. Incomplete final map lines are ignored.
The remaining event weight includes native work and unmapped JIT code; it is not a native-only
percentage. Guest PCs do not identify processes or unwind guest stacks. First real Poco captures
completed successfully, mapping about 44% of vCPU event weight with stable before/after maps.
Exact sample bounds, native reports and limitations are recorded in [PERFORMANCE.md](PERFORMANCE.md).

## Publisher wake diagnostics

`LT_PUBLISH_WAKE_EARLY=on` (default off) snapshots the engine notification count before the
publisher's frame/listener/audio work. It then waits only if that count is unchanged, preserving
notifications received during work. The 10 ms control/audio polling timeout remains.

Independent `LT_PUBLISH_WAKE_PROF=on` (default off) emits `[publish-wake]` five-second blocks:
`begin-ns`, `end-ns`, `loops`, `changed`, `changed-wait-ns`, `frames`, `age-ns`, `max-age-ns`.
`changed` counts loops with a notification during work, and `changed-wait-ns` sums their
subsequent waits. Some notifications may already have been consumed by acquisition, so these
are not missed-frame counts. `age-ns / frames` is mean frame timestamp age at publication;
it includes LCD/pacing work before capture and excludes later listener/audio work. It is not
solely publisher latency. Compare interleaved arms with the diagnostic on in both; keep it
off for ordinary FPS baselines. Two diagnostic pairs found no throughput or frame-age
gain; the switch remains off. Full measurements and limits are in PERFORMANCE.md.

## TLB dirty-reset experiment

`LT_TLB_DIRTY_FAST=on` (engine `42d50e5d2e`, default off) checks compact TLB entry flags and
host address range before fetching full-entry slow-write flags. The same entries and flags
are checked under the same lock. Dirty snapshots are still cleared and every reset retained.
Keep forced-snapshot skipping off; that separate experiment worsened game pacing.

`LT_LCD_SNAPSHOT_PROF=on` measures the enclosing snapshot call in both arms. Engine `1cd3a771c3`
adds explicit `begin-ns` and `end-ns` fields to its blocks; older logs have no exact boundaries.
Snapshot time includes bitmap copy/clear, the TLB reset and the TCG `run_on_cpu` ordering
barrier. It does not isolate reset-loop time. Do not attribute its full duration to the scan or
remove the ordering barrier based on this timing. Three Poco Jetpack pairs found mixed
throughput (52.38 → 53.00 fps) and unchanged enclosing snapshot cost (509.11 → 508.97 µs).
The switch stays off; stage measurements below isolate a local reset saving. Full pairs and boundaries are
in [PERFORMANCE.md](PERFORMANCE.md).


## LCD snapshot stage timing

`LT_LCD_SNAPSHOT_STAGES=on` (engine `9fd2a85805`, default off) enables the existing
snapshot diagnostic and adds `[lcd-stages]` blocks with the same exact boundaries.
`presync-ns` times listener synchronization; `setup-ns` address setup and allocation;
`bitmap-ns` atomic bitmap copy/clear including RCU; `reset-ns` TLB dirty-write tracking
reset including its lock; `clear-ns` dirty-clear listeners; `barrier-ns` the after-sync
ordering hook (TCG waits for vCPU work). Timings include timer overhead and descheduling.
Their sum excludes dirty-page counting and some wrapper bookkeeping; the enclosing
`[lcd-snapshot] ns` includes these too. Logs are emitted after the LCD unlock.

`dirty-pages` counts dirtied pages in the requested scanout range across snapshots;
`empty` counts snapshots with zero such pages. Alignment padding is excluded from that
count even though bitmap copying clears it as before. `n - skipped` is the denominator
for stage means. No resets, clears or ordering barriers are skipped, including empty
snapshots. Composed/rotated paths do not use this diagnostic. Keep forced-snapshot
skipping off. Four Poco Jetpack diagnostic captures completed: reset means
82.29/108.22 → 56.03/56.23 µs/update, but the TCG ordering barrier dominates at
443–477 µs. Requested-range empties are 10–20% of snapshots per arm; their count
excludes cleared alignment padding and cannot alone authorize skipping whole-bitmap
resets/barriers. One off arm is hitch-heavy; no repeatable ordinary game gain or
diagnostic overhead established. Full metrics/bounds in PERFORMANCE.md.


### Selected guest instruction boundaries

`LT_GUEST_TRACE_PC=ADDRESS,ADDRESS,...` (engine `ab08a5d277`, default off)
selects up to32 AArch32 instruction boundaries. Use a low bit of1 for Thumb and
0 for ARM, just as with function pointers. Invalid configurations log an error
and select no points. Set `LT_FRAME_PATH` and `LT_FRAME_PATH_CONTEXT=on` too;
keep audio/fault/full accounting/user profiling off for scheduling captures.
There are no generated runtime probes with this switch off. CPU0 snapshots
read registers only, without guest memory reads or debugger stops. The bounded
buffer is80MiB in this mode; reject dropped/incomplete traces as usual.

Each three-record observation has a unique ID:

- `guest-pc`: a=ID, b=PC-with-Thumb-bit<<32|TPIDRPRW-with-low-two-bits-masked,
  c=raw-CIDR<<32|raw-TTBR0.
- `guest-regs`: a=ID, b=R0<<32|R1, c=R2<<32|R3.
- `guest-stack`: a=ID, b=LR<<32|SP, c=R12<<32|processor-mode.

These are **before-instruction** observations. A conditional instruction can
still be skipped, and a reached function/block point does not prove a wait or
return completes. Choose points and interpret register arguments using the
matching firmware's disassembly; no firmware addresses are baked into the
engine. Avoid instruction interiors or large point sets. A boundary after the
kernel's TPIDRPRW write can identify the scheduled thread, and actual enqueue/
block paths can provide the complementary state evidence. Installed pmap and
thread addresses are different identities.

After clean Stop and the same-worker process snapshot:

```sh
python3 tools/perf/guestpoints.py events.tsv frames.log --engine-pid PID \
    --guest-processes processes.json --guest-kernel /local/kernel.macho \
    --guest-cache /local/dyld_shared_cache_armv6 --json guest-points.json
```

`LT_GUEST_TRACE_DELAY_MS` (engine `d70057ad74`) optionally delays whole PC
observations by0–600000ms from the first AArch32 translation/point-config
initialization; the default is0. The effective delay is printed in the mode
line. Whole triplets are skipped before assigning IDs, preserving paired,
contiguous observations. Frame/context milestones continue through boot and
settling. Start the focused frame window after the delay has elapsed. This
preserves the bounded80MiB buffer for the relevant period rather than recording
all scheduler activity during boot. The first undelayed capture overflowed;
both analyzers rejected it, so it supplied no attribution result.

The analyzer checks the complete footer, monotonic triplets, context identity
and frame windows, and retains every snapshot including those outside the
window. It supplies counts/arguments and frame classes, not an automatic claim
that audio or a particular scheduler dependency caused a missed deadline.
Use the same host scheduler trace to split execution from runnable/sleeping
intervals. One delayed real capture now confirms complete triplets and actual
scheduled-thread transitions. It separates aligned/unaligned converter calls
and matches kernel enqueue requests to scheduled boundaries; see PERFORMANCE.md
for measured differences, dispatch/queue timing limits and the remaining
non-decoder hitches. This is attribution evidence, not an optimization result.

### Guest swap API and display handoff

For a missed refresh after a completed physical-buffer write, follow the
guest's submission path. `tools/perf/n72-7e18-swap-points.json` pins 20 points
to the recorded n72/7E18 kernel and shared-cache SHA256 values. It covers swap
API entry/epilogues, nonnull IOSurfaceUnlock, CoreSurfaceBufferGetID results,
scheduled-thread changes and thread_setrun requests. These addresses require
the matching firmware; do not reuse them on another build. No firmware is
included. Set LT_GUEST_TRACE_PC from the manifest's pc values, with frame/context
and physical surface tracing; delay guest points until the scene has settled.
Keep user recording and unrelated diagnostics off. Do not stream guest syslog.

After clean Stop, run guestpoints.py, hitchsched.py with --all-states, and
surfacemap.py with --lcd-address-mode n72-physical against the same window/raw
files. Retain their outputs as guest-points.json, hitches.json, surfaces.json,
and the detached same-worker snapshot as guest-processes.json, then:

```sh
python3 tools/perf/swapcalls.py build/perf-codex/poco-swap-path-1 \
    --engine-pid 6494 --json build/perf-codex/poco-swap-path-1/swap-report.json
```

The tool validates worker/window, pinned symbol hashes, snapshot/raw input
identity and scheduler continuity. It pairs APIs per thread, retains initial
epilogues/pending entries, reports call counts/results/elapsed times, and splits
each API and matching-write→flip interval into host states and selected guest
thread execution. It preserves ordered same-thread unlock observations and all
overlapping APIs. It does not infer a buffer dependency from adjacent calls.
On the real capture, every call/summary/flip split/present association matches
the original focused report. No phone interaction is performed by the tool.

An epilogue observation precedes return execution. SwapWait elapsed time includes
other guest threads. Post-window ownership needs lifetime continuity; selected
thread running includes kernel/native execution. An enqueue request is not
proof of runnable state or exact queue residence. Physical backing, layer IDs,
CA eligibility and pixel-generation identity are distinct. In particular, the
newly observed secondary-layer-only transactions correlate with missed refreshes
but must be traced back to the CA notification path before changing behavior.

The follow-up manifest `n72-7e18-ca-points.json` traces QuartzCore's actual
native_window_swap callback (verified at drawable+0x10 in its constructor),
IOSurfaceGetID inside that callback, CAImageQueueInsertImage, native buffer
acquisition and display begin/finish/primary-state branches. Pass it with
`swapcalls.py --points tools/perf/n72-7e18-ca-points.json`; firmware hashes
remain pinned. Observation-only points stay in guest-points.json for branch
and ordering analysis; they are not paired API calls or void-function results.
The callback's IOSurfaceGetID is separate from the older SetLayer observations
of CoreSurfaceBufferGetID. Both IDs agree for all3466 observed fresh flips in
poco-ca-path-1, with one ordered callback/insert association each.

That capture finds primary-state flag0 with the descriptor retained in all104
secondary-only transactions. In all81 matching held cycles the producer callback
finishes after the previous compositor primary-state decision. Their association
with missed refreshes therefore does not justify skipping them. Trace queue
consumption next. The same capture also identifies Bluetooth-owned execution
in10of13large hitches using actual post-window task enumeration, without syslog
streaming; fresh HCI/startup evidence is still needed before changing that path.

### Compositor queue consumption

For queue consumption, `n72-7e18-queue-points.json` extends the CA manifest to
32points: generation comparison, CopyImageInfo with its selected serial, render
comparison and shared-queue update. SwapWait/native buffer acquisition/absence
points are removed. Run swapcalls.py with this manifest after the same raw
analyzers. The insertion serial probe precedes a later conditional increment;
its notes specify the pinned native caller's always-set bit1 and preserve the
base+1 derivation. Do not treat raw base serial equality or process-local queue
pointers as independent image/queue identity. Void update boundaries carry no
success result. The real queue capture validates3590ordered serial candidates
and separates pre-query insertion timing from two post-selection audio delays;
full evidence and local report scripts are recorded in PERFORMANCE.md.

### Bluetooth startup and controller exchange

Engine `deb39789e4` supports default-off `LT_BT_PATH=on` together with
`LT_FRAME_PATH=/absolute/private/path/bt-path.tsv`. Keep user recording off.
The existing deferred trace retains these events in a bounded40MiB buffer;
pull it after clean Stop. It records metadata and reply prefixes only, never
guest firmware command payloads. Controller/UART/timer behavior is unchanged.

| Event | a | b | c |
| --- | --- | --- | --- |
| `bt-command` | HCI opcode | parameter byte count | configured latency ns |
| `bt-queue` | reply chunk bytes | first8bytes, little-endian packed | pending bytes after queue |
| `bt-flush` | bytes about to inject | first8bytes, little-endian packed | pending bytes before injection |
| `bt-state`, a=0 | machine reset | partial command bytes | pending bytes before clearing |
| `bt-state`, a=1 | UART backpressure | pending bytes | configured latency ns |
| `bt-state`, a=2 | existing overflow/drop | requested bytes | pending bytes |

The injection event is immediately **before** `qemu_chr_be_write`, not a
measurement of completed UART/DMA handling or userspace receipt. Queue/injection
events preserve the byte counts even when only a prefix is known.

```sh
python3 tools/perf/btpath.py CAPTURE/bt-path.tsv --json CAPTURE/bt-report.json
```

The analyzer requires a complete/lossless footer, checks queue/reset occupancy
and known prefix agreement, pairs Command Complete opcodes/status and the
minidriver announcement, and retains every command/reply with injection times,
unmatched work, input hash and limits. It performs no device interaction.
If guest syslog is needed, use a bounded `androidsyslog.py capture` from the
same worker and retain its identity. Its receive timestamps can include a
backlog, so they do not place message execution within a particular frame.

The first two real captures are healthy startup references. The later queue
capture retains a same-worker timeout/relaunch despite complete prompt reply
injections; successful injection is insufficient proof of userspace receipt.
Use protocol captures to narrow the missing transition, then ordinary
interleaved comparisons with diagnostics and user recording off to measure a fix.
