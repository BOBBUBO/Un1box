# Android frontend

User scope D12 (2026-10-14): gyro/motion qualification is deferred, unqualified
and excluded from initial acceptance. Automatic experimental motion listeners
are disabled by default. Preserve existing APIs/adapters without further gyro
integration, probe/SDK work or claims of working guest gyro. Multi-touch, display,
audio, durability and lifecycle remain required.

Kotlin/Compose UI binds to a same-UID `:engine` process. Only that process loads
JNI and QEMU. Binder carries controls/status and timestamped bounded input batches;
video renders directly into a transferred Surface. Surface references are acquired
and released in native code, frames copy under the runtime/engine frame lock,
latest frame wins, and no video frame travels in a Binder parcel. A native CPU
conversion from BGRA8888 to Surface RGBA8888 preserves the first correctness path.

The shared `Portable/include/lighttouch.h` owns validation, device state, writer
leases, board discovery, boot arguments, touch transforms and motion conversion.
Android passes stable MotionEvent pointer IDs; never pointer indices as identities.
Rejected whole touch batches trigger native release of all owned contacts and
abort the remainder of that Android gesture. Async guest rejection is polled and
reported; queue admission alone is never displayed as delivered input.
View-local pixel positions map through the same native letterbox function used by
Linux. Guest portrait remains upright inside the current Android view; touch must
not apply physical display rotation a second time. Sensor vectors remain Android
natural-device coordinates and use the shared display rotation conversion. Sensor
units are m/s² (accelerometer including gravity) and rad/s (calibrated gyroscope).
The shared mapper translates SensorEvent BOOTTIME timestamps to host monotonic
age, rejects stale samples and advertises guest capabilities separately. Host
sensor availability never implies guest gyro availability.

## Pinned tools

- Gradle 8.11.1 (wrapper distribution SHA256 verified on download)
- Android Gradle Plugin 8.9.2; compile/target API 35, build-tools 35.0.0
- Kotlin + Compose compiler plugin 2.1.20; UI/foundation 1.7.8, Material3 1.3.2
- JDK 17; NDK 28.2.13676358; CMake 3.22.1
- AndroidX Activity Compose 1.10.1; DocumentFile 1.0.1
- Native ABIs arm64-v8a and x86_64, minimum Android API 29

