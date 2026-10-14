# Pixel boot diagnosis overlay

`GuestPixelBootDiagnosisTest.kt` is a test-only overlay outside normal source
sets. Export the selected frozen production Android source/catalog into a new
private directory, copy this file into its Android instrumentation source set,
and build only the matched test APK with the same native/dependency inputs and
independent outputs. Record source/overlay revisions, hashes, command, log,
main/test signature equality and absence of test native payloads. The integrator
provides `expectedMainApkSha256` from the sealed main APK receipt (including APK24
when selected); the test requires 64 lowercase hex digits and hashes the actual
installed APK. There is no default or previous-APK fallback. This is an identity
check, not a guest acceptance claim. Existing sealed artifacts remain untouched.

## Original clock and after-Start admission

After a separate compiler/AVD allocation, use `pixel_supervisor.py`, the Pixel
adapter around the unchanged bounded supervisor, with the same allocation
arguments and a new Pixel10/API36/16KiB attempt. Reuse the encrypted imported base through
the reviewed external-COW route in COMPARISON.md. A retained dirty record requires
explicit `-e pixelColdRecovery true`; otherwise the overlay requires a clean
record. It checks actual selected metadata and records priorDirty/recovery mode.
Recovery does not claim clean initial admission. Capture the actual supervisor's original process clock once immediately
after launch:

```sh
python3 Android/scripts/pixel/capture_origin.py \
  --attempt /private/current-attempt --supervisor-pid ACTUAL_PID \
  --exec-session ACTUAL_SESSION --avd LightTouch_pixel_10_API36_16384
```

Schema2 `clock-context.json` derives `originNs` directly from process start ticks.
It cannot overwrite an existing context. Generate a unique32-digit lowercase hex
token and programmatically hash the unchanged clock-context bytes. Run this
instrumentation asynchronously on only the allocated emulator:

```sh
adb -s emulator-5554 shell am instrument -w -r --no-restart \
  -e class org.lighttouch.android.GuestPixelBootDiagnosisTest \
  -e diagnoseVirtualPixel true -e recordId VERIFIED_NEW_RECORD_ID \
  -e expectedMainApkSha256 SEALED_MAIN_APK_SHA256 \
  -e pixelAttemptToken UNIQUE_32_LOWERCASE_HEX \
  -e pixelOriginToken SHA256_OF_ORIGINAL_CLOCK_CONTEXT \
  -e pixelObserveSeconds 300 -e allowOneSleepWake true \
  org.lighttouch.android.test/androidx.test.runner.AndroidJUnitRunner
```

The diagnostic fixes packaged x86 ANGLE and arms private read-only QMP before
Start. It atomically publishes `ready.json` after Start, including UID/PID/start
ticks, device boot ID and pre/post-Start Android elapsed times. While the test
waits, invoke the host admission command:

```sh
python3 Android/scripts/pixel/admit_observation.py \
  --adb /path/to/selected/adb --attempt /private/current-attempt \
  --token SAME_UNIQUE_32_LOWERCASE_HEX
```

The host checks current UID/boot/record/worker identities and publishes
`pixel-bound.json` for scoped cleanup. It then brackets a fresh device BOOTTIME
read with host MONOTONIC times, allowing precision, delivery and a1s margin and
reserving20s for readback. The test waits at most20s and verifies the full300s
still fits its explicit device limit. Late setup/Start/admission refuses the
interval; it never shortens300s or backdates the origin. The original
UI240/readback810/Stop840/hard900 bounds remain. Distinct epochs are not directly
subtracted or treated as proof of equal future clock rates. Host completion and
readback must still be witnessed by original810; the device limit supplements
the independent original watchdog.

The Activity stays foreground for the full admitted interval, recording native
observations, raw images and CPU snapshots every10s. QMP can perturb timing and
is diagnosis, not a performance benchmark. One explicitly enabled balanced150ms
Home pulse follows observed sleep without a pre-wake agent-liveness requirement.
Inspect the actual subsequent image; admission/counters do not prove a response.
Eventual Scenario.close may pause the guest; the supervisor owns semantic Stop
and strict shutdown verification. Retain failures and writable state unchanged.

## Bounded evidence before disposal

The Pixel adapter runs a one-time readback hook before Stop/cleanup, including
early worker/instrumentation failures. Capture runs in a separate child capped
at10s and original810, with disposal reserve. Stalled ADB/local IO cannot hold
normal Stop/owned cleanup. The900s hard watchdog is unchanged. Any still-alive
capture child is explicitly recorded, not counted as retired.

`diagnostic_control.py` copies only the declared token's ready/result/timeline,
derived raw frame/QMP paths and bound record/PID engine/Stop logs. It rejects
scope/path/symlink, boot, UID, PID-start or process-role mismatches; caps one file
at4MiB and received remote data at32MiB; and records remote exits even when ADB
returns0. Permission failure is not absence. Proven worker absence permits only
historical bound files, never a live verdict. Partial reads/errors and forced
child disposal are retained in private receipts. Receipt failure does not
suppress Stop or owned cleanup.

