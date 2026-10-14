# Observed admission handoff and first n81 measurement

This is a source review and future execution plan, not an executed large-base or
guest result. `ae13e875` passed five small-fixture CTests under ASAN/UBSAN. Those
tests exercise actual storage callbacks, private socket datagrams and injected
deadline clocks, plus host SDL software rendering. They do not execute the
`Main.cpp` parent/worker HELLO/start cancellation race. The parent guard ordering
correction in `bafe76a6` requires an allocated incremental build and test receipt.

## Process and ownership review

The parent forks/execs its known child before calling SDL initialization. Device
admission, engine loading and `lt_runtime_create` execute only in the worker.
Library selection/import before this fork retain their existing separate behavior;
the new admission budget does not cover parent import operations.

`PrestartHandles` retains the leased device while a runtime borrows it. Cancellation
returned by observed native admission leaves a null device handle and releases its
lease. After a runtime exists, cleanup destroys the unstarted runtime before closing
the device. If destruction fails, cleanup retains both handles for process disposal.
After successful `lt_runtime_start`, the guard deliberately does not close an active
device. Native start failures may already have written `.dirty`; no cleanup path
clears it merely because a process or constructor returned an error.

HELLO means the native admission checks and runtime/frame mapping creation completed.
It is sent **before** `lt_runtime_start`; it does not prove successful start, engine
readiness, a guest frame, or guest acceptance. Successful SCM_RIGHTS transmission
retains the queued/received descriptor reference after the worker closes its local
duplicate. A subsequent start failure cannot invalidate that reference. The parent
closes its received descriptor after mmap and keeps the mapping until teardown.

Before HELLO, Escape/close/SIGINT, a 900-second absolute budget or 30 seconds without
an observed checkpoint request admission cancellation. Progress callbacks never
extend the absolute budget. Cancellation checks happen between native digest chunks
and checkpoints; a blocked syscall is handled by the bounded host fallback. The
parent permits five seconds for cancellation acknowledgment, then disposes only its
unreaped child. It must not describe that fallback as successful native cancellation
or clean guest shutdown.

A cancel request can race the final callback and HELLO/start. The active worker
aliases late `AdmissionCancel` to ordinary Stop. After receiving HELLO, a canceled
parent also submits ordinary Stop, allowing the existing 30-second worker and
35-second parent shutdown policy. This can time out during early guest boot and
retain dirty state. Cancellation after the handoff is not guaranteed to leave a
previously clean state clean.

`bafe76a6` constructs the child guard before either parent peer validation. Peer
validation or later SDL/protocol/setup exceptions therefore close the sockets and
run bounded WNOHANG disposal. A still-unreaped child receives TERM then KILL within
bounded waits; ECHILD/reaped children are never signaled again. Pending disposal is
reported and does not imply that a lease is free. This correction has source-order
verification only until its new compiler/test slot completes.

Post-HELLO audio initialization and script parsing can still throw. These failures
dispose the owned process and retain any dirty state; they are not orderly Stop
receipts. During ordinary termination, status/PCM sends are nonblocking. The parent
stops reading on a terminal status and waits a bounded interval for process exit.
There is no blocking socket-drain dependency, but unread PCM/input replies can remain:
the final frontend counters are not proof that every native sample/reply was consumed.
Exit zero still requires the worker's confirmed clean shutdown path and actual exit.

## One future measured n81 launch

Root allocates this run separately. No state directory, base copy, new hash pass,
engine process, GUI or guest was created by this plan. After the guard correction is
built, bind the actual executable hash/source to a new receipt; do not attribute its
bytes to the earlier `821ffcdd` artifact. The tested ae13 frontend was:

```text
builds/runtime/observed-linux-admission/debug-sanitized-01/lighttouch-linux
SHA256 821ffcdd27170be29c1b614511d4fc1306cc02954d79cc207520069072c844df
```

Its archive SHA is
`450f6c1768103ba4aeadc9018ce754533fd5b8b40469e007def086ff30f6f4b4`.
It uses GNU13.3 Debug, scoped SHA -O2 with assertions, ASAN/UBSAN, debug QMP enabled
and system SDL2. The previous non-sanitized one-store measurement (12.8405 seconds
for 2.214GB) is **not** a throughput result for this sanitizer producer. Retain the
old artifact/receipt when producing a corrected frontend.

