# LightTouch

LightTouch runs classic iPhone OS and iOS in an emulator on **Android, Linux,
Windows and macOS**. This fork extends
[LightTouchMac](https://github.com/samhenrigold/LightTouchMac) around the shared
[qemu-ios engine](https://github.com/blake-lucas/qemu-ios).

The desktop ports share one device-library/setup UI and the same guest-window
controls. Android uses a touch-oriented UI. Prepared systems can be reused by
several saved devices, each with its own writable storage.

This is a development project. Working features and test coverage differ by
platform; builds are previews, not a fully qualified cross-platform release.

## Current platforms

| Platform | Build | Current status |
| --- | --- | --- |
| Android | APK; ARM64 and x86_64, Android 10+ | Real guest boot, graphics, input and audio exercised on Poco and Pixel hardware. Complete lifecycle, concurrent media and 4/16 KiB compatibility acceptance is ongoing. |
| Linux | x86_64 AppImage | Guest boot, input, accelerated graphics and audio exercised, including a Radeon desktop. Local IPA installation works; Legacy Store HTTPS trust/logging fixes are included. |
| Windows | x86_64 ZIP | Shared desktop UI preview available. Earlier native preview boot/audio/controls were user-tested; the shared package and Apps services still need Windows qualification. |
| macOS | Apple Silicon app ZIP | Shared desktop UI and native host compile/package successfully. Shared guest execution and Apps services remain unqualified. Current packaged dependencies require macOS 26. |

Intel macOS and 32-bit Android hosts are not qualified. Virtual devices supplement
physical testing; their graphics drivers do not reproduce every phone GPU.

## Getting started

Use the supplied platform development package: launch the Linux AppImage, install
the Android APK, extract the Windows ZIP and run `LightTouch.cmd`, or extract the
macOS ZIP and open its app. The local development handoff collects packages,
checksums, build logs and component revisions together under
`port-workspace/deliverables/session-wrap-20261016/`, outside this repository.
Upstream notarized macOS releases remain available from
[LightTouchMac releases](https://github.com/samhenrigold/LightTouchMac/releases).

Firmware and prepared guest assets are supplied separately from these port
packages. Start with a compatible prepared system:

1. Choose an existing prepared base folder or import its `.itbase` archive.
2. Select the matching guest-tools `.itpack`: ARMv6 for the iPod touch 2G/n72,
   ARMv7 for the iPod touch 4G/n81.
3. Supply board-specific boot assets when requested. The n72 route needs its
   matching SecureROM; a prepared n81 system boots through its included
   `kboot.bin` and needs no separate hardware boot ROM.
4. Save the configuration and start it. Use the virtual Home, Power, volume,
   Pause/Resume, rotation and screenshot controls in the desktop guest window.
5. Use **Stop** and wait for confirmed shutdown before closing. Saved devices
   keep independent writable state; the prepared base remains immutable.

The desktop **New from selected** action creates a fresh device configuration
using the same imported system, without copying another device's saves. Existing
saved configurations can be selected without choosing all their files again.
See [Linux setup](Linux/launcher/README.md),
[Android build/run notes](Android/BUILD-AND-RUN.md) and
[shared desktop documentation](Desktop/README.md) for platform details.

## Firmware and performance

The main exercised baseline is **iPod touch 2G, iOS 3.1.3 (n72ap-7E18)**. A
prepared iPod touch 4G/iOS 4.2.1 route is also present, with further qualification
in progress. Upstream's broader
[firmware catalog](LightTouchMac/Resources/firmware-catalog.json) and device list
are preparation references, not verified compatibility claims for these ports.
A selected firmware/device matrix covering iPhone OS/iOS 1–7 is planned; betas
and untested preparation routes are outside the current cohort.

Guest CPUs currently run through **QEMU TCG dynamic translation**. Hardware
virtualization is not an implemented guest CPU backend. Host graphics acceleration
is used by the supported rendering paths. The persistent translation-cache
experiment remains **disabled and unqualified** on Linux and Android; no
cross-session speed improvement is claimed. The performance display measures the
host QEMU worker and host memory, not CPU/RAM use inside iOS. Gyroscope support
is deferred and unqualified.

Legacy Store search/download and local IPA installation share desktop service
code. Linux has the working service route; Windows/macOS provider integration
and actual guest service acceptance are unfinished.

## Building and contributing

Both maintained forks use **`main`**. Start with the coordinated desktop entrypoint:

```sh
python3 Desktop/build.py --matrix
```

[Desktop/README.md](Desktop/README.md) documents Linux AppImage, shared Windows
staging and native macOS build/package recipes. Builds use the appropriate
platform toolchains and explicit dependency inputs. Android currently uses its
[Gradle/native build route](Android/BUILD-AND-RUN.md). See also
[Linux packaging](Linux/packaging/README.md),
[macOS shared host](macOS/SharedDesktop/README.md), and
[session handoff](SESSION-HANDOFF.md).

[CONTRIBUTING.md](CONTRIBUTING.md) retains upstream Cocoa/Xcode development and
firmware-preparation instructions. Compile/package success is distinct from
actual guest acceptance. Use focused checks for changed behavior, preserve
working saves and retain failed test evidence.

## License and credits

LightTouch is free software under GPL version 2 or, at your option, any later
version; see [LICENSE](LICENSE). Upstream authorship and licenses are retained.

It builds on [LightTouchMac](https://github.com/samhenrigold/LightTouchMac),
[qemu-ios](https://github.com/samhenrigold/qemu-ios),
[QEMU](https://www.qemu.org),
[devos50's qemu-ios](https://github.com/devos50/qemu-ios),
[libimobiledevice](https://libimobiledevice.org), libusbmuxd, libplist, libtatsu,
the upstream usbmuxd fork, iBoot32Patcher, FFmpeg, GLib, libslirp, PCRE2, pixman,
OpenSSL and Unrar.swift with RARLAB's UnRAR. Preserve each package's bundled
license and source notices.

iPhone, iPod touch and iPad are trademarks of Apple Inc. LightTouch is not
affiliated with Apple.
