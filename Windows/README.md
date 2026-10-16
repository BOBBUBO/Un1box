# Windows x86_64 compile preview

This is a separate, experimental Windows frontend around the current iOS QEMU
engine. It is not a Windows acceptance pass. Use Windows10/11 x86_64 with a real
hardware OpenGL2.1 compatibility driver. No software GPU provider is bundled.
The guest CPU still uses QEMU TCG; Windows Hyper-V does not accelerate these
32-bit iOS guest CPUs.

## Start a prepared guest

Extract the complete Windows preview ZIP to a writable local folder, then run
`LightTouchWin.exe`. The .NET WindowsDesktop runtime is included in the
self-contained publish; no administrator installation is intended.

1. Choose **Open prepared folder** for an existing base, or **Import .itbase**
   to unpack one into the application data folder. Prefer an existing prepared
   folder for the large n81 base; there is no need for a redundant18GB copy.
2. Select the matching guest tools `.itpack` (ARMv6 for n72, ARMv7 for n81).
   The launcher verifies the package family/payload hashes and regenerates the
   host-side offer inside this device's writable state for every boot.
3. n72 additionally needs its matching SecureROM. n81 boots `kboot.bin` from
   the prepared base and never asks for a separate hardware boot ROM.
4. Choose **Start**. The device screen opens in a separate SDL window; the
   launcher has Home, Lock/Wake, Pause/Resume and Shut down controls.
5. Choose **Shut down** and wait for confirmation before closing. A timeout
   preserves the running process and exclusive save lock. It does not kill the
   emulator or mark the save clean.

Prepared base contents remain read-only. Private NOR, NAND overlay, regenerated
package offer and logs live under
`%LOCALAPPDATA%\LightTouch\Windows-preview\devices\<id>`. The `running.lock`
prevents two launchers from using that same writable state. Do not reuse an
Android/Linux state directory: this preview creates its own state/identity pin.

A native window close, Task Manager termination, crash or machine power loss
can leave `shutdown-unconfirmed.json`. The next Start warns before reusing that
preserved state; a dark screen alone never means clean shutdown. Keep a backup
of important guest data. Strict guest shutdown/persistence still needs an actual
Windows/iOS run, even though separate-process host contract tests exercise the
confirmation and timeout paths.

## Scope and limitations

Only the existing n72ap and n81ap prepared routes are exposed. Stock IPSW
preparation, Legacy Store/IPA services, modern networking, rich settings,
performance metrics and persistent TCG cache are not implemented by this
Windows preview. No Apple firmware/ROM, prepared NAND or proprietary guest
package is included. Supply your own legitimately obtained compatible inputs.
Gyro is deferred and unqualified. Windows multitouch, hardware graphics output,
audible audio, lifecycle, clean restart and guest persistence remain unqualified
until measured on an actual Windows host. Wine is not installed on the build
machine; PE compilation/import inspection is not target execution evidence.

The WGL guest executor uses the existing compatibility OpenGL path, including
synchronous completion for Apple fence operations. This preserves conservative
ordering rather than claiming an unmeasured performance improvement.

## Build on Linux for Windows

Requires a Linux x86_64 host with Python3.12+, zstd/tar, MinGW-w64 GCC/G++13+
(the POSIX variant), make, ninja, pkg-config and a .NET8 SDK. Source builds must
run within the caller's admitted memory/CPU/time limits; the scripts intentionally
do not allocate an unrestricted background builder.

```sh
python3 Windows/tools/stage-mingw.py \
  --manifest Windows/mingw-dependencies.json --destination /outside/git/win-deps
Windows/tools/build-engine.sh /path/to/current-qemu-windows-source \
  /outside/git/win-deps /outside/git/fresh-engine-build \
  /path/to/verified/ffmpeg-9.0.1.tar.xz
Windows/tools/build-ui.sh /path/to/dotnet /outside/git/fresh-win-publish
python3 Windows/tools/package-runtime.py \
  --engine /outside/git/fresh-engine-build/engine/qemu-system-arm.exe \
  --dll-dir /outside/git/win-deps/sysroot/mingw64/bin \
  --dll-dir /outside/git/win-deps/sysroot/codec/bin \
  --destination /outside/git/fresh-win-publish/runtime
```

`build-ui.sh` runs the focused Linux host contracts before cross-publishing a
self-contained Windows UI. They use tiny generated data and a separate fake QMP
process, never a private firmware fixture. `package-runtime.py` audits recursive
PE imports and rejects missing/ambiguous DLLs and MSYS/Cygwin dependencies. It
does not execute Windows code. Retain build logs, exact source revisions,
package checksums and actual host test output with each artifact.

## Reference provenance

Selected C# recipe/library/archive code and Windows WGL/file/socket ideas come
from `BerryPVZ/LightTouchWin`, reviewed revision
`7aecedc273f66d3a4aeeb27fee750629c6fffc3c` on `port/windows-native`.
The reference's broad feature claims, automatic publication, native firmware
workers and forceful disposal/kill behavior were not adopted. Existing GPL
licensing and upstream notices remain applicable. The engine adapters were
reviewed against our newer06640a3 source rather than applying its entire old
engine patch. MSYS2 dependencies are isolated MSVCRT `mingw64` packages; this
cross recipe does not mix in the reference's UCRT64 installation.
