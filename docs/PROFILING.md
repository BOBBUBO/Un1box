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
baselines. Compare changes with interleaved A/B pairs using the same capture settings.

For a focused context-switch investigation, `LT_GLES_CONTEXT_PROF=on` (default off) writes
`[gles-context]` totals/maxima to the engine log every five seconds. It separately times initialization,
make-current and dispatch for viewport, framebuffer bind and surface present calls, without requiring
Start profile. `switches` counts changes in the engine's selected context, not driver-internal work.
Dispatch includes strict error checks. Divide summed nanoseconds by `n` for the mean per call.

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

`LT_LCD_FORCED_SNAPSHOT_SKIP=on` independently skips this work when all rows must be repainted
(default off). It leaves dirty bits set, which can add later redundant repaint or surface uploads.
Measure that cost as well as the saved snapshot time before enabling it. Skipped work contributes
zero nanoseconds to the diagnostic. Neither switch enables the live recorder or full accounting.

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
off for ordinary FPS baselines. Runtime measurement is pending.

## TLB dirty-reset experiment

`LT_TLB_DIRTY_FAST=on` (engine `42d50e5d2e`, default off) checks compact TLB entry flags and
host address range before fetching full-entry slow-write flags. The same entries and flags
are checked under the same lock. Dirty snapshots are still cleared and every reset retained.
Keep forced-snapshot skipping off; that separate experiment worsened game pacing.

`LT_LCD_SNAPSHOT_PROF=on` measures the enclosing snapshot call in both arms. Engine `1cd3a771c3`
adds explicit `begin-ns` and `end-ns` fields to its blocks; older logs have no exact boundaries.
Snapshot time includes bitmap copy/clear, the TLB reset and the TCG `run_on_cpu` ordering
barrier. It does not isolate reset-loop time. Do not attribute its full duration to the scan or
remove the ordering barrier based on this timing. Device measurements remain pending.
