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
link description for four C imports plus `dyld_stub_binder`, not an acquired SDK
or a claim that target imports have been verified. It names the guest's normal
libSystem install path.

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
file must be ARMv6 `MH_EXECUTE`, contain `LC_UNIXTHREAD`, and have no `LC_MAIN`.
Compressed `LC_DYLD_INFO_ONLY` is supported on the 3.1.3 target, as documented in
the upstream armv6-toolchain README; the stricter `--legacy` rewrite is for
1.x/2.x. The signed file must additionally contain `LC_CODE_SIGNATURE`, with
independently verified page hashes and a legacy SHA-1 CodeDirectory. Only then
does the script create the private IPA with a
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

Those local missing tools were resolved for **artifact creation only** through
the subsequent explicitly authorized Mac experiment below. The Linux recipe's
installed linker and signer blocker remains truthful for the local tool set.

## Actual Mac artifact result, 2026-10-15

The separately authorized Mac source-only experiment found Apple clang 21,
ld-1267 (including an ARM32-capable `ld-classic`), and existing Homebrew ldid
2.1.5_2. `mac_link.py` records serial real subprocesses with 20s outer, 10/15s
CPU, 32 MiB file and 1 GiB sampled process-tree RSS limits. It uses an empty
sysroot and no header search path for the guest code. Native compilation of the
pinned upstream `machotool.c` uses the existing Mac toolchain. Stage that public
source from the matching qemu-ios checkout alongside our five source files in a
new private `/tmp/lighttouch-install-probe.*` directory. The runner checks that
`machotool.c` matches the reviewed SHA-256
`372843eb2b5a23ff82790d7cf33ddb40ba8ee69caf81b002024798425abfced4`.
Then run:

```sh
python3 /tmp/lighttouch-install-probe.NEW/mac_link.py
```

The recipe first attempts genuine ARMv6 linking, and has the upstream
ARMv7-subtype round-trip available only if that fails. The installed ld-classic
actually linked ARMv6 directly, so the successful attempt needed no subtype
round-trip. `machotool mkold --subtype 6` emitted a legacy thread entry;
`ldid -S` then signed the executable. Both SHA-1 and SHA-256 CodeDirectory page
hashes were independently recomputed and matched all 13 pages. The original icon
and Info.plist were packaged with the 0755 executable in an IPA.

Actual product: 50,912-byte ARMv6 `MH_EXECUTE`, `LC_UNIXTHREAD`,
`LC_CODE_SIGNATURE`, no `LC_MAIN`; 4,396-byte IPA. These are inspection facts,
not a guest pass. Artifact hashes and exact tools/commands are in private
`MAC-RESULT.json` and `TOOLCHAIN.txt` evidence, not embedded in public source.

Preserved failed attempts caught Mac `/tmp` resolving to `/private/tmp`, xcrun's
default MacOS SDK metadata conflicting with an iPhone target (fixed by an empty
sysroot), the manual stub initially omitting `dyld_stub_binder`, and applying the
unneeded 1.x/2.x `--legacy` conversion to an iOS3.1.3 lazy-bind executable. A
mutable command-list reference in one failed-attempt receipt mislabels its first
ARMv6 link as ARMv7; its raw log identifies ARMv6, and future receipts copy argv.
That failed receipt has not been rewritten or used as success evidence.

The copied successful artifact also passed the canonical Linux IPA preflight
for an ARMv6 iPod2,1/iOS 3.1.3 target, ZIP CRC and executable-mode checks. Later
source review tightened signature bounds and the hash layout: the same real
artifact passes; private copies with a modified signed code byte or a zero hash
size fail. The Mac runner now retires its child if memory monitoring fails;
that failure path was exercised with a tiny local process. Those prospective
runner refinements have not been used for another Mac build. Original staging
sources, commands, failures and hashes remain intact.

No historical SDK was acquired, no package installed, no arbitrary app payload
downloaded, and no target/phone/AVD/fixture operation performed. Runtime UIKit
behavior, guest signature acceptance, actual install, visible icon, launch,
touch and clean shutdown remain unqualified. The built app is explicitly
`BUILT_SIGNED_UNQUALIFIED_IPA` until actual target evidence establishes them.

The next runtime handoff is [ACCEPTANCE.md](ACCEPTANCE.md): it covers the actual
desktop chooser, private service bridge, measured icon/launch/tap proof and strict
clean stop. `install_acceptance.py` prints a plan by default; only the exclusive
session owner may execute its bounded service diagnostic. It cannot qualify
pixels or the desktop chooser. Its synthetic guards can be checked without any
guest, daemon or native device library:

```sh
python3 -m unittest discover -s Linux/tests/guest-install-probe \
  -p test_install_acceptance.py -v
```
