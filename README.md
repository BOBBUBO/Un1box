# LightTouch

LightTouch runs classic iPhone OS and iOS in an emulator on **Android, Linux, Windows and macOS**. It is a fork of [Light Touch for Mac](https://github.com/samhenrigold/LightTouchMac), built around a shared [qemu-ios engine](https://github.com/blake-lucas/qemu-ios).

The desktop ports share one device library and setup UI. Android has its own touch UI. A prepared iOS system can back several saved devices, each with its own storage.

[Download the latest pre-release](https://github.com/blake-lucas/LightTouch/releases) (v0.0.8). These are early builds: what works differs by platform, as listed below.

## Current platforms

| Platform | Build | Status |
| --- | --- | --- |
| Android | APK, ARM64 (Android 10+) | The main test platform, on a Poco F3 and a Pixel 10. Prepares devices from an IPSW on the phone. Graphics, audio, touch, tilt, the Legacy Store and IPA installs work. |
| Linux | x86_64 AppImage | Boot, input, accelerated graphics, audio, the Legacy Store and IPA installs work. Prepares devices with `firmwarekit` from the command line. |
| Windows | x86_64 ZIP | Boot, graphics, audio and Legacy Store installs confirmed by users. Cannot prepare devices yet: copy a prepared `.itbase` from another computer. |
| macOS | Apple Silicon app ZIP | The shared desktop UI builds and packages. Guest services are not yet fully checked. Needs macOS 26. Upstream's [Light Touch for Mac](https://github.com/samhenrigold/LightTouchMac/releases) is the tested Mac app. |

## Devices

| Device | iOS | Status |
| --- | --- | --- |
| iPod touch 2G | 3.1.3 | **Tested**: the main target, fastest. Games such as Flight Control and Jetpack Joyride run at or near 60 fps on recent phones. |
| iPod touch 4G | 6.1.6 | **Tested, slow**: starts and runs on Linux and Android; slower than the 2G. |
| Others in the [firmware catalog](Portable/firmware/firmware-catalog.json) | iPhone OS 1 to iOS 7 | Can be prepared and tried; not yet checked on LightTouch. [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md) records what has been run. |

## Getting started

Install or unpack the build for your platform. You also need an iOS system for it to run. The [asset guide](docs/ASSETS.md) covers both routes:

- **Prepare one from Apple's IPSW.** On Android, tap **Add a device › Prepare from an IPSW**, pick the device and iOS version, and the app downloads the IPSW and prepares it on the phone (the iPod touch 2G takes about a minute). On Linux and macOS, `firmwarekit` does the same. Every platform uses the same preparer ([Portable/firmware](Portable/firmware/README.md)).
- **Use an already prepared system** (`.itbase`), for example the one `tools/get-assets.py` takes from upstream's Mac app, along with its guest tools (`armv6.itpack` or `armv7.itpack`) and, for the iPod touch 2G, its boot ROM.

Then start the device. Use the on-screen Home, Power and volume buttons, and **Stop** to shut iOS down cleanly. Each device keeps its own storage, and the prepared system is never changed.

## Performance

Guests run through QEMU's TCG dynamic translation; there is no hardware virtualization of the guest CPU. Much of this fork's engine work is speed: cross-page chaining, indirect-branch caches, fewer big-lock waits, and a faster OpenGL ES path. The history and measurements are in [docs/PERFORMANCE.md](docs/PERFORMANCE.md). On Android the vCPU is kept on the phone's fast cores. The performance overlay shows host-side engine load and memory, not CPU use inside iOS.

## Building and contributing

`scripts/build.sh` builds the engine, the Linux frontend, the Android APK, `firmwarekit` and (on a Mac, over ssh) the guest tools; `scripts/build-release.sh` builds a release for all four platforms. Windows and macOS app builds run through `Desktop/build.py` on their own hosts. [docs/BUILDING.md](docs/BUILDING.md) covers the inputs, and [CONTRIBUTING.md](CONTRIBUTING.md) the repository layout, firmware preparation, tests and style. Development happens on `dev/*` branches of this repository and the engine; releases come from `main`.

## License and credits

LightTouch is free software under GPL version 2 or, at your option, any later
version; see [LICENSE](LICENSE). Upstream authorship and licenses are retained.

It builds on [LightTouchMac](https://github.com/samhenrigold/LightTouchMac),
[qemu-ios](https://github.com/samhenrigold/qemu-ios),
[QEMU](https://www.qemu.org),
[devos50's qemu-ios](https://github.com/devos50/qemu-ios),
[libimobiledevice](https://libimobiledevice.org), libusbmuxd, libplist, libtatsu,
the upstream usbmuxd fork, iBoot32Patcher, FFmpeg, GLib, libslirp, PCRE2, pixman,
OpenSSL, Swift and its Foundation, swift-crypto, ZIPFoundation and Unrar.swift with RARLAB's UnRAR. Preserve each package's bundled
license and source notices.

iPhone, iPod touch and iPad are trademarks of Apple Inc. LightTouch is not
affiliated with Apple.