Official platform references, checked 2026-10-14:
[AGP 8.9 compatibility](https://developer.android.com/build/releases/agp-8-9-0-release-notes)
requires Gradle 8.11.1 and JDK17 and supports API35;
[Compose compiler setup](https://developer.android.com/develop/ui/compose/setup-compose-dependencies-and-compiler)
uses a compiler plugin matching Kotlin;
[16 KiB native support](https://developer.android.com/guide/practices/page-sizes)
requires aligned native dependencies and uncompressed APK entries. AGP≥8.5.1 and
NDK≥r28 provide compatible packaging/linking defaults. This app additionally
links JNI with max-page-size 16384. Verify the entire engine dependency closure,
not only JNI.

## Build

Build the real QEMU Android engine first using its pinned backend recipe. Stage
all Bionic shared libraries under `ENGINE_ROOT/arm64-v8a/` and
`ENGINE_ROOT/x86_64/`, including `libqemu-arm.so`. JNI and QEMU use the same NDK28.2 shared C++ runtime;
AGP packages it once from the NDK (`ANDROID_STL=c++_shared`). These private build outputs must
stay outside Git. No stub/fallback engine is accepted. The build fails explicitly
if a requested ABI is missing.

```sh
export ANDROID_HOME=/path/to/Android/Sdk
export JAVA_HOME=/path/to/jdk-17
cd Android
./gradlew --no-daemon --max-workers=2 \
  -PportableDir=/path/to/LightTouch/Portable \
  -PqemuSource=/path/to/qemu-ios \
  -PengineRoot=/path/to/android-engine-libraries \
  -PfirmwareDir=/path/to/LightTouch/Portable/firmware \
  -PxpwnSource=/path/to/pinned-xpwn \
  -PnativeDepsRoot=/path/to/android-dependency-prefixes \
  -PbuildRoot=/path/to/private/android-build \
  :app:assembleDebug
./scripts/check-apk.py /path/to/private/android-build/app/outputs/apk/debug/app-debug.apk
```

To build only the physical phone ABI while x86_64 is being compiled, add
`-PtargetAbis=arm64-v8a`. This does not qualify the AVD gate. Dependency verification metadata is checked in and Gradle checks SHA256 for the
pinned dependency closure. Regenerate intentionally with
`--write-verification-metadata sha256` when updating dependencies, then review
the changed versions/checksums.

## Private imports and state

The UI accepts a prepared `.itbase` archive or prepared base directory selected
through Android’s document picker; the native domain validates archive entries,
locked outputs and metadata and atomically publishes an independent copy. The
archive SHA256 is computed before native import to detect changes during
processing; it is a local integrity digest, not a publisher authenticity claim.
It also imports the matching guest tool package and, only for recipes requiring
it, the legitimate boot ROM into app-private
`files/bootrom.bin` and `files/guest.itpack` as inputs for the next import. Firmware
is never bundled. Every native record under `files/library` receives its own
immutable base, matching package/optional ROM copies, writable state and serial
log. The native library generates record IDs, persists selection atomically and
exposes board/build/capabilities to the device selector. Importing a second device
never replaces the first. The original selected files stay intact. A legacy
`files/base`/`files/state` pair is transactionally adopted only into an empty
library, preserving its dirty marker, overlay and source copies.
Prepared-kernel routes use the base’s validated kboot.bin and do not require a
hardware boot ROM. The native catalog/recipe owns compatibility checks; import success is not boot
acceptance. The first UI deliberately preserves an existing device rather than
replacing its state.

PNG screenshots use the actual copied guest frame, retain a private copy in
`files/screenshots/`, and publish to MediaStore Pictures/LightTouch without broad
storage permission. A debug build can export them with `adb exec-out run-as
org.lighttouch.android cat files/screenshots/NAME.png` using an authorized device.

## Lifecycle policy

Backgrounding pauses the guest and cancels held input; foreground return requires
an explicit Resume. Audio focus loss also pauses and cancels input; focus gain
resumes only a guest paused by focus, never a guest paused by the user or activity. Sensors unregister while inactive.
Surface loss releases contacts and native Surface ownership. The local foreground
service is `specialUse`, with explicit subtype local user-controlled legacy guest
emulation; notification permits return to the app. See
[foreground service types](https://developer.android.com/develop/background-work/services/fgs/service-types).
No Google services, root privileges or isolated-UID storage exceptions are needed.
The foreground notification permission can be denied without affecting user input.

Stop requests guest shutdown and polls native confirmation for at most 15 seconds.
The domain alone decides clean versus dirty storage. A rejected shutdown request or timeout is preserved in private
`files/worker-stop-error.txt` and shown after reconnect, while native dirty metadata
remains authoritative. The process exits after Stop;
restart binds a newly created worker and never invokes QEMU twice in one process.
Native/OS crashes also preserve the dirty marker and original/base. Cold recovery
uses the native existing overlay, with dirty state visible in status. No snapshot
capability is claimed.

Audio drains only actual bounded signed-16-bit PCM into an AudioTrack configured
from native sample rate/channel metadata. Unsupported audio remains unsupported;
no generated PCM can stand in for guest audio acceptance.

## Acceptance

`tests/semantic-smoke.yaml` checks the Android shell through semantic IDs. Private
local acceptance must additionally boot the real guest, exercise production touch
batches, compare rendered output, hear captured guest
audio, and restart with persistent data on the phone and both AVD page sizes.
An APK build or shell test does not prove those gates. Record native/backend/asset
revisions with logs, measured frame cadence, boot time and process IDs. The n72ap
baseline has no guest gyro. Gyro/motion acceptance is explicitly deferred and
not a completion gate. Modeled board flags are not qualified functionality.

## Debug instrumentation

The Debug variant alone contains a separate same-UID `:tcgProbe` service and
`lighttouch_probe` JNI library. It executes four legal ARM instructions on the
actual packaged QEMU `virt` machine and verifies RAM word42 through private QMP.
The worker exits after the probe; production UI never accepts custom guest argv.
This establishes app-UID CPU/JIT execution, not iOS boot or graphics.

```sh
./gradlew [same pinned build properties] :app:assembleDebugAndroidTest
adb install -r /path/to/app-debug.apk
adb install -r /path/to/app-debug-androidTest.apk
adb shell am instrument -w -e class org.lighttouch.android.TcgSmokeTest \
  org.lighttouch.android.test/androidx.test.runner.AndroidJUnitRunner
```

AndroidX Test Runner1.6.2 and JUnit1.2.1 are pinned; see the
[official AndroidX Test releases](https://developer.android.com/jetpack/androidx/releases/test).
`GuestInputTest` is opt-in (`-e exerciseGuest true`) and requires a legitimately
imported/selected device. Instrumentation may restart the app and its worker;
never launch it assuming a prior running guest stays alive. `settleSeconds`
(default60) gives the device owner time to confirm guest Home and open the chosen
built-in gesture workload. `gesture` selects pinch/cross/cancel/all. Pointer IDs7
and42 travel via real MotionEvents into GuestDisplay, Binder, JNI and native
controller input; independent lift changes pointer index without changing ID.
Screenshots and a trajectory manifest stay in private app storage. The retained motion
test is separately disabled unless experimentalMotion=true and is excluded from
initial acceptance. It does not establish working guest gyro. No gyro probe/SDK
build is performed. The production adapter remains disabled by default.
Both harness manifests explicitly require guest-response review; successful host
execution is not accepted as a guest-visible gesture/gyro result.

## Local userspace preparation

The preparation dialog reads firmware IDs from the original upstream catalog,
bundled as a generated build asset. Native `lt_firmware_supports` labels implemented
versus unsupported routes; it does not imply measured target acceptance. Pick a
legitimate stock IPSW and verified private handoff root containing `inventory.json`
and `release-guest-unpacked/guest-tools`. Only the required guest-tools subtree, inventory and IPSW are copied into
app-private storage; unrelated NAND fixtures, SDKs and captures stay at their
original location.
The local inventory is trusted hash evidence, not the unavailable original exporter
manifest; the historical Apple SDKs are not bundled or replaced. Native validates
stock firmware/catalog identity, local guest tool hashes and package payload tables.

A separate same-UID `:firmware` foreground service loads only the firmware JNI
library, performs native userspace preparation with progress/cancel, and exposes
explicit completion/failure/restart-required status. It executes no extracted
programs and mounts no filesystem. A complete prepared output is validated/imported
into a fresh shared native device record; its declared matching guest.itpack is
selected by native code. Recipes, architecture/tool selection and boot arguments
remain native. Each new job uses a fresh firmware worker. Interrupted staging trees
are never admitted as devices, and an existing device is preserved on failure.

Native build requires the firmware source, pinned XPwn checkout and static PIC
OpenSSL prefix at `nativeDepsRoot/{arm64-v8a,x86_64}/prefix` (include/openssl and
lib/libcrypto.a), built with the same NDK. External symlinks may adapt a private
build layout. Preparation is not G6 acceptance until the resulting base boots on
Linux and unrooted Android with actual guest rendering and usable input.

Private fixture import is explicit opt-in instrumentation:
`-e class org.lighttouch.android.PrivateFixtureImportTest -e importPrivateFixture true`.
Provision `files/private-fixtures/n72ap-7E18.itbase`, `files/bootrom.bin`, and
`files/guest.itpack` privately with the verified handoff hashes. The harness
uses production `IEngine.importBase`, checks record preservation and selection,
checks that status remains responsive during native validation, and writes
`files/import-evidence-*/result.json`. Import success does not prove guest boot.
UI status polling uses one background request at a time; native startup does
integrity admission outside the frame/control mutex and publishes handles only
when ready. Stop during admission is retained and applied when startup returns.

Real Android userspace preparation acceptance is also opt-in:
`-e class org.lighttouch.android.FirmwareAcceptanceTest -e preparePrivateFirmware true`.
Privately stage `files/acceptance-fw/stock.ipsw`,
`files/acceptance-fw/handoff/inventory.json`, and its
`release-guest-unpacked/guest-tools` directory. Supply
`files/acceptance-fw/input-sha256.json`, mapping every staged input path relative
 to `acceptance-fw` to its verified SHA256. The test first cancels at the native
`write NAND` checkpoint after system edits, checks cleanup and unchanged inputs,
waits for Binder death, retries in a fresh same-UID worker, then validates and
imports the completed base through production IEngine. Its cancellation hook
exists only in Debug native builds and requires an explicit non-exported service
bind extra. Timings/results/stage traces are under `files/firmware-evidence-*`;
native preparer diagnostics remain at `files/prepared/job-*.native.log`.
Default timeout is 1800 seconds per job, overridden with
`firmwareTimeoutSeconds`. This establishes preparation/import only; guest boot
and the normal picker flow still require independent acceptance.
