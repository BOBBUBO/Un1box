# One desktop product, native platform adapters

Desktop UI changes belong in `Desktop/`. The Android touch frontend stays separate.
The intended default Linux, Windows and macOS applications use the same Tk device
library/setup UI and SDL guest-window UI. Platform code owns native dialogs where
needed, process/IPC, graphics contexts, audio devices, filesystem/locks and
packaging. It must not implement an independently evolving Home/Power toolbar.
No new browser runtime or third UI toolkit is introduced.

## What is shared now

`Desktop/ui/ControlBar.hpp` is the actual UI implementation consumed by Linux:
button layout, bitmap labels, hit testing, guest viewport geometry, keyboard
mapping and balanced mouse/finger/keyboard button ownership. It includes shared
`Commands.hpp`; the SDL event loop routes those commands through its existing
Linux transport. `Desktop/ui/Presentation.hpp` provides the existing opaque guest
texture behavior. Linux include/test wrappers retain existing paths; they contain
no duplicate implementation. `Desktop/tests/ControlBar.cpp` exercises that code.

Change Home/Power controls, toolbar geometry or shortcuts here once. A desktop
adapter adopting this guest window compiles this same source; it must not copy
it. Rebuild each target afterward. No generated per-platform toolbar is needed.
`Desktop/contracts/README.md` describes the adapter boundary, not a
replacement for existing private Linux profiles or native asset admission.

## What still differs

Linux now imports the actual shared Tk `Launcher` and `ScrollPanel` from
`Desktop/lighttouch_desktop/launcher.py`. Its entrypoint provides one `LinuxHost`
adapter for existing profile persistence/native admission, process/session,
archive inspection and guest app services. Existing profiles and devices.json
remain in place. Staging copies these exact shared modules, with a relocated
import test. The shared widgets import no Linux process or native library code.
The Linux adapter still uses `fcntl`, XDG storage, POSIX signals, `/proc`,
`os.O_DIRECTORY`, native import CLI and Linux app-services. Its SDL `Main.cpp`
also embeds Unix sockets, memfd, fork/exec and waitpid. Those host implementations
are not portable merely because the UI has moved. Do not replace this working
runtime or advertise a shared Windows/macOS shell before adapters are tested.

The isolated Windows preview is a separately compiled WinForms/QMP/standalone-QEMU
spike. It is not the future product UI. Retain its audited WGL, PE closure and
safe shutdown lessons; replace its UI with the shared Tk/SDL surfaces after a
Windows process/frame/input adapter exists. Its native standalone QEMU SDL
window does not yet consume `Desktop/ui`.

The existing Cocoa/Swift macOS app remains a native reference and development
backend route. Its library/menu/control code is not shared with Linux today.
Preserve its CGL/engine/helper integration; add a host adapter for the shared
Tk/SDL shell, then retire duplicate product controls only after target tests.
The incremental `macOS/SharedDesktop/SharedGuestWindow.swift` adapter now routes
existing DeviceLink IOSurfaces/input through `Desktop/native/Window.cpp` and
these exact SDL controls. It is an optional source library, not yet the native
application's window. Compilation and macOS execution remain unqualified;
the new `macOS/SharedHost` runner now implements managed native records,
admission, fresh package offers, CoreAudio and strict shutdown receipts for the
shared Tk `MacHost`. It uses a separate default library and leaves installed
Cocoa/Linux libraries untouched. Shared profile/archive code now lives in
`Desktop/lighttouch_desktop`; Linux retains compatibility imports. See the
adapter README for the concrete lifecycle boundary and missing services.
The upstream Xcode frontend may remain a reference, not a second source of
product UI requirements. Adopting Tk on macOS needs packaged Tcl/Tk validation,
main-thread event handling and native dialog/menu checks; this is unqualified.
`macos-shared-arm64` selects the separate shared bundle recipe with an explicit
self-contained Python/Tk runtime. Source and synthetic host tests are not a
Mac compile, package, dialog, SDL/CGL or real guest acceptance pass.

## Incremental convergence

1. Land the shared SDL controls/presentation and build dispatcher without changing
   guest behavior. This foundation also implements the Tk extraction below.
2. The Tk widget extraction and first Linux adapter are implemented in this
   foundation. Next narrow profile-storage, file/admission, process/session and
   app-service adapter contracts further where actual second-host integration
   requires it; preserve existing private formats and GUI tests.
3. Extract the Linux SDL guest event/render loop from process/IPC. Reuse the same
   `Desktop/ui` layout and command dispatcher, with a small frame/input/lifecycle
   interface. Keep hardware GL/EGL/WGL/CGL in native adapters. Shared ownership and
   release/cancel semantics must also apply during focus loss/resize/close.
4. Implement Windows and macOS adapters and package the shared shell on those
   hosts. Require actual boot/render/multi-touch/audio/clean-stop/persistence
   before declaring parity. Keep the Windows spike/native Mac reference isolated
   until replacement is proven. Gyro remains deferred/unqualified.

## Build ownership

`python3 Desktop/build.py --matrix` reports current targets and source adoption.
The same entrypoint plans/dispatches native build and packaging recipes; it does
not claim a Linux machine can compile Apple SDK artifacts. It records exact
argv, source revision, target, UI adoption and child status in private receipts.
It refuses unsupported routes before launching commands. Existing finite Linux
build/packaging guards remain authoritative. Mac execution requires a macOS
host, native SDK/Swift and explicit vendor dependencies. Remote scheduling is
external (for example SSH to the authorized Mac and run this same command).

Build outputs, dependencies and receipts stay outside source. Guest assets and
private state are never build inputs to this dispatcher. Native compilation,
packaging and runtime acceptance are separate claims; plans are not passes.
