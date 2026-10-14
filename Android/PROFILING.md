# Profile an existing Android guest

Use the installed Debug candidate21b with a running foreground guest and its
current dedicated `:engine` PID. The helper attaches; it does not start an
Activity, restart/stop the worker, compile Java, invoke adb root/unroot, or write
system properties. Keep recordings private. Results do not establish guest
rendering, audible output or lifecycle acceptance.

The selected NDK is28.2.13676358, simpleperf build12821384. The helper verifies
the exact Android and Linux report binary hashes before any device command.
Android's `--app` route can delegate to an app-context/platform recorder; retain
the actual recording cmdline/version instead of claiming every recording ran
the NDK binary. [AOSP app profiling](https://android.googlesource.com/platform/system/extras/+/main/simpleperf/doc/android_application_profiling.md)
describes the supported non-root debuggable-app route.

```sh
LT_NDK=/path/to/Android/Sdk/ndk/28.2.13676358
LT_SERIAL=your_selected_device
LT_ENGINE_PID=current_existing_worker_pid
LT_PROFILE=/path/to/private/new-profile-directory
python3 Android/scripts/profile-existing-guest.py \
  --ndk "$LT_NDK" --serial "$LT_SERIAL" --engine-pid "$LT_ENGINE_PID" \
  --scope engine --event cpu-cycles:u --out "$LT_PROFILE"
# Review the plan, then add --execute to the same command.
```

Use `--scope app` for a separate whole-app recording of the exact running app,
engine and firmware process identities. These process modes select `-p` and
collect threads of the selected processes. The separate `--scope engine-threads`
mode enumerates `/proc/ENGINE_PID/task` under the app UID, verifies each task's
Uid/Tgid/Pid, start ticks and actual comm, and passes **only `-t VERIFIED_TIDS`**.
It does not add `-p` or collect system-wide. The same selected TIDs must retain
their owner/start ticks/comm afterward; an exiting or reused TID invalidates the
recording. Threads created later are outside that explicit selection. Recording is fixed
at10s/99Hz, with an8KiB DWARF stack,16MiB user buffer and32MiB file limit. The
binary/output directory is a fresh `/data/local/tmp/lt-perf-<uuid>`; unrelated
profiler outputs are untouched. `receipt.json` retains all commands, selected
UID/PID/start ticks and scoped thread mappings before/after. `ps NAME` can show
the process name for every thread, so the helper also reads each owned thread's
actual `/proc/PID/task/TID/comm`.

The pinned profiler can internally adjust kernel limits. The helper instead
requires existing paranoid<=1, sample-rate>=4000 (its internal default even
with requested99Hz), an unchanged valid cpu-percent and enough existing mlock
budget for tiny rings. If that conservative read-only preflight, app-context
permission or recording fails, retain the failure and use the counters below.
Do not apply the profiler's suggested settings/security workaround. The helper
avoids NDK wrappers that switch ADB privilege mode or use shared output paths.
[AOSP command reference](https://android.googlesource.com/platform/system/extras/+/main/simpleperf/doc/executable_commands_reference.md)
documents process/thread selection, sample frequency and reporting.

The helper runs the pinned host report and requires **both** nonzero Samples and
reported sample event count. Exit0 and a perf.data file are insufficient. A
zero-sample recording returns2 with `INCONCLUSIVE_ZERO_SAMPLES_NO_CPU_ATTRIBUTION`;
unknown/missing/ambiguous reports remain unavailable. Positive samples still
need symbol and workload review. Use the exact unstripped engine and JNI inputs
from the sealed producer's input-to-packaged mappings, matched by build ID, with
the report command's `--symdir` option; do not substitute a newer build.

The first actual21b recording used `task-clock:u` while a static Photo was
selected. Recording/report commands succeeded but produced0 samples/event
periods. The engine's process CPU counters advanced across the wider
preparation/record bracket, so this means inconclusive sampling, not CPU idle.
The next `cpu-cycles:u` process recording also produced zero samples, but that
guest was paused after ActivityScenario.close; it is not an active benchmark.
A controlled explicit-thread variant is:

```sh
python3 Android/scripts/profile-existing-guest.py \
  --ndk "$LT_NDK" --serial "$LT_SERIAL" --engine-pid "$LT_ENGINE_PID" \
  --scope engine-threads --event cpu-cycles:u --out "$LT_PROFILE"
# Review the plan, then add --execute. Use a new evidence directory each time.
```

The operator must explicitly resume the same guest after any SDK scenario
closes, then perform the declared live menu/Photos workload during recording.
Neither zero-sample result identifies a kernel or profiler defect. Unsupported
hardware events remain a retained permission/event failure; never change
settings to make them work.

## Paired counters and responsiveness

Keep the same worker UID/PID/start ticks and resumed Activity throughout the
observed interval. Obtain actual `IEngine.status()` snapshots through an already
attached app-UID observer; this host helper does not start instrumentation to
collect them. Instrumentation completion/ActivityScenario.close can pause or
stop the target and confound the profile. Retain observations and raw captures
before, during and after the operator's declared workload.

- Compare `producedFrames`, `presentedFrames`, frame serial/timestamps,
  presentation-gap buckets and window/copy failures. A static frame can be
  correct; copied/presented counters alone do not prove a new guest response.
- Compare pulled PCM/nonzero sample energy, written frames/playhead,
  write failures and underruns, with the same audio observation baseline.
  Cumulative nonzero PCM is not simultaneous touch/render/audio proof.
- Preserve existing and new input errors. A retained historical rejection is
  not cleared by profiling or by successful queued admission. Do not bypass
  acceptance test guards without a reviewed fault/baseline contract.

For a future normal launch measurement, capture the fresh worker's
`observations.hostMonotonicNs` immediately before start admission and retain the
same-worker first produced/copy/present sequence. Frame/publish/presenter times
share the device native monotonic epoch. A later balanced interaction followed
by a fresh coherent changed frame and inspected guest response establishes
responsiveness more strongly than a boot-logo frame. Keep the Activity resumed
until after the settle/capture boundary. Record both earliest nonzero frame and
first inspected responsive scene separately; no automatic scene classification.

Host ADB command times, Android elapsedRealtime (BOOTTIME), MotionEvent uptime,
PCM capture-relative timestamps and native MONOTONIC are distinct domains.
Do not subtract across them. An existing worker cannot provide retrospective
launch latency, and an ADB `am start -W` timing is not guest boot latency.
Candidate21b keeps candidate20's QEMU engine, but also changes JNI/Runtime
optimization, ownership, telemetry and Stop receipts; a20→21b comparison cannot
attribute an improvement solely to `-O2`.
