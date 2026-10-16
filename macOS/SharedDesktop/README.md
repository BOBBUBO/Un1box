# Native adapter for the shared desktop guest window

This source adds a real presentation/input adapter, not a second macOS toolbar.
`SharedGuestWindow` calls the C ABI in `Desktop/native`, which draws the exact
shared `Desktop/ui/ControlBar.hpp` and opaque presentation source. Its only frame
source is an existing `DeviceLink` IOSurface ring; touch, hardware buttons,
pause/resume, rotation and visibility use that link's existing commands. The
host screenshot callback receives the existing IOSurface synchronously; it
must finish reading/copying before returning. Audio stays with the native
engine/session owner. Guest CPU execution stays TCG and guest GL stays the
native engine's CGL path; the SDL renderer presents finished guest pixels.

The optional Swift package product is `LightTouchSharedDesktopMac`. It needs
Swift6.2/macOS14.4 and SDL2 development headers/library discoverable by `sdl2`
pkg-config (Homebrew `sdl2`). It is not linked into the default Cocoa executable.
No native macOS compilation, package, guest test or shared macOS launcher is
claimed by adding this target. A source plan is not an execution receipt.

An existing native session owner integrates it in this order:

1. Retain normal native library identity, storage containment/admission,
   `FirmwareTool.admitBoot`, matching guest-package composition and refreshed
   host endpoint logic. Create a fresh `DeviceSessionProcess`/helper for each
   boot, with its real private lease. Do not feed raw imported Mac QEMU arguments
   through this adapter or reuse a previous helper.
2. Construct `SharedGuestWindow(link: ..., stopRequested: ..., ...)` on the main
   thread. Its Stop callback belongs to the existing graceful-shutdown and
   bounded process-reaping owner. Do not map it to force quit or treat
   `DeviceProcessDeath.stopped`/exit0 alone as shutdown proof.
3. After the native boot callback succeeds, call `didBoot()`. From the main run
   loop call `tick()` at a bounded display cadence. This object must be the only
   reader of `DeviceLink.frontSurface()`; do not attach a Cocoa display reader
   concurrently. IOSurface rows copy directly into one opaque SDL texture, with
   no JSON/base64/frame files. Hardware SDL presentation is required; there is
   no software-renderer fallback.
4. Call `willStop()` before external Stop or disposal so all owned contacts and
   buttons release. Finish the existing shutdown-confirmation and reaping
   transaction before `closeAfterReap()`. This object never clears a dirty mark,
   removes a lease, reaps a helper or labels guest storage clean.

The C ABI supports one main-thread guest window. Mouse, touch and keyboard have
separate ownership namespaces; ten stable guest touch slots are available.
Input starts disabled. Focus loss, resize, drag outside the guest, pause,
rotation and Stop release owned inputs; closing cannot re-enable the session.
The hardware Home/Power and visible toolbar labels/shortcuts are taken directly
from shared source, not duplicated in Swift.

`Desktop/tests/NativeWindow.cpp` exercises actual adapter routing using synthetic
SDL window geometry, including concurrent ownership, two crossing contacts,
independent lift, ten-contact admission, focus cancellation, toolbar drag-off,
pause ordering, duplicate mouse filtering, Stop and hardware-renderer refusal.
That asset-free test is host evidence only, even when compiled/passing. macOS
Mach rendezvous, signing, CGL/SDL coexistence, physical input, audio, clean guest
shutdown/persistence and packaging require separate actual target evidence.

Still required for a shared macOS application: the Python host/session/profile
adapter for `Desktop/lighttouch_desktop/launcher.py`, native managed record
mapping without changing the Cocoa/Linux libraries, native import/admission
commands, strict shutdown receipt, app-service integration and packaged Tcl/Tk.
The existing Cocoa frontend remains the reference until those pieces pass.
