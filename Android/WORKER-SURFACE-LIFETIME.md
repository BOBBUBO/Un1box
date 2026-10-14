# One UI Surface per engine worker

Production24 retained a Surface image from the retired worker after recovery
started a different worker. The new producer reached frame53/awake/agent Lock,
while presentation had2posts,7500window-lock failures/-22 and no frame-copy or
geometry failures. Saved UI images still show the earlier power-off scene.
This establishes a presentation failure. It does not identify the platform's
exact reason for returning EINVAL or a guest framebuffer/TCG defect.

The source previously retained one AndroidView/GuestDisplay across worker Binder
replacement. Its mutable engine closure and immediate onServiceConnected callback
could attach the existing holder Surface/BufferQueue to the new process. A new
ANativeWindow reference, native generation or View lease does not construct a new
SurfaceView/BufferQueue. In particular, a producer's unconfirmed disposal did not
cause the UI-owned Surface to be replaced.

WorkerGuestSurface now keys its AndroidView by strict Binder incarnation identity,
with reuse disabled. A new worker constructs a new View/holder; same-worker status
recomposition keeps the existing View. Each View captures its worker, and checks
that it is still current before attachment, touch admission or delivering an error.
Cleanup retains the captured worker/token after replacement, so it can release
A's resources without resolving to B. An
old callback cannot use a mutable closure to acquire the new worker's lease before
Compose disposes the old View. Existing native owner/request/generation/ref rules
remain authoritative. Release retires only that View; MainActivity clears its
pointer only if it still names the released View. Native window calls and retained
window lifetime remain unchanged.

The pure identity test covers referential worker identity even if objects compare
equal. The opt-in WorkerGuestSurfaceTest uses the production composable with fake
workers on actual Android SurfaceViews. It requires a stopped unused real service
and the exact installed main APK hash, retains same-worker View identity, replaces
the View/holder on worker change, checks a valid new Surface, and invokes late old
callbacks before and after replacement. It asserts old cancel/detach target A,
disposal releases A exactly once and no old callback reaches B. It does not kill a process, run a guest,
prove BufferQueue internals or qualify recovery from an actual dead CPU producer.
These tests are staged UNRUN until allocation.

A later allocated target check must repeat the actual normal Stop/fresh-worker
restart and separately the already-retained failed Stop/explicit recovery route
without deleting state. Bind the exact main/engine/source/old-new PID+Binder
incarnations. Capture a current recognizable guest raw frame and UI Surface before
Stop; after the new worker becomes ready, capture its new raw image and actual UI
Surface with coherent source/copied/presented sequence/time, window generation,
lock/post/geometry/copy error deltas and awake/backlight state. Inspect that the UI
shows the new worker's changed scene rather than the old power-off frame. Retain
normal confirmed Stop separately. No recovery/visible target pass follows from
this source fix or synthetic Surface test.

## Allocated Poco check after the candidate is sealed

Use one matching main/test pair and its source/engine manifest. Candidate25 is
currently prepared, not a sealed APK or a target result. Record the installed APK
hash, app UID, selected record ID, worker PID/start ticks and main-process PID at
each boundary. Do not substitute the old, main19-bound unlock diagnostic.

1. **Unused-worker control test.** Before starting any guest, run
   `WorkerGuestSurfaceTest#newWorkerCreatesNewSurfaceViewAndRetiredCallbacksCannotReachIt`
   with `exerciseWorkerSurfaceReplacement=true` and
   `expectedMainApkSha256` equal to the installed main APK hash. Retain its
   `surface-worker-evidence-*/result.json` and actual instrumentation exit. This
   proves the tested UI callback/holder contract with fake endpoints only. It
   must not replace the following real-worker check. If wire evidence is wanted,
   arm it on the still-unused real worker before the operator starts the guest.
2. **Real retained-record entry.** Use normal Start for a clean selected record;
   use explicit Recover retained state only for an actually dirty retained
   record. Preserve the old failed-worker files and marker evidence. Do not
   manufacture an unconfirmed Stop or clear a marker for this check. Wait for
   an inspected recognizable guest scene before issuing agent/UI RPCs. Logo,
   agent liveness and a positive producer counter are not this gate.
3. **Current-worker presentation.** Retain a raw guest PNG, device Surface PNG and
   status/observations immediately around each capture. On an awake static
   scene require a positive copied `guestFrameSequence` equal to the current
   `producerFrameSequence`, nonzero RGB samples, `windowAttached=1`, and a recent
   `framePublishedHostNs` relative to the same native `hostMonotonicNs` clock.
   Use the existing frame-readiness test's age bound; do not subtract Java uptime
   or device wall time from native timestamps. During animation, allow a later
   publisher sequence only if a subsequent bounded sample catches up; repeated
   copies of an aged image are a failed or incomplete gate. Inspect both PNGs:
   the UI must show this worker's scene, not a retired power-off/keyboard scene.
4. **Same-record clean restart.** Before Stop, retain a recognizable scene and
   the above identity/counter snapshot. Request production Stop and retain the
   durable receipt for that exact PID. Clean means an actual valid native poll
   snapshot with stopped state, shutdown confirmed, dirty false, storage failure
   false and engine exit zero, plus absent dirty marker and retired worker.
   Missing/unconfirmed receipts stay unconfirmed. Start the same clean record in
   a fresh worker, verify the changed PID/start ticks and new native presentation
   session, then repeat the raw/Surface/scene gate. A previously dirty recovery
   entry and this clean restart are separate results.
5. **Error and progress deltas.** Within each PID, retain `windowFailures`,
   first/last lock/post errors, geometry failures, invalid buffers and copy
   failures before attachment/ready and after a changed guest scene. Record
   `presentedFrames`, copied/producer sequences, surface generation,
   presentation epoch and presenter phase/timestamp alongside them. Never
   subtract counters across worker PIDs or hide early failures by resetting the
   baseline after readiness. A new active worker's repeated `-22` lock failures,
   unchanged present count or stale Surface image reproduces the blocker even
   when raw copying and the agent work. Historical first/last error fields alone
   are not new failure deltas. Retain startup errors separately from any later
   retired-Surface events; an unexplained positive startup count is not a clean
   presentation result.

After that gate, continue Product's matching-candidate input/media plan on the
same current worker: mapped two-pointer Photos pinch with IDs7/23 and independent
lift; concurrent two-contact screenshot sound/frame presentation; orientation,
recreate, pause and audio-focus lifecycle; then the sustained declared workload.
Foreground before each `--no-restart` instrumentation invocation and resume
explicitly after an ActivityScenario closes. Re-check the raw/Surface identity,
freshness and error deltas after each lifecycle transition. Do not clear existing
fatal worker errors to admit a test. A correctly classified viewport cancellation
is a nonempty rejected gesture with bounded reason/history; require local IDs to
drop and a fresh DOWN before the next gesture. Nonfinite input or release failure
remains an actionable failure. The synthetic View tests do not qualify actual
outside-viewport guest delivery, all-packet asynchronous delivery, audible sound
or sustained performance; retain those distinctions in the final result.
