# Build and run the Android development candidate

This guide follows the verified candidate18 build. It is a Debug development APK, with arm64-v8a and x86_64, API29 minimum and API35 compile/target. The main/test APKs are built together; running Android instrumentation is a separate step.

| Source | Candidate18 revision |
| --- | --- |
| Android UI, Binder and tests | `4cdea763896f689f1cbd8bad8a982877f972685f` |
| Portable domain, firmware and CActivation | `53ea530954411460d915aace69249398801c4101` |
| QEMU engine and JNI bridge headers/helper, both ABIs | `91a43a76d582b8c0aff91005142055fb7cad5ca9` |

All62 tracked native/JNI/CActivation files at the two app revisions were checked byte-identical. Candidate18 adds the viewport ownership fix and bounded wake diagnosis without changing native input APIs. Keep `Packages/FirmwareKit/Sources/CActivation` beside `Portable` when exporting native source. Omitting it makes firmware CMake fail. Source export must also retain the catalog at `LightTouchMac/Resources/firmware-catalog.json`.

For an archived Android build, export the catalog explicitly alongside Android:

```sh
git archive "$LT_APP_REVISION" Android LightTouchMac/Resources/firmware-catalog.json \
  > android-source.tar
```

An Android-only archive silently makes Gradle's `prepareFirmwareCatalog` task
`NO-SOURCE`. Candidate19's APK lacked the asset; preparation could rely on a
previously provisioned app-private catalog. Candidate20 exports the tracked
catalog from its Android revision `3409cce3b21d9617723c80ac88665944f51553c3` and
verifies `assets/firmware-catalog.json` against that source before handoff. A
successful APK build alone does not catch this omission. Check the archive and
packaged asset even when using a warm build cache.

## Inputs and build

