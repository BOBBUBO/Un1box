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
The `LightTouchDesktopHost` product now owns this adapter for the maintained
shared Tk launcher through `macOS/launcher.py` and `lighttouch_desktop.macos`.
This is implemented source with asset-free host tests, not yet a native macOS
compilation, packaged Tcl/Tk or guest execution pass.

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

The native host maps one saved profile to an official DeviceInstance under that
profile's private state root. It admits through `FirmwareTool.admitBoot`, creates
fresh guest-package offers and derives native boot arguments without imported
Mac paths/endpoints. n81 uses prepared kboot/NAND/writable NOR and no separate
ROM. No existing Cocoa/Linux state is converted or copied. Bases remain shared
and read-only. Archive import streams to new unpublished staging, validates
supported native assets and publishes without overwriting an existing base.

Recovery markers are descriptor-relative, refuse symlinks, are fsynced and carry
a session identity. Clean publication reacquires the stopped native lease and
requires actual final shutdown confirmation, no storage failure, helper reaping
and nonforced exit0. Neither native `.stopped` nor exit0 alone counts. Stop
resumes paused guests, asks guest shutdown, then requests QEMU quit only after
confirmation. Bounded force cleanup retains dirty state. An overdue live helper
retains its owner rather than being orphaned with a false terminal receipt;
external finite process-tree supervision is still required for acceptance runs.
Host screenshots copy the existing IOSurface into isolated state/screenshots.

`macOS/build-shared.py` is the explicit `macos-shared-arm64` dispatcher bundle
recipe. It requires native Swift6.2+, SDL2, engine/helper inputs and a separately
supplied self-contained Python/Tk runtime; it relocates native dependencies and
ad-hoc signs the development bundle. It copies no bases, guest tools or state.
The minimum macOS version is the highest deployment floor read from **every
actual packaged Mach-O dependency**. An installed SDL2 compatibility library
backed by SDL3 may require macOS26; building the host with an older deployment
target does not remove that dependency requirement.

`macOS/python-runtime.py plan` prepares a per-file hash manifest from explicitly
named Python framework, matching `_tkinter` extension and Tcl/Tk script roots.
It includes the interpreter, framework binary and stdlib; it excludes external
site-packages, test suites, ensurepip and bytecode. Escaping links and special
files are refused. `export` requires that reviewed manifest's SHA256, rechecks
all source hashes and creates an absent private output directory. This is a
curated runtime export, not permission to redistribute an installed provider:
matching license/source-notice auditing remains required. Example planning
arguments (replace paths with audited native providers):

```sh
python3 macOS/python-runtime.py plan \
  --python-prefix /explicit/Python.framework/Versions/3.13 \
  --python-version 3.13 \
  --tkinter /explicit/_tkinter.cpython-313-darwin.so \
  --tcl-scripts /explicit/lib/tcl9.1 \
  --tk-scripts /explicit/lib/tk9.1 \
  --output-manifest /private/runtime-plan.json
python3 macOS/python-runtime.py export \
  --manifest /private/runtime-plan.json --manifest-sha256 REVIEWED_SHA256 \
  --output /private/new-python-runtime
```

The exporter leaves native install names intact; the app recipe binds exported
files back to their original hashed providers and relocates the complete native
closure. Tcl/Tk scripts are selected inside the packaged runtime. Python runs
without user site-packages or bytecode writes into the signed app.

Shared native tools reside under `Contents/Helpers`; the maintained Tk launcher
remains the app entrypoint. Before admission or engine loading, both tools
provide the asset-free `--runtime-info` diagnostic. Packaging uses their actual
Foundation main-bundle contexts to expose SwiftPM resource bundles from
`Contents/Resources/SwiftPM`. A context outside `Contents` is rejected instead
of relying on build-scratch resources. Native resource paths, dependency floors
and runtime manifest binding are retained in the build receipt. Relocated
package testing must remove access to the original build scratch/providers
before claiming self-containment.
The `macos-arm64` Cocoa route remains a reference, never a shared UI pass.

The initial shared host at source27f4b168 compiled natively on arm64 macOS26
with Swift6.3.3 and SDL2 API2.32.74. This proves compilation of that revision;
the subsequent runtime diagnostic/export/resource packaging source still needs
its own execution qualification. SDL/CGL coexistence, packaged Tk/native dialogs, real guest
touch/render/audio/shutdown/restart/persistence remain unqualified. Guest
networking/app services/IPA and native process counters are unavailable in the
shared Mac host. Linux's existing app-service path is preserved. Gyro remains
deferred and unqualified. TCG caching remains explicitly disabled unless a
separately qualified route implements it.
