# Rebuilding the frame-stall diagnostic pair

The frame timeline collector needs an engine containing both read-only QOM
properties, `frame-scan-counters` and `portable-frame-counters`. A test-only APK
update cannot add those properties to an already installed engine. An older
engine produces an explicit unsupported-engine receipt.

The minimum Poco build is one fresh ARM64/API29 QEMU engine and one new
instrumentation APK. The engine's exported API and SONAME stay unchanged, so
an engine-only diagnostic Main APK can retain the existing managed code,
Portable/JNI libraries, graphics providers, firmware tools and x86 engine.
Replace the ARM64 engine and its complete verified notice bundle, then align
and sign the APK again. No JNI or firmware preparation rebuild is necessary
for this isolated diagnostic. A universal release still needs independent
qualification of every packaged ABI.

Use `scripts/repack-diagnostic-engine.py` after the native engine build and
source/notices audits have completed. It requires the exact original Main APK
hash, the clean engine revision, the engine manifest and matching notices. It
rejects a changed C++ runtime and existing output. Every unrelated APK payload
must remain byte-identical. It removes old JAR signatures, writes an **unsigned**
APK and records per-entry preservation/replacement hashes. Its result requires
`zipalign -P 16`, signing with the original certificate, and independent
signature/alignment/payload checks. It never installs or starts anything.

For example, using privately supplied paths and exact hashes:

```sh
python3 Android/scripts/repack-diagnostic-engine.py \
  --base-apk ORIGINAL_MAIN.apk --base-sha256 ORIGINAL_MAIN_SHA256 \
  --engine-root ENGINE_INPUTS --notices ARM64_ENGINE_NOTICES \
  --engine-revision EXACT_QEMU_COMMIT \
  --output diagnostic-unsigned.apk --receipt diagnostic-repack.json
zipalign -P 16 4 diagnostic-unsigned.apk diagnostic-aligned.apk
# Sign diagnostic-aligned.apk with the original certificate into a fresh file.
# Verify that certificate, zipalign -c -P 16 4, and every receipt payload hash.
```

Compile the collector from its exact committed source against the existing
pinned Main outputs. The private test-only Gradle recipe disables all Main and
native tasks and uses independent writable copies of the cached outputs. This
is compile-classpath reuse; it does not prove that source is the producer of
the original Main binary. Keep those source identities separate. Preserve the
old source packet, signed APKs and private device state.

Before target testing, bind the newly signed Main/Test hashes, actual engine
identity, unused worker PID/UID/start time and a clean isolated guest state.
Arm the fixed QMP endpoint before Start with `armFixedQmp=true` in
`GuestSessionSetupTest#armExistingUnusedWorkerBeforeOperatorStart`, retaining
the existing ANGLE and touch-wire admission. Never reuse a spent worker or
substitute old keyboard/scene bindings. For the 30-second visible-phase pilot,
keep `keyboardVisiblePhases=true`, `captureFrameTimeline=true`,
`requireConcurrentMediaWindows=true`, `requireFrameProgress=true` and
`requireGuestAudio=true`. Supply the newly inspected keyboard binding.

Retain the raw QMP transcript, exact counter bodies, timeline text, native
images, input/audio/frame samples and strict Stop receipt. Inspect the actual
images and compare scan, dirty-update, publication, native acquisition and
presentation boundaries. The counters alone do not prove guest pixels changed,
UIKit consumed input, audible playback, or sustained acceptance. Keep all
original overlap predicates and deadlines; a failed pilot stays failed. A
30-second diagnostic does not complete the required 20-minute session.