Use the tool versions in [README](README.md#pinned-tools): JDK17, NDK28.2.13676358, CMake3.22.1, SDK/build-tools35, pinned Gradle8.11.1/AGP8.9.2/Kotlin2.1.20. Obtain the pinned sources and a verified artifact cache before an offline build. Keep sources, compiled outputs and guest inputs separate.

The verified build requires these supplied local inputs:

- Real Bionic engine inputs for **both** ABIs: each directory contains `libqemu-arm.so`, its producer manifest, the matching NDK `libc++_shared.so`, and its audited notice bundle. Build/relink recipes are in QEMU's `contrib/portable/README.md`; host/glibc libraries cannot substitute.
- Same-NDK static PIC firmware dependency prefixes under `deps/{arm64-v8a,x86_64}/prefix`, including pinned OpenSSL, plus the hash-verified XPwn checkout or selected source export. Firmware CMake verifies the selected source inputs.
- The frozen candidate02 x86 ANGLE three-library/closure/notice inputs and frontend notice bundle. Use their sealed producer manifest SHA256 pins; keep these legal build inputs separate from firmware/ROM/guest assets. ARM uses the platform provider; packaging ANGLE does not enable it automatically.

Set the following placeholders to your verified source/input directories and to the publisher's sealed manifest pins. The recipe below is the candidate18 command with machine-local paths parameterized; all properties were used in the tested build.

```sh
export ANDROID_HOME=/path/to/Android/Sdk
export JAVA_HOME=/path/to/jdk-17
export CMAKE_BUILD_PARALLEL_LEVEL=2
LT_ANDROID_SRC=/path/to/android-4cdea-source
LT_NATIVE_SRC=/path/to/native-53ea-source
LT_QEMU_SRC=/path/to/qemu-91a-source
LT_INPUTS=/path/to/verified-build-inputs
LT_XPWN_SRC=/path/to/pinned-xpwn-source
LT_BUILD=/path/to/private/new-android-build
# Set LT_ANGLE_CLOSURE_SHA, LT_ANGLE_NOTICE_SHA and LT_FRONTEND_NOTICE_SHA
# from the sealed public-input manifest receipts, not from unverified downloads.
cd "$LT_ANDROID_SRC/Android"
./gradlew --offline --no-daemon --max-workers=2 \
  '-Dorg.gradle.jvmargs=-Xmx768m -XX:MaxMetaspaceSize=384m' \
  -PnativeJobs=2 -PtargetAbis=arm64-v8a,x86_64 \
  "-PportableDir=$LT_NATIVE_SRC/Portable" \
  "-PfirmwareDir=$LT_NATIVE_SRC/Portable/firmware" \
  "-PqemuSource=$LT_QEMU_SRC" "-PxpwnSource=$LT_XPWN_SRC" \
  "-PnativeDepsRoot=$LT_INPUTS/deps" "-PengineRoot=$LT_INPUTS/engine" \
  "-PengineNoticeArm64=$LT_INPUTS/engine/arm64-v8a/notices" \
  "-PengineNoticeX86=$LT_INPUTS/engine/x86_64/notices" \
  -PsourceRevision=4cdea763896f689f1cbd8bad8a982877f972685f \
  -PsourceReference=https://github.com/blake-lucas/LightTouch/tree/4cdea763896f689f1cbd8bad8a982877f972685f \
  "-PbuildRoot=$LT_BUILD" \
  "-PangleLibraries=$LT_INPUTS/angle/lib" \
  "-PangleManifest=$LT_INPUTS/angle/closure.json" \
  "-PangleManifestSha256=$LT_ANGLE_CLOSURE_SHA" \
  "-PangleNotices=$LT_INPUTS/angle/notices" \
  "-PangleNoticesSha256=$LT_ANGLE_NOTICE_SHA" \
  "-PfrontendNotices=$LT_INPUTS/frontend" \
  "-PfrontendNoticesSha256=$LT_FRONTEND_NOTICE_SHA" \
  :app:assembleDebug :app:assembleDebugAndroidTest \
  :app:testDebugUnitTest --tests 'org.lighttouch.android.*'
```

The tested build passed the ten JVM cases and compiled the real-View, dirty-admission and concurrent-media instrumentation classes. Preserve source archives, exact argv/environment, producer manifests and input-to-packaged strip mappings. Different local paths/debug signing keys can change APK bytes; compare the actual payload provenance rather than assume every new build matches a sealed APK hash.

Check the APK before installing:

```sh
LT_APK="$LT_BUILD/app/outputs/apk/debug/app-debug.apk"
LT_TEST_APK="$LT_BUILD/app/outputs/apk/androidTest/debug/app-debug-androidTest.apk"
python3 scripts/check-apk.py "$LT_APK"
"$ANDROID_HOME/build-tools/35.0.0/apksigner" verify --verbose "$LT_APK"
"$ANDROID_HOME/build-tools/35.0.0/apksigner" verify --verbose "$LT_TEST_APK"
```

Candidate18's sealed checks covered all17 DSOs, uncompressed16KiB APK/ELF alignment, ABI/SONAME/platform-or-packaged dependency closure,247 notice payload hashes and exact input-or-NDK-strip mappings. The firmware corresponding-source kit covers its declared subset; complete app/engine sources, static-library relink material, source availability and whole-APK licensing eligibility remain separate reviews. Use the matching producer receipts rather than reuse a kit for a different binary.

## Install and supply private guest inputs

These are device-owner steps; stop/retain any existing session before updating. Installation and data preservation depend on the same package/signing key. Choose one authorized device explicitly:

```sh
LT_DEVICE_SERIAL=your-authorized-device
adb -s "$LT_DEVICE_SERIAL" install -r "$LT_APK"
adb -s "$LT_DEVICE_SERIAL" install -r "$LT_TEST_APK"
adb -s "$LT_DEVICE_SERIAL" shell am start -W -n org.lighttouch.android/.MainActivity
```

Normal use: import the legitimate matching guest package and optional ROM through the pickers, then import a prepared `.itbase`/folder. Each validated import creates a new record and independent state; select the intended board/build. n72's ROM is a separately supplied legitimate input. Prepared-kernel boards use native validated `kboot.bin` and do not require an arbitrary boot ROM. Start, Home, Power, Pause/Resume, Screenshot and Stop are semantic controls. Backgrounding pauses/cancels input; reopen and explicitly Resume. Guest Home/Photos readiness requires inspecting real guest pixels and response.

For the fixed private-fixture harness, the owner may provision verified inputs into Debug app storage at `files/private-fixtures/n72ap-7E18.itbase`, `files/bootrom.bin`, and `files/guest.itpack`; the harness expects its specific verified baseline, not arbitrary assets. Use a local authorized transfer into those paths, or the normal pickers. For example, a verified private package can be streamed without staging it in public/shared storage:

```sh
adb -s "$LT_DEVICE_SERIAL" shell "run-as org.lighttouch.android sh -c 'cat > files/guest.itpack'" < /path/to/private/matching-guest.itpack
```

Verify transferred bytes against the owner's trusted inventory before invoking the opt-in import test. Keep guest sources, prepared outputs, app-private state, logs and captures outside Git and cloud uploads. Do not delete `.dirty` or replace a baseline to recover a session. Ordinary Start rejects interrupted state; **Recover retained state** explicitly cold-boots preserved writable data in a fresh worker. Confirmed guest halt and successful engine cleanup, not timeout/exit0 alone, clear dirty state.

## Preparation, tests and current limits

**Prepare from stock IPSW** uses the shared native userspace recipe in a separate same-UID firmware service. Supply legitimate matching stock firmware and the trusted handoff root containing `inventory.json` plus `release-guest-unpacked/guest-tools`; the picker copies only required inputs. The local inventory is integrity evidence, not the missing original exporter manifest. No historical SDK is acquired or synthesized. Native discovers supported catalog routes, validates outputs/package hashes and imports a new record. Completion/import is distinct from boot acceptance.

Linux uses the same preparer and Portable domain; see [firmware preparation](../Portable/firmware/README.md) and [Linux frontend](../Linux/README.md). There is no Android-specific firmware recipe or boot-argument list. A locally prepared base must still boot, accept input, halt cleanly and preserve state on each required target.

Foreground MainActivity before every `am instrument --no-restart` test. Inspect JUnit output and private receipts: shell exit0 is not a JUnit pass. Ordinary instrumentation completion can terminate the target; choose session holds/stop/recovery deliberately. A safe first host-admission check uses:

```sh
adb -s "$LT_DEVICE_SERIAL" shell am start -W -n org.lighttouch.android/.MainActivity
adb -s "$LT_DEVICE_SERIAL" shell am instrument --no-restart -w -r \
  -e class org.lighttouch.android.GuestDisplayAdmissionTest \
  org.lighttouch.android.test/androidx.test.runner.AndroidJUnitRunner
```

Candidate18 passed all three real View/fake Binder cases on Poco, including unowned resize preserving an unrelated button pulse; this is not native button delivery or guest input proof. Candidate17b passed actual dirty admission with unchanged metadata and explicit recovery/QMP diagnosis to a lit lock screen. Its later wake diagnosis remained black, and Stop disposed the worker after the deadline with dirty state retained. Shutdown receipts are best effort: a missing receipt does not establish a blocked call or a clean halt. See [wake diagnosis](WAKE-DIAGNOSIS.md) for the optional150–500ms host hold and its separate guest-clock limits.

Required guest multi-contact response, concurrent touch/render/nonzero audio, full lifecycle, sustained20-minute session, clean same-state reboot/persistence, prepared-stock boot and both AVD page-size gates remain subject to their own retained acceptance. See [candidate17 sequence](CANDIDATE17-ACCEPTANCE.md) and [concurrent/media recipes](CONCURRENT-ACCEPTANCE.md) for opt-ins and manual checks.

Gyro/motion is explicitly deferred/unqualified and does not block the initial goal. Do not enable experimental motion, build a probe/SDK or advertise modeled board gyro as working. Android display/audio/touch/lifecycle and durability remain required.
