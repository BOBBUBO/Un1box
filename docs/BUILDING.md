# Building (development)

`scripts/build.sh` builds what day-to-day work needs, with outputs saved under `build/` in this
repository (ignored by git). Object trees go in `build/obj/` and build incrementally.

```sh
scripts/build.sh linux                 # build/linux/libqemu-arm.so + build/linux/lighttouch-linux
scripts/build.sh android               # build/android/engine/arm64-v8a/ + build/android/LightTouch-debug.apk
scripts/build.sh portable-tests        # Portable/ unit tests (ctest)
LT_MAC=user@mac scripts/build.sh guest-package ltbench   # build/guest-package/*.itpack, build/ltbench/LTBench.ipa
scripts/build.sh all                   # linux android portable-tests (+ the Mac targets when LT_MAC is set)
scripts/build.sh --clean android       # reconfigure from scratch (after a configure-level change)
```

| Target | Output | Built from |
| --- | --- | --- |
| `linux-engine` | `build/linux/libqemu-arm.so` | qemu-ios (`$QEMU_IOS`), portable board, x86_64 |
| `linux` | the engine and `build/linux/lighttouch-linux` | `Linux/CMakeLists.txt` (includes `Portable/`) |
| `android-engine` | `build/android/engine/arm64-v8a/` (`libqemu-arm.so`, `libc++_shared.so`, manifest, notices) | qemu-ios with NDK clang, API 29 |
| `android` | the engine and `build/android/LightTouch-debug.apk` (arm64-v8a, debug) | `Android/` Gradle |
| `portable-tests` | test results | `Portable/` with `-DLIGHTTOUCH_BUILD_TESTS=ON` |
| `guest-package` | `build/guest-package/{armv6,armv7}.itpack`, `build.log`, `VERSION`, `SHA256SUMS` | qemu-ios `contrib/guest-package/build.sh`, run on the Mac over ssh |
| `ltbench` | `build/ltbench/LTBench.ipa` | `tools/guestbench/build.sh`, on the Mac over ssh |

Windows and macOS app builds are not in this script. `Desktop/build.py` runs their recipes on their
own hosts (`Desktop/README.md`), and `Linux/packaging/README.md` has the AppImage packaging.

## Engine changes

An engine change happens in the qemu-ios checkout (`$QEMU_IOS`, default `~/git-projects/qemu-ios`,
branch `dev/perf`). `scripts/build.sh android` (or `linux`) rebuilds it incrementally and packages it
again. Known issue: the first configure of a fresh Android tree has once stopped at QEMU's meson
postconf with `FileNotFoundError: .../pyvenv/bin/meson` although the file exists afterwards; running
the same command again built normally. The engine is QEMU's `qemu-system-arm` link turned into a shared library by qemu-ios
`contrib/portable/make-shared.py`. The build keeps the link's response file (`ninja -d keeprsp`), which
that step reads. The Android APK checks that the engine notice bundle names the library by SHA-256, so
the script rewrites `notices/manifest.json` after each engine build.

Engine A/B switches are environment variables. On Android a debug APK reads them from the app's
`files/debug-env` (`KEY=VALUE` lines), so one APK can be measured both ways (`docs/PERFORMANCE.md`).

## Inputs that are not in this repository

Set these in the environment or in `scripts/build.env`, which the script sources if present (not
tracked). The defaults are the development machine's paths.

