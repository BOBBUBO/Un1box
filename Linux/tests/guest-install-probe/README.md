# Owned iOS installer/launch probe

This is an original, tiny test app for the Linux install path. It contains no
downloaded application, Apple SDK headers, firmware or private fixture contents.
The app uses public Objective-C runtime/UIKit calls via explicit ARM32 C
declarations and `dlopen`/`dlsym`. All original source follows the repository's
GPL-2.0-or-later license.

Expected behavior after a future **actual** installation and launch: an **LT
Probe** Home-screen icon opens a purple screen saying **LAUNCH CONFIRMED**.
Tapping anywhere changes the screen to blue and **INPUT CONFIRMED**; a second tap
changes it back. These are intended oracles, not reported target results.

The target is ARM1176/ARMv6, ARM state, iOS 3.1.3, 320×480. `start.S` implements
the legacy process stack entry for `LC_UNIXTHREAD`; it does not use `LC_MAIN`.
Unlike the broader upstream 1.x/2.x startup recipe, this source specifically
targets 3.1.3, whose libSystem initializes itself. `libSystem.tbd` is our own
four-symbol link description, not an acquired SDK or a claim that target imports
have been verified. It names the guest's normal libSystem install path.

## Reproducible build/tool diagnosis

Use a fresh **private directory outside Git**:

```sh
python3 Linux/tests/guest-install-probe/build.py --out /absolute/private/new-output
```

The script makes no network calls. Tiny compiler/linker subprocesses have 20s
outer, 10/15s CPU, 1 GiB virtual-memory and 32 MiB output-file bounds. It retains
exact commands, compiler/linker output, source/output hashes and parsed Mach-O
facts in `RESULT.json`. It refuses an output path inside this source repository.

Optional explicit existing tools:

```sh
python3 Linux/tests/guest-install-probe/build.py \
  --out /absolute/private/new-output \
  --clang /path/to/clang --linker /path/to/arm32-capable-Mach-O-ld \
  --signer /path/to/ldid
```

The linker must genuinely support ARMv6 and old iOS dyld metadata. The script
does not relabel another CPU's executable or invent a successful link. A linked
file must be ARMv6 `MH_EXECUTE`, contain `LC_UNIXTHREAD`, and have neither
`LC_MAIN` nor `LC_DYLD_INFO_ONLY`. The signed file must additionally contain
`LC_CODE_SIGNATURE`. Only then does the script create the private IPA with a
0755 executable, original generated icon and Info.plist. A successful file build
would still be `BUILT_SIGNED_UNQUALIFIED_IPA`, never guest qualification.

## Actual local result, 2026-10-15

Clang 18.1.3 compiled both sources without SDK headers using
`--target=armv6-apple-ios3.1.3 -marm -ffixed-r9 -ffreestanding -nostdinc` and
warnings as errors. Real `llvm-objdump-18` identifies both as 32-bit ARM V6
**OBJECT** files; the app's only undefined imports are `__exit`, `_dlopen`,
`_dlsym`, `_write`. These objects are not runnable apps.

Both installed host LLVM 18 and Android NDK 28.2 `ld64.lld` fail the actual link
with **missing or unsupported -arch armv6**. Both also reject `-arch armv7`, so
upstream's ARMv7-link/subtype restoration route is unavailable with these tools.
`ldid` is absent from PATH. No executable, signed app or IPA was produced.

The first attempt also passed the older `-iphoneos_version_min` spelling, which
LLD rejected for missing `-platform_version`; the corrected attempt uses
`-platform_version ios 3.1.3 3.1.3` and isolates the ARM-architecture rejection.
That failure remains in private evidence, alongside the corrected result.

The immediate missing tools are an ARM32-capable Mach-O linker and signer.
Building/installing another toolchain or using remote Mac tools is a separate
resource assignment. No historical SDK was acquired, no arbitrary app was
downloaded, and no target/phone/AVD/SSH operation was performed. Runtime UIKit
behavior, code-sign acceptance, actual install, visible icon, launch, touch and
clean shutdown remain unqualified.
