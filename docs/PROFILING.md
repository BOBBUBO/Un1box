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
no samples. New snapshot build/runtime confirmation pending.

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
