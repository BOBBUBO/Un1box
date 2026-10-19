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
