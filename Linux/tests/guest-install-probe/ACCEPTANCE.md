# Actual Linux install acceptance handoff

This is an execution plan, not a guest result. Use the privately retained signed
original `InstallProbe.ipa` and its independent build/review receipt. Never use
the deliberately corrupted verification copies. The exclusive guest owner runs
the stages below after native controls have passed their own prerequisites.

## Before boot

Use the current matching frontend/engine/package, the read-only prepared base,
and a new isolated writable state. Reuse the prepared base in place; do not copy
the large fixture or expose assets through a public service. Start the actual
desktop launcher with an isolated `--data-dir` and the explicit bundled service
paths. `Launcher.start()` creates `Bridge` first, then passes its
`IT_USB_TCP=<private qemu.sock>` environment into the fresh native worker.

An already-running worker started without that environment cannot be made into
an install test by starting a daemon afterward. Stop it cleanly, then start the
proper session. Keep the same bridge alive through native orderly shutdown.
Use only that session's private `mux.sock`; never the system usbmux daemon.

Retain exact AppImage/AppDir/source hashes, base identity, state path/initial
dirty state, native command/environment differences, frontend PID/start time,
and admitted worker PID/start time. No parallel guest/phone/helper controller.

## Primary route: actual desktop UI

1. Start iOS through the launcher. Inspect fresh native pixels to establish the
   actual unlocked Home scene and working controls; use current calibration.
2. Open **Install apps…**, wait for its actual iPod2,1/iOS 3.1.3/ARMv6 reply,
   then choose **Install local IPA…** and the exact reviewed original IPA.
   Retain a screenshot of this real selection/status path and its newly created
   private `service-logs/app-*.jsonl` transcript. No catalog download is needed
   for this local-IPA acceptance test.
3. Wait for the helper's structured `installed: true` result and exit zero.
   Canonical `Mobile.install()` requires an actual Complete callback and a fresh
   installation_proxy browse containing `org.lighttouch.installprobe`.
   Stop/Close must remain blocked until the install helper retires. A timeout or
   error is unconfirmed: retain evidence and inspect before any retry.
4. Capture and inspect fresh actual Home pixels showing **LT Probe**. Its page
   and icon coordinates must be measured from that image; do not guess a fixed
   grid slot. Tap that icon through the real native touch path.
5. Capture and inspect the purple **LAUNCH CONFIRMED** screen. Tap its view once
   through native input, then capture and inspect blue **INPUT CONFIRMED**.
   These two changed app screens are the owned probe's launch/input proof.
6. Return Home and retain a fresh screenshot. Only after the service is inactive,
   request the launcher's **Stop iOS**. Wait for the strict terminal contract
   below; keep all artifacts/state even on a failure.

`guest launch org.lighttouch.installprobe` is deliberately unavailable: the
typed launch operation only accepts `com.apple.*` builtins. The gyro-probe
operation is not an alternative launcher. Open the actual visible app icon.
Frontmost metadata can supplement pixels but cannot replace them.

## Bounded service diagnostic, if the UI path needs isolation

`install_acceptance.py` is plan-only by default and starts nothing without
`--execute`. It uses canonical `ServiceJob` and `app_services.helper`, performs a
30-second info check of the exact target before any install, verifies the reviewed
IPA SHA-256 and bundle, rechecks the bridge socket identity, and bounds the install
helper to 900 seconds. It retains JSONL/results and exact service source/library
hashes in a new private evidence directory. It never launches/stops a guest or
daemon and never sends UI input. Its service success leaves all visual/UI and
shutdown qualifications false.

Run this only while the exclusive owner holds the current session and no Apps
helper is active. Do not press Stop/Close or impose a shorter external timeout
during its install; this external controller cannot hold the launcher's own
`AppsWindow` Stop guard. A failed/unconfirmed install must not auto-retry.

```sh
python3 Linux/tests/guest-install-probe/install_acceptance.py --action install
```

For execution use the AppDir's existing bundled interpreter/runtime environment
and the exact current Bridge socket, not a newly started or guessed daemon:

```sh
env PYTHONHOME="$APPDIR/usr" PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
  LD_LIBRARY_PATH="$APPDIR/usr/lib:$APPDIR/usr/lib/lighttouch" \
  "$APPDIR/usr/bin/python3" -s "$PROBE_SOURCE/install_acceptance.py" \
  --action install --execute \
  --linux-source "$APPDIR/usr/share/lighttouch" \
  --socket "$SESSION_MUX_SOCKET" \
  --library "$APPDIR/usr/lib/lighttouch/libimobiledevice-1.0.so.6" \
  --plist-library "$APPDIR/usr/lib/lighttouch/libplist-2.0.so.4" \
  --ipa "$REVIEWED_IPA" --expected-ipa-sha256 "$REVIEWED_IPA_SHA256" \
  --evidence "$NEW_PRIVATE_EVIDENCE"
```

The shell variables are explicit owner-supplied paths, not discovery commands.
For an info-only diagnostic omit the IPA flags and use `--action info`. A service
diagnostic does not test the desktop chooser; retain that as a separate open
checkpoint even if subsequent icon/launch/touch succeeds.

## Evidence and shutdown contract

Use `Linux/scripts/capture-native-checkpoint.py --window ID --log LOG
--output-dir NEW_DIR` for each native checkpoint. Retain its actual BMP, preview,
matching equal/even coherent readback receipt and current source/diagnostic
timestamps. Inspect the screenshots yourself. A host GUI capture also records
the launcher/Apps status. Capture only on the owned display/window. Source
intent, helper exit zero, queued touch requests or a generic Home image do not
qualify the new app's visible behavior.

Canonical `launcher.session.clean_exit(frontend_exit, FINAL)` must return true:
`status_available=1 state=0 confirmed=1 dirty=0 worker_reaped=1 worker_exit=0
worker_disposal_requested=0 frontend_return=0`, plus frontend exit zero. Retain
the exact FINAL line and worker identity retirement. Black pixels or an exited
window are insufficient. After native exit, the launcher retires only its owned
Bridge. Preserve writable state and base identity; do not wipe or reinstall to
convert an unconfirmed result into a pass. A fresh restart with the same isolated
state and a visible LT Probe icon is useful persistence evidence, but it needs its
own fresh-worker and strict terminal receipts.
