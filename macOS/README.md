# Native macOS development build

This builds the macOS port with the Command Line Tools: qemu-ios with TCG and its existing
accelerated CGL/OpenGL renderer, the native SDL host and the `LightTouchDevice` engine helper.
It does not port the Linux EGL path to macOS or enable CPU hardware virtualization. Swift 6.2
or newer is required.

Only native ARM64/macOS 26 has been compiled here so far. The Swift frontend's
14.4 deployment target does **not** qualify that operating system floor for the
bundle: the chosen Homebrew dylibs may require a newer system. Intel,
universal builds, distribution signing/notarization and guest acceptance are
unqualified.

## Dependencies and engine

Install the macOS Command Line Tools and native dependencies. A trusted existing
Light Touch.app supplies ancillary tools/dylibs (`--vendor-contents`); this
recipe does not rebuild those dependencies.

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

The macOS app is the shared Tk/SDL shell (`Desktop/`) with the `LightTouchDesktopHost` and
`LightTouchDevice` Swift products from the root `Package.swift`. `macOS/build-shared.py`
compiles them, stages the shared Python sources and a supplied self-contained Python/Tk
runtime, relocates the engine's dylib closure, then ad-hoc signs and verifies the bundle.
See `macOS/SharedDesktop/README.md` for its arguments and the receipt it writes. The recipe
refuses to replace an existing app. The upstream Cocoa app, its Xcode project and its
`LightTouchServices` helper are no longer part of this repository.

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
