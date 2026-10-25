# Desktop app (Rust)

One app for Linux, Windows and macOS, built with `cargo build --release`, replacing the Python/Tk launcher, the Linux
SDL worker, the Windows C# helper and the macOS Swift host. It aims for parity with the Android app: preparing
devices from an IPSW (ipsw.me / archive.org), the Legacy Store with specific app versions, IPA installs, and the same
look. Work happens on `dev/desktop` (app) and the engine's `dev/desktop`.

## Layout

| Crate | What it is |
| --- | --- |
| `lighttouch-sys` | The shared C++ core (`Portable/`, also used by Android) compiled by `cc`, plus raw bindings to `lighttouch.h`. The engine's two ABI headers are vendored in `engine-include/`. |
| `lighttouch-core` | Safe wrappers: device library, engine runtime, the worker line protocol, frames over shared memory, app paths. |
| `lighttouch-catalog` | The firmware catalog (built in), IPSW and boot ROM downloads, guest tools selection, Legacy Store client, IPA checks. |
| `lighttouch-services` | Guest services over the emulated USB cable: the usbmuxd-qemu bridge, usbmux, lockdown (TLS 1.0 via OpenSSL), AFC, installation proxy. |
| `lighttouch-model` | All app behaviour without a UI toolkit: devices, sessions, background tasks, settings. |
| `lighttouch` | The executable: the egui frontend (`src/ui/`), `worker` mode, developer commands (`list`, `import`, `run`). |

Processes: the app, plus one `lighttouch worker` per running device (the engine starts once per process). The worker
loads the engine library through the core, plays audio with cpal, writes frames into shared memory the app created,
and speaks JSON lines on stdin/stdout. The UI only renders `Model` state and calls its methods, so the toolkit can be
replaced without touching behaviour.

## Running from a checkout

```sh
cargo build
LIGHTTOUCH_ENGINE=/path/to/libqemu-arm.so LIGHTTOUCH_GUEST_TOOLS=/path/to/guest-tools target/debug/lighttouch
target/debug/lighttouch import n72ap-7E18.itbase --rom bootrom.bin --package armv6.itpack
target/debug/lighttouch run <record id> --seconds 60 --shot frame.ppm   # headless session
LIGHTTOUCH_FIRMWAREKIT=/path/to/firmwarekit target/debug/lighttouch prepare n72ap-7E18 [--ipsw FILE]
LIGHTTOUCH_USBMUXD=/path/to/usbmuxd-qemu target/debug/lighttouch run <record id> --store "doodle jump"   # or --install App.ipa, --services
```

Windows, cross-built from Linux with the posix-threads MinGW toolchain (`.cargo/config.toml` picks the linker):

```sh
export CC_x86_64_pc_windows_gnu=x86_64-w64-mingw32-gcc-posix CXX_x86_64_pc_windows_gnu=x86_64-w64-mingw32-g++-posix \
       AR_x86_64_pc_windows_gnu=x86_64-w64-mingw32-ar
cargo build --release --target x86_64-pc-windows-gnu --features lighttouch-services/vendored-openssl
```

The engine DLL is qemu-ios's MinGW build relinked by `contrib/portable/make-shared-windows.py`; it and its DLLs,
`lighttouch-usbmuxd.exe` and `guest-tools/` go in `resources\` beside `lighttouch.exe`.

`LIGHTTOUCH_DATA` overrides the data folder (default `~/.local/share/LightTouch`, `%LOCALAPPDATA%\LightTouch\App`,
`~/Library/Application Support/LightTouch`); `LIGHTTOUCH_RESOURCES` the bundled resources folder.
To check the UI where nothing can capture the screen (over ssh), `LIGHTTOUCH_UI_SHOT=file.png` saves the window after
`LIGHTTOUCH_UI_SHOT_AFTER` seconds and closes it; `LIGHTTOUCH_UI_START=1` starts the selected device first.

## Status

| Area | State |
| --- | --- |
| Device library, add from `.itbase`/folder, rename, delete | Done (Linux) |
| Start, stop, recover, pause, mute, screenshot, frames, touch, buttons, keys | Done (Linux, checked under Xvfb) |
| Theme, device frame, overlay (FPS/basic/full), full screen, settings | Done; frame inspector to do |
| Prepare from an IPSW (catalog, download, firmwarekit sidecar, adoption) | Done (Linux) with the Rust firmwarekit: n72ap-7E18 prepared in 15 s and n81ap-10B500 in 126 s (keybag and seal boots included), both boot to the lock screen |
| Legacy Store, specific versions, IPA install, last crash (Rust usbmux/lockdown/AFC/instproxy over usbmuxd-qemu) | Done (Linux), checked on iPod touch 2G 3.1.3 and 4.2.1 and iPhone 3GS 5.1.1; a store install takes ~20 s from attach. Following the guest when it reattaches under a new usbmux id is written but not yet exercised |
| macOS | Everything above works on an M-series Mac: prepare (firmwarekit and `lighttouch-oneshot` built there from this tree; n72 3.1.3 downloaded and prepared in 23 s), sessions in the same egui UI (Retina, system theme), Legacy Store installs with the 0.0.9 app's usbmuxd. Needs the engine's indirect-cache JIT fix (qemu-ios `079f70a607`; the guest hangs in iBoot without it); tested with that fix relinked into the 0.0.9 engine, not yet an engine built from source |
| Windows | Builds by cross-compiling from Linux (below). Under Wine: import, sessions from the CLI and the UI (boots, clean shutdown, frames, controls). Not checkable under Wine: guest GPU drawing (the engine refuses software OpenGL, so the screen is black there), audio (no device), and app services (the USB daemon checks who owns each loopback connection, which Wine can't answer). Needs a real Windows run for those. The Rust `firmwarekit.exe` cross-builds and is in the dist; prepare under Windows not yet run |
| Packaging (`cargo xtask dist`: AppImage, zip, .app) | To do |
| `firmwarekit` in Rust (`crates/lighttouch-firmware`; Windows prep, no Swift toolchain) | Done for every board recipe: K48 family (iPad 1, iPod touch 3G/4G, iPhone 3GS/4), iPod touch 2G and iPhone OS 1.x (iPod touch 1G, original iPhone). Checked against the Swift tool: identity, kboot, NOR, iBoot and GID blobs byte-identical; every fit check, the guest package and activation records equal; the volumes equal file for file except binary plists' object order. 1.x now prepares on Linux (Swift needed a Mac) and boots to SpringBoard. Open: the iPod touch 2G's 4.x keybag boot (n72keybag), the sibling keybag ramdisk (recipe keybag_ramdisk_from), Android bundling |
| Rust tcp_usb host replacing usbmuxd-qemu | Later |