The hook does not acquire an Android surface screenshot or classify a scene.
Take a separately bound surface image during live observation when required.
Normal scheduled Stop occurs at840, after the810 readback gate; its expired hook
does not override that gate. Collect successful/live diagnostic files before810
with a new private output directory:

```sh
python3 Android/scripts/pixel/diagnostic_control.py \
  --adb /path/to/selected/adb --attempt /private/current-attempt \
  --output /private/current-attempt/unique-live-readback
```

Reproducible host tests use synthetic ADB responses and do not prove target
behavior:

```sh
python3 -B -m unittest discover -s Android/scripts/pixel/tests -v
```

No helper/test receipt declares Home, physical Pixel equivalence, audible output,
persistence or a speedup. x86 SwiftShader cannot qualify Tensor ARM64/vendor GPU
compatibility. Gyro remains deferred.

## Memory comparison and observation

Use `--pixel-memory-mb 4096` or `8192` on the Pixel adapter (never combine with
`--memory-mb`). Both use the original fixed deadlines. `memory_monitor.py` records
host meminfo/vmstat/swap/PSI and zram provenance from launch through Stop and
owned cleanup; it samples guest memory/swap/PSI individually, with unavailable
reads explicitly null. Worker status/PSS is bounded by UID/start-ticks/role checks
before and after the read; denied PSS remains unavailable. Host swap counter
deltas include actual host page bytes. Requested cadence is2s; actual command
times and gaps are retained, so this is not a promise of gapless sampling.

Before owned cleanup, guest reads are disabled and acknowledged; host monitoring
continues. A stalled acknowledgement forces bounded monitor retirement and
records the resulting coverage gap. The sampler never retries/restarts an AVD,
changes swap/memory settings, or accepts a guest. A final host-only lifecycle
snapshot is recorded even after emulator disposal. Monitor failure cannot
suppress the original cleanup. COMPARISON.md defines the controlled experiment.

## Explicit existing dirty recovery binding

The retained dirty library uses `--fresh-library` only for the existing
supervisor's service/UI gate. It does not mean a fresh/clean library and does not
run import. Do not create an `import-record.json` receipt. Before Start use the
allocated attempt's token and actual original-record pins:

```sh
python3 Android/scripts/pixel/bind_recovery.py \
  --adb /path/to/selected/adb --attempt /private/current-attempt \
  --token SAME_UNIQUE_32_LOWERCASE_HEX --record DECLARED_RETAINED_RECORD_ID \
  --record-sha256 VERIFIED_RECORD_SHA256 --lock-sha256 VERIFIED_LOCK_SHA256 \
  --rom-sha256 VERIFIED_ROM_SHA256 --guest-sha256 VERIFIED_GUEST_SHA256
```

The readonly preflight caps10s and original490, verifies the original supervisor
and owned AVD process, current app UID/boot, unique engine PID/start/role, selected
record, metadata/base/bootrom/guest hashes and successful regular dirty-marker
read. It publishes `recovery-record-bound.json` once and writes no app state.
The sealed Kotlin still checks its actual worker is unused before recoverStart.
Run the test with `pixelColdRecovery=true`; after-Start admission rejects any
change from the preflight worker/record/clock/token or recovery mode.

Stop rechecks the original PID/start/UID/role/boot/selection and records that
record's dirty marker. Missing/mismatched identity produces unconfirmed Stop
admission, never a Stop sent to a replacement worker. Owned cleanup still runs.
No import, normal clean-record admission or lifecycle pass is inferred.

## Single identity and clean subsequent Start

`plan_manifest.build` constructs every stage from the same token, record, output,
mode and frozen main hash. Before any VM launch use the generated
`launchCheckedCommand`: the actual CLI checks all stage identities and actual
source/controller/launcher/APK hashes, requires Root's single-attempt allocation
and consumes it once. A copied stale admission token is rejected before exec.

The next same-child normal Start is supported by the actual03 native clean Stop
receipt, not a deleted marker. It uses the ordinary `--record-id` clean-record
supervisor gate and `coldRecovery=false`, and binds actual initialized state with
confirmed absent dirty marker. Permission denial is not absence. Historical03
files are for scoped later retrieval; they cannot prove a current guest scene.

Run generated `readyWatcherCommand` concurrently with diagnosis. It polls only
that attempt's token through original490, retains exact ready bytes immediately,
and publishes admission through the original after-Start clock/identity check.
The Kotlin20s wait and full300s are unchanged. Client completion is bounded by
original800, preserving10s finish plus10s scoped readback through810; setup time
is included and no fixed330s cap truncates the measured56s setup plus300s.

The supervisor itself now uses original process ticks, including CLI validation
and imports, for all gates/watchdog. Worker memory sampling and early-failure
readback can use the actual pre-Start binding when admission never publishes a
post-Start binding. Captures remain explicitly unconfirmed and never imply a
full interval, visible boot, physical Pixel or audible-output pass.
