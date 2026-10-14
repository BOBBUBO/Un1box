# Pixel APK22 boot diagnosis overlay

`GuestPixelBootDiagnosisTest.kt` is a test-only overlay, kept out of the normal
Android source sets. Export Android and the firmware catalog from production
commit `05d6a5ef06a2a05d3055fd3307c3327644ae2c22` into a new private directory,
then copy this one file into its
`Android/app/src/androidTest/java/org/lighttouch/android/` directory. Build only
`:app:assembleDebugAndroidTest`, with the original APK22 native/dependency inputs
and independent build outputs. The production APK remains unchanged. Record the
base revision, overlay revision/hash, build command/log, test APK hash/signature
and absence of native payloads. A successful compilation is not target diagnosis.

After a separate explicit compiler/AVD allocation, use the bounded supervisor
and a new Pixel10/API36/16KiB record. Reuse the matched production import test for
the verified compact reference bundle. The diagnostic starts a fresh unused
worker, fixes packaged x86 ANGLE and arms the private read-only QMP endpoint
before Start. It keeps the Activity foreground for up to300 seconds, retaining
runtime observations, raw guest images and CPU snapshots every10 seconds.

```sh
# Use only the allocated emulator, a verified new record and original deadline.
adb -s emulator-5554 shell am instrument -w -r --no-restart \
  -e class org.lighttouch.android.GuestPixelBootDiagnosisTest \
  -e diagnoseVirtualPixel true -e recordId VERIFIED_NEW_RECORD_ID \
  -e pixelObserveSeconds 300 -e allowOneSleepWake true \
  org.lighttouch.android.test/androidx.test.runner.AndroidJUnitRunner
```

Set the operator's command timeout from the original supervisor origin: never
start if a300-second observation plus readback cannot finish before810 seconds.
The UI240/Stop840/hard900 bounds remain fixed. Native QMP snapshots can perturb
timing and are diagnosis, not a performance benchmark. One explicitly enabled
balanced150ms Home pulse follows the first observed sleeping state without
requiring an already-live guest agent. Capture the actual image afterward; button
admission and a changed frame counter do not prove a responsive guest.

The old provider test ended at the first coherent logo and closed its Activity,
which could pause the guest. This overlay deliberately retains foreground
observation through the full interval. Its own eventual Scenario.close may pause
the guest; the supervisor owns semantic Stop and strict receipt verification.
Read back the scoped full engine log and proc identity/CPU counters during the
same worker lifetime. Persist model inspection of actual raw and surface images,
not automated Home classification. Retain failures and writable state unchanged.

No receipt from this test declares guest Home, physical Pixel equivalence,
audible playback, persistence or a performance improvement. x86 SwiftShader
cannot qualify Tensor ARM64/vendor GPU compatibility. Gyro remains deferred.
