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
| Prepare from an IPSW (catalog, download, firmwarekit sidecar, adoption) | Done (Linux): n72ap-7E18 downloaded and prepared in 62 s, boots; firmwarekit needs a static Swift stdlib to ship |
| Legacy Store, specific versions, IPA install, last crash (Rust usbmux/lockdown/AFC/instproxy over usbmuxd-qemu) | Done (Linux), checked on iPod touch 2G 3.1.3 and 4.2.1 and iPhone 3GS 5.1.1; a store install takes ~20 s from attach. Following the guest when it reattaches under a new usbmux id is written but not yet exercised |
| macOS | Everything above works on an M-series Mac: prepare (firmwarekit and `lighttouch-oneshot` built there from this tree; n72 3.1.3 downloaded and prepared in 23 s), sessions in the same egui UI (Retina, system theme), Legacy Store installs with the 0.0.9 app's usbmuxd. Needs the engine's indirect-cache JIT fix (qemu-ios `079f70a607`; the guest hangs in iBoot without it); tested with that fix relinked into the 0.0.9 engine, not yet an engine built from source |
| Windows: Portable portability layer, engine DLL | To do |
| Packaging (`cargo xtask dist`: AppImage, zip, .app) | To do |
| `firmwarekit` in Rust (Windows prep, no Swift toolchain) | Later, checked against the Swift tool's output hashes |
| Rust tcp_usb host replacing usbmuxd-qemu | Later |
