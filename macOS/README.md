# Native macOS development build

This builds the existing Cocoa/Swift frontend and its two native helpers, plus
qemu-ios with TCG and its existing accelerated CGL/OpenGL renderer. It does not
port the Linux EGL path to macOS or enable CPU hardware virtualization. The
Xcode project remains the upstream release/archive route.

The Command Line Tools recipe needs Swift 6.2 or newer. Its explicit
`LIGHTTOUCH_CLT_BUILD` flag excludes the two Xcode-only preview macros and gives
the development app a separate default device library:
`~/Library/Application Support/org.lighttouch.native-test/State`.
Set `LTM_STATE_DIR` to override that with another private test directory.

Only native ARM64/macOS 26 has been compiled here so far. The Swift frontend's
14.4 deployment target does **not** qualify that operating system floor for the
bundle: the chosen Homebrew dylibs may require a newer system. Intel,
universal builds, distribution signing/notarization and guest acceptance are
unqualified.

## Dependencies and engine

Install the macOS Command Line Tools and native dependencies. A trusted existing
Light Touch.app supplies ancillary tools/dylibs and its resource artwork; this
recipe does not rebuild those dependencies. Sparkle 2.10.0 is the official
prebuilt SwiftPM XCFramework, pinned by the root `Package.resolved`.

```sh
brew install ninja meson jq pkgconf glib pixman libslirp openssl@3 libimobiledevice ffmpeg dtc
export PATH="/opt/homebrew/bin:$PATH"
python3 macOS/build-engine.py \
  --qemu-source /absolute/path/to/qemu-ios \
  --output /absolute/private/native-engine \
  --python /opt/homebrew/bin/python3.13 \
  --openssl-prefix /opt/homebrew/opt/openssl@3 --jobs 2
```

Use a fresh output path. For an exported source archive, also pass
`--archive-revision` with its recorded Git revision. Keep the
`native-engine-receipt.json` and step logs. The explicit OpenSSL link flags are
needed by the emulated board's big-number crypto implementation.

## Frontend and test bundle

```sh
python3 macOS/build-native.py \
  --vendor-contents "/Applications/Light Touch.app/Contents" \
  --engine /absolute/private/native-engine/libqemu-arm.dylib \
  --scratch /absolute/private/mac-swift-build \
  --output "/absolute/private/LightTouch Native Test.app" --jobs 2
open "/absolute/private/LightTouch Native Test.app"
```

For an exported app source archive, pass `--source-revision`; use
`--engine-revision` to record its separately compiled QEMU revision.
The recipe refuses to replace an existing app. It compiles `LightTouch`,
`LightTouchDevice` and `LightTouchServices`, copies and relocates the engine's
Homebrew dylib closure, then ad-hoc signs and verifies the development app.
The adjacent `.build-receipt.json` records final file hashes and distinguishes
newly compiled code from reused dependencies. Keep it beside the app.

This is a development bundle, not a fully rebuilt or notarized release. Named
vendor resources and guest tools are reused; `Resources/Device`, firmware,
prepared bases and writable state are excluded. Supply compatible private
assets separately. No guest was booted to qualify this build. Automatic update
checks are disabled for the test bundle. Retain upstream dependency licenses
and perform a source-distribution audit before redistributing it.

## Focused native checks

These compile and execute on macOS without firmware or a guest:

```sh
clang++ -std=c++17 -Wall -Wextra -Werror -Wno-deprecated-declarations \
  macOS/tests/CGLReadback.cpp -framework OpenGL -o /absolute/private/cgl-readback
/absolute/private/cgl-readback
clang -std=gnu11 -Wall -Wextra -Werror \
  -I /absolute/path/to/qemu-ios/contrib/ios-app macOS/tests/EngineAbi.c \
  -o /absolute/private/engine-abi
/absolute/private/engine-abi "/absolute/private/LightTouch Native Test.app/Contents/Frameworks/libqemu-arm.dylib"
```

The CGL check requires an accelerated legacy context, a 320x480 packed D24S8
framebuffer and correct clear/triangle readback. It is a host capability check,
not production guest rendering evidence. Engine ABI discovery opens the actual
shared library eagerly and lists its boards without entering `qemu_ios_main`.
Use two build jobs and a measured memory/time supervisor on shared machines;
keep existing emulators and guest state untouched.

`DeviceLinkSmoke.swift` checks 256 concurrent pre-start requests against the
actual compiled DeviceRuntime archive: exactly one closed-link reply per request
on the specified queue, and no helper or guest process:

```sh
swift build --package-path Packages/DeviceRuntime \
  --scratch-path /absolute/private/device-runtime-build --jobs 2
mac_bin=$(swift build --package-path Packages/DeviceRuntime \
  --scratch-path /absolute/private/device-runtime-build --show-bin-path)
swiftc -I "$mac_bin/Modules" -I Packages/DeviceRuntime/Sources/LTMLinkC \
  macOS/tests/DeviceLinkSmoke.swift "$mac_bin/libDeviceRuntime.a" \
  -framework Foundation -framework Security -framework IOSurface \
  -o /absolute/private/device-link-smoke
/absolute/private/device-link-smoke
```

The existing Swift Testing suite needs a toolchain that provides the `Testing`
module, typically full Xcode. On this Command Line Tools installation,
`swift test --package-path Packages/DeviceRuntime` compiled the library but
failed to compile the test target with `no such module 'Testing'`; no IPC-suite
pass is claimed. The standalone checks above do not replace that suite.
