# Un1box

<p align="center"><img src="./Picsart_26-10-25_03-23-04-384.jpg" alt="Un1box" width="700"></p>

**Un1box** is the Android app that runs classic iPhone OS and iOS on your phone: pick a device and a version, prepare it on the phone, and a real, touchable iOS device appears in a window. **Android only, for now** — the desktop ports in this repository (Linux, Windows, macOS) still ship under their **LightTouch** names and are unchanged.

Un1box is a fork of **[Light Touch](https://github.com/blake-lucas/LightTouch)** — and of [Light Touch for Mac](https://github.com/samhenrigold/LightTouchMac) before it — built around the shared [qemu-ios engine](https://github.com/blake-lucas/qemu-ios). LightTouch gave this project its emulator, its device preparation, its engine work and most of its code; Un1box adds its own Android app, its own name and its own direction. Everything LightTouch does, it still does here: the settings, the store, the device library and the prepared-system format are the same, so a device prepared by either one can be opened by the other.

A prepared iOS system can back several saved devices, each with its own storage.

## Communities

Discord server: https://discord.gg/ZpEkAV47H9
Telegram channel: https://t.me/hyperhle

## Download

[Un1box builds](https://github.com/KlugKlugTG/Un1box/releases) — CI builds every commit. The plain LightTouch release, including the desktop builds, is on [upstream](https://github.com/blake-lucas/LightTouch/releases/latest), v0.0.11.

Un1box is still early and things can change between versions.

## Platforms

| Platform | Build | Status |
| --- | --- | --- |
| **Android** | APK, ARM64 (Android 10+) | **This is Un1box.** The main test platform, on a Poco F3 and a Pixel 10. First launch walks you through preparing a device from an IPSW on the phone. Graphics, audio, touch, tilt, the back camera and IPA installs work, and the Legacy Store can be browsed like the website, with several installs queued in the background. |
| Linux | x86_64 AppImage | The LightTouch desktop app (Rust), built from this repository. Prepares devices from an IPSW; accelerated graphics, audio, internet inside iOS, the Legacy Store and IPA installs work. Needs glibc 2.31 or later. |
| Windows | x86_64 ZIP | The same app as on Linux. Preparing devices, graphics, audio, Legacy Store installs and internet confirmed on Windows 10 and 11. |
| macOS | Apple Silicon app ZIP | The same app as on Linux. Preparing devices, running them and Legacy Store installs checked on an M-series Mac; internet not yet. Needs macOS 26. Upstream's [Light Touch for Mac](https://github.com/samhenrigold/LightTouchMac/releases) is the tested Mac app. |

## Devices

| Device | iOS | Status |
| --- | --- | --- |
| iPod touch 2G | 3.1.3 | **Tested**: the main target, fastest. Games such as Flight Control and Jetpack Joyride run at or near 60 fps on recent phones. |
| iPod touch 2G | 4.2.1 | **Tested** |
| iPod touch 3G | 4.2.1 | **Tested** |
| iPod touch 4G | 6.1.6 | **Tested**: on a Pixel 10, Infinity Blade and Infinity Blade II run at full speed and Epic Citadel at 60 fps. Most other games, such as Jetpack Joyride, Doodle Jump and Real Racing, run at or near 60 fps on recent phones. |
| iPhone 3GS | 5.1.1 | **Tested, slow** |
| iPhone 4 | 4.2.1, 4.3.5 | **Tested, slow** |
| Others in the [firmware catalog](Portable/firmware/firmware-catalog.json) | iPhone OS 1 to iOS 7 | Can be prepared and tried; not yet checked on Un1box. [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md) records what has been run. |

## Getting started on Android

Install the APK and tap **Get started**. Pick a device and iOS version, and the app downloads iOS and prepares it on the phone (the iPod touch 2G on iOS 3.1.3 takes about a minute). You don't need any files of your own; if you already have the IPSW, you can choose it instead of downloading. The iPod touch 1G and 2G and the original iPhone also need a boot file, which the app downloads for you.

Then start the device: the on-screen Home, Sleep/Wake and volume buttons are under the display, and **Stop** shuts iOS down cleanly. Each device keeps its own storage, and the prepared system is never changed.

Because Un1box has its own package (`org.un1box.android`), Android treats it as a separate app from LightTouch: it installs beside it rather than over it, and each keeps its own prepared devices.

## The desktop ports

Unpack the build, then get an iOS system for it. The [asset guide](docs/ASSETS.md) covers both routes:

- **Prepare one from Apple's IPSW** with `firmwarekit` on Linux or macOS. Every platform uses the same preparer ([Portable/firmware](Portable/firmware/README.md)).
- **Use an already prepared system** (`.itbase`) made elsewhere, along with its guest tools (`armv6.itpack` or `armv7.itpack`) and, for the iPod touch 2G, its boot ROM.

## Performance

Guests run through QEMU's TCG dynamic translation; there is no hardware virtualization of the guest CPU. Much of this fork's engine work is speed: cross-page chaining, indirect-branch caches, fewer big-lock waits, and a faster OpenGL ES path. The history and measurements are in [docs/PERFORMANCE.md](docs/PERFORMANCE.md). On Android the vCPU is kept on the phone's fast cores. The performance overlay shows host-side engine load and memory, not CPU use inside iOS.

## Building and contributing

The desktop app and `firmwarekit` (the preparer every platform uses) are one Rust workspace: `cargo build` ([docs/DESKTOP.md](docs/DESKTOP.md)). `scripts/build.sh` builds the engines, the Android APK and (on a Mac, over ssh) the guest tools; `scripts/build-release.sh` builds a release for all four platforms. [docs/BUILDING.md](docs/BUILDING.md) covers the inputs, and [CONTRIBUTING.md](CONTRIBUTING.md) the repository layout, firmware preparation, tests and style. Development happens on `dev/*` branches of this repository and the engine; releases come from `main`.

Tests that need no build, no device and no CI run:

```sh
python3 tests/branding/run.py    # the name: label, package, sources, JNI
python3 tests/idle/run.py        # the app's idle loops
python3 tests/mic/run.py         # the microphone path
```

## License and credits

Un1box is free software under GPL version 2 or, at your option, any later version; see [LICENSE](LICENSE). Upstream authorship and licenses are retained.

It exists because of **[Light Touch](https://github.com/blake-lucas/LightTouch)** by blake-lucas, which itself builds on [Light Touch for Mac](https://github.com/samhenrigold/LightTouchMac) by Sam Henri Gold. Un1box's name and Android app are its own; the emulator underneath is theirs.

It also builds on
[qemu-ios](https://github.com/samhenrigold/qemu-ios),
[QEMU](https://www.qemu.org),
[devos50's qemu-ios](https://github.com/devos50/qemu-ios),
[libimobiledevice](https://libimobiledevice.org), libusbmuxd, libplist, libtatsu,
the upstream usbmuxd fork, iBoot32Patcher, FFmpeg, GLib, libslirp, PCRE2, pixman,
OpenSSL, egui and the Rust crates listed on each app's licence page. Preserve each package's bundled
license and source notices.

iPhone, iPod touch and iPad are trademarks of Apple Inc. Un1box is not
affiliated with Apple.
