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
| `lighttouch-catalog` | The firmware catalog (built in), IPSW and boot ROM downloads, guest tools selection. |
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
```

`LIGHTTOUCH_DATA` overrides the data folder (default `~/.local/share/LightTouch`, `%LOCALAPPDATA%\LightTouch\App`,
`~/Library/Application Support/LightTouch`); `LIGHTTOUCH_RESOURCES` the bundled resources folder.

## Status

| Area | State |
| --- | --- |
| Device library, add from `.itbase`/folder, rename, delete | Done (Linux) |
| Start, stop, recover, pause, mute, screenshot, frames, touch, buttons, keys | Done (Linux, checked under Xvfb) |
| Theme, device frame, overlay (FPS/basic/full), full screen, settings | Done; frame inspector to do |
| Prepare from an IPSW (catalog, download, firmwarekit sidecar, adoption) | Done (Linux): n72ap-7E18 downloaded and prepared in 62 s, boots; firmwarekit needs a static Swift stdlib to ship |
| Legacy Store, specific versions, IPA install (Rust usbmux client, lockdown, AFC, instproxy) | Next |
| macOS: Portable portability layer, engine dylib exports | To do |
| Windows: Portable portability layer, engine DLL | To do |
| Packaging (`cargo xtask dist`: AppImage, zip, .app) | To do |
| `firmwarekit` in Rust (Windows prep, no Swift toolchain) | Later, checked against the Swift tool's output hashes |
| Rust tcp_usb host replacing usbmuxd-qemu | Later |
