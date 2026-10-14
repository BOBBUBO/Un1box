# Candidate19 existing-session unlock diagnosis

`GuestUnlockTest` is test-only and opt-in. It requires the exact sealed candidate19
main APK and an already-started same-UID worker; its initial binding omits
`BIND_AUTO_CREATE`. It does not import, start, recover, enable tracing, invoke a
guest RPC, save a guest photograph or enable motion. Root must already have
inspected the n72 Lock layout; the test does not classify screens automatically.

After the new isolated test APK is built and installed by the device owner, keep
the existing main APK and guest state unchanged. Foreground MainActivity before
`--no-restart`, then use the matching runner:

```sh
adb -s "$ANDROID_SERIAL" shell am start -W -n org.lighttouch.android/.MainActivity
adb -s "$ANDROID_SERIAL" shell am instrument --no-restart -w -r \
  -e class org.lighttouch.android.GuestUnlockTest \
  -e exerciseGuestUnlock true -e observedN72LockLayout true \
  -e resumeExisting true \
  org.lighttouch.android.test/androidx.test.runner.AndroidJUnitRunner
```

This is a planned invocation, not a recorded unlock pass. A paused session needs
explicit `resumeExisting`; a stopped/failed/unused worker is rejected. If asleep,
the test admits balanced Power DOWN/UP with500ms host hold, then polls for at most
15s for running, native sleep0, positive backlight, producer growth and nonzero
RGB samples. UID/PID/session identity must remain unchanged. An already-awake
static frame can fail this strict producer-growth gate; cached presentation is
not substituted for a fresh producer observation.

While one ActivityScenario remains resumed, it captures the raw frame and sends
one stable pointer7 through the production GuestDisplay: guest-image fractions
(.20,.92) to(.85,.92),15 moves scheduled across600ms, then UP. Actual aspect-fit
viewport geometry is recorded. A changing View/viewport or rejected/cancelled
contact fails rather than silently continuing another gesture. Raw captures are
scheduled at UP+300ms and UP+1500ms before activity closure. Per-move evidence is
buffered in memory so file writes do not consume the short awake window.

Private `unlock-evidence-*` contains result, status timeline and MotionEvents;
captures stay under `files/screenshots/<evidence-folder>`. An already-configured
worker trace is copied through the bounded existing wire reader after the swipe;
it is never enabled late. Cleanup attempts UP if needed and always full input
cancellation. Inspect JUnit plus retained receipts and images. All guest-unlock,
visual/audible and guest-acceptance flags remain false pending root review.

BOOTTIME host deadlines, MotionEvent uptime and guest virtual trace time have
different epochs. Button/touch admission is not application or guest consumption.
The owner's external execution deadline is still required for Binder/platform
calls. Instrumentation completion may terminate the target package; retain the
evidence before interpreting subsequent lifecycle or dirty-state outcomes.

Build this new runner from candidate19's frozen Android/AIDL sources plus the
reviewed test file only. Record both source revisions and file hashes; exclude
native/main APK tasks and leave the sealed main APK byte-identical. No compiler
or target run was performed by the product lane at this source checkpoint.