Use the existing immutable prepared directory directly, one new private state and
the supplied matching armv7 package. Native engine facts select `iPod-Touch-4G` for
`n81ap`; the current shared domain has a kboot/NAND overlay/NOR writable-copy route.
That source-supported route has not yet admitted this complete supplied fixture.
The exact base/package paths below come from the retained firmware n81 route audit.
The existing private engine is the byte-frozen Linux `b88aaabee7` producer; this plan
does not apply later Android/RCU backend changes to those bytes.

From the workspace, after binding `FRONTEND` to the reviewed corrected executable:

```sh
env -u IT_USB_TCP -u IT_AUDIO_HW -u IT_GLES_DEBUG \
  ASAN_OPTIONS=detect_leaks=1:halt_on_error=1 \
  UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 \
  "$FRONTEND" \
  --base /home/blake/Documents/LightTouch-Android-Handoff/n81ap-mac-verified/LightTouch-n81ap-8C148-Handoff/fixture/n81ap-8C148 \
  --state /home/blake/Documents/LightTouch-Development/port-workspace/builds/runtime/n81-observed-acceptance/state-01 \
  --engine /home/blake/Documents/LightTouch-Development/port-workspace/builds/runtime/gui-acceptance/libqemu-arm-1ffcb57d.so \
  --package /home/blake/Documents/LightTouch-Android-Handoff/n81ap-mac-verified/LightTouch-n81ap-8C148-Handoff/release-guest-unpacked/guest-tools/armv7.itpack \
  --headless --no-audio --trace-io --seconds 240 \
  --screenshot /home/blake/Documents/LightTouch-Development/port-workspace/builds/runtime/n81-observed-acceptance/run-01/checkpoint.bmp
```

Engine SHA256 is
`1ffcb57dab386ad2e144337e7670ea4b058ad18608500b6c7d72db2624ba03ff`;
matched package SHA256 is
`f0df8dfb31b59ee17d930c18fa126f77d0259b54e9fb37acb91bc4cc821a6141`.
Record the chosen DISPLAY/EGL provider: a headless SDL frontend does not establish
that the engine has no display dependency. Omit ROM, import, recovery, arbitrary
machine/memory overrides and uncalibrated scripts. State creation is performed by
native admission; do not replace retained state-01 if a previous attempt exists.
Native writable state uses an overlay and small NOR copy, never an 18GB base copy.

The root controller should retain argv, source/executable/engine hashes, frontend
PID and startticks, announced worker PID/startticks, stdout/stderr/serial, load/RSS
and CPU/wall samples. Pair every progress observation's receipt CLOCK_MONOTONIC with
worker timestamp, sequence, phase and actual processed/total digest bytes. Retain
the final digest checkpoint and verified HELLO as separate milestones; done=total
alone is not admission success. Then retain runtime readiness, source frame sequence
and timestamp, backlight/sleep/agent, actual captures and terminal/exit receipts.

Use a 1220-second absolute outer observation bound: internal admission is at most
900 seconds, session duration begins after HELLO/setup and is 240 seconds, and normal
Stop has its existing 35-second parent bound. The margin accommodates cancellation
and disposal. At the outer bound, signal the **known owning frontend** INT and
continue observing ordinary Stop for 40 seconds. Any forced cleanup must use held
pidfds or verified exact parent/worker PID plus startticks and original ancestry;
dispose the known worker before killing its supervisor. Never signal arbitrary
descendants, shared QEMU workers or a reused PID. Record unresolved processes/leases,
dirty state and forced termination as failures, not clean exit. Do not launch a
second writer or add implicit recovery after a failed run.

Full native admission, runtime start/readiness and guest-visible pixels are separate
results. The ending screenshot may be sleeping or stale and must be inspected with
its receipt; no inferred Home/input pass. This first graphics-only measurement does
not qualify audio, physical audibility, ES2 guest workload, persistence, sustained
performance, motion/gyro, SDK/probes or n81 firmware preparation.