| Variable | Default | What it is, and where it came from |
| --- | --- | --- |
| `QEMU_IOS` | `~/git-projects/qemu-ios` | the engine source (github.com/samhenrigold/qemu-ios fork, `dev/perf`) |
| `LT_INPUTS` | `~/Documents/LightTouch-Development/port-workspace/builds` | prebuilt inputs: `backend/deps/arm64-v8a/prefix` and `backend/deps-linux/prefix` (glib, pixman and the rest), plus `backend/crypto/*` (gcrypt), built by qemu-ios `contrib/portable/build-deps.py` and `build-crypto-deps.py` (see that directory's README); `mobile-android/arm64-v8a` (the USB bridge helpers `libusbmuxd_qemu.so` and `liblt_appservice.so`, from `Android/packaging/build-mobile-android.py`); `graphics/angle-android-arm64-gl-build-01/candidate-01` (ANGLE for arm64, with its ELF closure and notices); `product/frontend-inputs-15` (the frontend notice bundle). The APK build checks the ANGLE and frontend bundles against the SHA-256 pins in the script. |
| `LT_ENGINE_KIT` | `~/Documents/LightTouch-Development/perf/engine-api29/arm64-v8a` | the NDK's `libc++_shared.so`, the engine notice bundle and a fallback `portable-manifest.json`, which go next to an Android engine |
| `ANDROID_HOME`, `ANDROID_NDK_VERSION`, `JAVA_HOME` | `~/Android/Sdk`, `28.2.13676358`, JDK 17 | Android SDK, NDK and JDK (`Android/README.md` lists the pinned tool versions) |
| `LT_XPWN` | `~/.hermes/cache/scratch/lighttouchwin-xpwn` | the XPwn source the firmware tools build from |
| `LT_GUEST_TOOLS` | `build/guest-package` | the `armv6.itpack` and `armv7.itpack` the APK carries (`-PguestToolsDir`); new devices get the matching one, and IPSW preparation takes its guest files from `armv6.itpack` |
| `LT_MAC` | unset | `user@host` of a Mac for `guest-package` and `ltbench` |
| `LT_MAC_DEVELOPER_DIR`, `LT_MAC_IPAD_SDK` | Xcode.app; `~/Developer/ipod2g-re/OldSDK/iPhoneOS3.2.sdk` on the Mac | see the Mac below |
| `JOBS` | `nproc` | parallel jobs |

## The Mac (guest package and LT Bench)

The guest package (the tools inside iOS: the GL shim, the agent, `it_boot` and the rest) and LT Bench
are armv6 iOS binaries. They link with Xcode's `ld` on a Mac. qemu-ios
`contrib/armv6-toolchain/README.md`, "Setting up a Mac to build the guest package", lists what that Mac
needs, where each piece came from, and what does not work: Xcode 27.1 (not Command Line Tools alone),
the iPhoneOS 3.1.3 and 3.2 SDKs, and `ldid`. LT Bench needs no SDK. The script copies the sources over
with `rsync` (to `~/ltpkg-build` on the Mac), builds there, and copies the results back.

The iPhoneOS 3.1.3 SDK (headers and link stubs for the guest link) also ships in Apple's original
installer, which the Internet Archive keeps in its iOS Firmware Exhibit Collection:
[`iphone_sdk_3.1.3_with_xcode_3.2.1__snow_leopard__10m2003a.dmg`](https://archive.org/download/iOS_Firmware_Exhibit_Collection/iphone_sdk_3.1.3_with_xcode_3.2.1__snow_leopard__10m2003a.dmg).
Not yet tried as a replacement for the third-party mirror the current SDK came from (docs/TODO.md).

A guest package reaches a device only through its record: `records/ID/assets/guest.itpack`, pinned by
`guest_package_sha256` in `record.json`. A newer serial installs over the existing system at the next
boot, and the runtime's verdicts (`Portable/src/GuestPackageJudge.hpp`) keep it installed. Don't
publish built packages without the project owner's go-ahead.

## Checking a build

- `scripts/build.sh portable-tests`.
- Desktop, headless:
  `build/linux/lighttouch-linux --library LIB --record ID --engine build/linux/libqemu-arm.so --headless --seconds 120 --no-audio`.
  It boots a copy of a record and prints the runtime's status lines. Add `--package ITPACK` to offer a guest package.
- Phones: `tools/perf/fcbench.py build/android/LightTouch-debug.apk 30 jetpack` (with `ANDROID_SERIAL`
  set) installs the APK, boots, starts the game and measures it. `docs/PERFORMANCE.md` describes the
  benchmark set and the A/B method, and `docs/GUEST-BENCHMARKS.md` describes LT Bench.

## Release builds

Releases follow the sealed recipes, not this script: `Android/BUILD-AND-RUN.md` (exact source pins,
both ABIs, notice verification), `Linux/packaging/README.md` (AppImage) and `Desktop/README.md`.
