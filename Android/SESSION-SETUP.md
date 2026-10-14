# Arm a selected unused worker (test-only)

For the matched candidate23 main/test AIDL, Root can add only GuestSessionSetupTest
to the sealed2d33 instrumentation source and build an isolated23b test APK.
Candidate23 main/native/engine binaries remain unchanged; runner source/hash is
separate. This helper never starts/resumes a guest, launches an ActivityScenario,
sends input, restarts a worker or pauses on close. It binds the existing service,
requires stopped/sessionStarted=false/no valid native session/no produced frames
and empty error, then arms fixed touch trace and optional fixed QMP. It records
actual main APK SHA, UID/PID, before/after status and exact admission results.
Its local binding omits BIND_AUTO_CREATE, and required expectedWorkerPid must
match before any arming. Missing/retired worker fails without creating/retrying it.

Root foregrounds MainActivity first and controls the platform instrumentation
lifetime/outer deadline. Planned invocation after the new runner is sealed:

```sh
adb -s "$LT_SERIAL" shell am start -W -n org.lighttouch.android/.MainActivity
adb -s "$LT_SERIAL" shell am instrument --no-restart -w \
  -e class org.lighttouch.android.GuestSessionSetupTest \
  -e armGuestSession true -e armTouchWireTrace true \
  -e expectedWorkerPid "$LT_EXPECTED_UNUSED_WORKER_PID" \
  -e expectedMainApkSha256 49565cd70fa9a83d99c2865707055313ec2cf373e85c13dcbc907401e4c71fa2 \
  org.lighttouch.android.test/androidx.test.runner.AndroidJUnitRunner
```

Add `-e armFixedQmp true` only for the separate approved fixed diagnostic choice.
Retain files/session-setup-evidence-*/result.json and full JUnit output. The arm
call returns admission success; touchTraceConfigured is consumed/published only
at actual Start and is not asserted true by this helper. No trace event delivery,
QMP endpoint, guest boot or acceptance is inferred. After instrumentation, Root
must independently confirm the same worker still lives and later Start reports
touchTraceConfigured=true; platform instrumentation completion can affect target
process lifetime, which the helper itself cannot guarantee. No automatic retry
or replacement if the worker was consumed/retired. Current helper source is
uncompiled until allocation; these are planned commands, not an executed trial.
