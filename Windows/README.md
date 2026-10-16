# Windows x86_64 compile preview

The maintained Windows package now uses the shared Python/Tk launcher and SDL
guest controls; run `LightTouch.cmd`. See [Desktop/README.md](../Desktop/README.md).
The native WinForms preview instructions below are retained for reference.

If an older shared package reports "Folder grants access outside this user,
SYSTEM and Administrators" for `SharedWindows/bases`, update the entire ZIP
into a fresh folder. The updated Python storage adapter and native helper
recognize the OWNER RIGHTS entry created by Python 3.13 after checking actual
ownership. Existing bases and saves are retained; no manual ACL changes needed.

Prepared-system import streams the ordered zlib payload through bounded memory
buffers with up to four file writers. Large assets stream in 1 MiB chunks;
NAND pages stay separate files. Verification reads and read-only permission
updates also use up to four workers. Publication waits for every worker and all
integrity checks. The logs include `[import-timing]` lines for hashing,
extraction, verification, permissions and publication. Actual Windows speedup
depends on filesystem/scanner behavior and remains to be measured on hardware.

Normal Starts reuse the successful import validation for a read-only system.
The helper checks the directory's Windows file identity, ownership/protection
and the small `device.lock.json`, without hashing NAND again. Receipts live in
`%LOCALAPPDATA%\LightTouch\SharedWindows\system-validation`. Older imports get
one full, four-reader verification on their first Start with this version.
Missing/stale receipts or replaced directories/changed system locks require a
full check. In Advanced, **Recheck all system files before starting (slow)**
forces that check. A saved result does not detect manual changes inside NAND
that leave the system lock unchanged; use the recheck option after modifying
an imported system or changing its read-only permissions.

This is a separate, experimental Windows frontend around the current iOS QEMU
engine. It is not a Windows acceptance pass. Use Windows10/11 x86_64 with a real
hardware OpenGL2.1 compatibility driver. No software GPU provider is bundled.
The guest CPU still uses QEMU TCG; Windows Hyper-V does not accelerate these
32-bit iOS guest CPUs.

## Start a prepared guest

Extract the complete Windows preview ZIP to a writable local folder, then run
`LightTouchWin.exe`. The .NET WindowsDesktop runtime is included in the
self-contained publish; no administrator installation is intended.

If replacing the first preview after a `nor-rw must be a private copy` error,
close the launcher and extract the updated ZIP into a new folder. Start the
launcher and select your existing saved device in the list; its selected inputs
and saves remain in the application data folder. No base reimport or save
deletion is needed. The engine now uses actual
Windows file identifiers: the old CRT inode check could mistake a separate NOR
copy for the base. Same-file and hardlink protection remains enabled.

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

For an asset-free native loader and NOR identity check, run
`powershell -NoProfile -ExecutionPolicy Bypass -File .\SmokeWindows.ps1` from the
extracted folder. It checks separate identical-content copies, same-file names,
hardlinks, Unicode paths and failed lookups using synthetic temporary files.
It also checks repeated NAND page/erase-marker replacements and preserves
committed data when publication fails. This does not boot or alter a guest.

## Logs and the first previews

The next preview also preserves the host mouse cursor while interacting with
the guest, and sends Home/Lock controls with the exact QMP `hold-time` field.
The earlier `Parameter 'hold_time' is unexpected` response was a host command
serialization bug; no guest data or firmware change is required.

Choose **Open data folder** in the launcher. Within `devices`, each saved device
has its own folder containing `emulator.log` (native errors and graphics
diagnostics), `serial.log` (iOS boot output) and its writable saves. The full log
paths are `%LOCALAPPDATA%\LightTouch\Windows-preview\devices\<id>\emulator.log`
and `serial.log` in that same folder. `library.json` maps device IDs to names.
These files remain available after the emulator exits.

The r2 engine could stop on `FMSS: cannot persist ...: File exists` when iOS
wrote a NAND page a second time. The updated engine atomically replaces that
private page using the native Windows replacement API. Keep your existing
overlay and unconfirmed-shutdown marker; do not delete the page named in the
error. Start a fresh emulator process with the updated runtime.

This transitional preview has controls in the launcher. Home/Power/volume and
other controls within the guest display window require the shared desktop
adapter still being developed; they are not provided by this hotfix.

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
