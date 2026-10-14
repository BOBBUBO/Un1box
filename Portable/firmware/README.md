# Native baseline preparation

`lighttouch_firmware` implements a blocking versioned C API for local stock
`n72ap-7E18` IPSW preparation. Linux's `lighttouch-prepare` calls the same library
that Android can package through JNI. It never executes extracted binaries,
requires root, mounts images, or invokes macOS tools. `lt_firmware_supports(id)` advertises the implemented route. Other catalog routes
return an explicit unsupported result.

Build from the pinned XPwn checkout, outside the source tree:

```sh
cmake -S Portable/firmware -B /private/build/firmware \
  -DXPWN_SOURCE=/private/sources/xpwn
cmake --build /private/build/firmware -j1
ctest --test-dir /private/build/firmware --output-on-failure
/private/build/firmware/lighttouch-prepare \
  LightTouchMac/Resources/firmware-catalog.json n72ap-7E18 \
  /private/stock.ipsw /private/verified/guest-tools \
  /private/verified/inventory.json /private/new-prepared-device
```

CMake also accepts the hash-verified selected XPwn source export described in
[source distribution](SOURCE-DISTRIBUTION.md). Both routes verify all 35 pinned
original source/header/license inputs before generating modified workers.

Stock input must match the catalog's SHA1 **and** exact byte count. The guest
inventory is a trusted local handoff inventory with `entries` containing `path`,
`type` and `sha256`; paths use `release-guest-unpacked/guest-tools/` or
`guest-tools/`. Verify the inventory's provenance before supplying it. The original exporter
manifest is unavailable; a local handoff/extraction inventory establishes file
integrity, and original itpack manifests establish package payload hashes. These
checks do not reconstruct or prove the missing exporter manifest, rebuild the
release assets, or replace the missing historical SDKs. Seven
exported files are required: `OpenGLES`, `libappsync.dylib`, `armv6.itpack`,
`it_agent`, `it_typein.dylib`, `sblaunch`, and `sbdlicon`. The
itpack loader, manifests, payload hashes and Mach-O architecture are validated.
It installs the package's real agent/preferences/hooks rather than fabricating
helper binaries, and writes the matched `guest.itpack` with an original-schema
`outputs.guest_package` path/hash record. Domain import/boot can discover that
package without frontend architecture choices. Source firmware, ROMs, exported guests and prepared outputs stay
outside Git; the library reads all sources without modifying them.

The baseline recipe decrypts IMG3 iBoot, generates SysCfg/NVRAM and UID-wrapped
NOR signatures, emits GID blobs, decrypts/extracts FileVault/UDIF rootfs, grows
HFSX in userspace, installs raw kernelcache/system edits/activation and rehashes
ad-hoc Mach-O code directories while retaining entitlements, installs the verified
seed package, and writes the four-chip FTL/NAND layout and versioned device lock.
Sound defaults, mobile owners, guest-tool markers and the shared proxy recipe are
part of the system edits. Full stock filesystems require allocation bitmap
relocation into the appended free space: stock XPwn `grow_hfs` incorrectly tries
to allocate the larger map from the full old volume.

Preparation stages in a private 0700 sibling, hashes output files, flushes them
and directories, and promotes with Linux/Android `renameat2(RENAME_NOREPLACE)`.
Existing destinations cannot be replaced. A cancelled/failed preparation removes
only its stage. A power interruption leaves an identifiable `.prepare-*` stage
that is never a prepared output. Callbacks run on the caller thread and can cancel
between bounded hash/NAND blocks and HFS operations; historical XPwn extraction
and single filesystem operations do not offer mid-operation cancellation.

Run preparation in a dedicated same-UID firmware service, distinct from the
emulator session service. XPwn uses raw allocations and assertions. Its reachable
`exit`/`hfs_panic`/`ASSERT` paths are redirected at compilation to a guarded C
failure boundary. A fatal error returns `LT_FW_RESTART_REQUIRED` and poisons subsequent XPwn calls;
terminate that firmware service to reclaim untracked allocations. Parser faults
such as invalid memory access additionally require service process-death handling.
Process exit alone is never preparation success: require API status OK **and** a
promoted output with lock/checksum metadata. The synchronous library serializes
preparation within a process; callbacks must not re-enter it.

Android cross compilation uses the NDK toolchain, Android-built OpenSSL and zlib,
and the same source checkout. Linux host `.so` files are never Android inputs.
Set `CMAKE_TOOLCHAIN_FILE`, `ANDROID_ABI`, `ANDROID_PLATFORM`, and
`OPENSSL_ROOT_DIR` for Android. The library and dependencies must share the APK's
16 KiB ELF alignment policy. Android frontend/JNI/service integration and actual
unrooted preparation are acceptance requirements beyond host compilation.

## Sources and distribution

- XPwn `planetbeing/xpwn` commit
  `20c32e5c12d1b22a9d55a59a0ff6267f539b77f4`, GPL-3.0. Source checkout is supplied
  explicitly. The audited Windows adapters originate at LightTouchWin commit
  `7aecedc273f66d3a4aeeb27fee750629c6fffc3c`.
- The generated B-tree and overflow-extent fixes retain the Windows recipe and
  `xpwn-write-extents.inc`; generated builds never edit the source checkout.
  Compilation redirects XPwn process exits to the guard described above.
- Baseline format/recipe algorithms derive from the preserved Swift FirmwareKit
  and audited Windows native preparation. Original macOS source is untouched.
  Activation remains the existing `CActivation` implementation.
- nlohmann/json v3.12.0 single header, MIT. Vendored source and `json-LICENSE.MIT`.
- tinyxml2 v11.0.0, zlib license. Vendored source/header and
  `tinyxml2-LICENSE.txt`. These versions are explicit portable dependencies and do
  not alter the macOS dependency pins.
- OpenSSL and zlib are supplied by the target toolchain. Their source versions and
  licenses must be included in the release dependency manifest. XPwn distribution
  requires its complete corresponding source, generated fixes and GPL-3.0 license.
  LightTouch's GPL-2.0-or-later grant permits the GPLv3 option for this firmware
  combination. Keep GPL-3.0 firmware components separate from GPL-2.0-only emulator
  linkage. The [selected-source audit](LICENSE-AUDIT.md) records exact inputs,
  retained notices and the exclusion of Apple TN1150 folding material; it does not
  claim complete APK distribution clearance.

Only binary-comparison HFSX version 5 is accepted by the filesystem worker.
The n72 stock and prepared catalog both use comparison type `0xBC`; the existing
binary comparator is preserved unchanged. HFS+, case-folded HFSX and unknown
comparison modes fail rather than receiving a different ordering. Their unused
Apple-derived folding implementation/table is excluded from compilation.

Generate deterministic Android offline notice assets with public legal sources:

```sh
python3 Portable/firmware/tools/package-notices.py /build/assets/licenses/firmware \
  --ndk-notice /path/to/android-ndk-28.2.13676358/NOTICE.toolchain \
  --source-reference https://publisher.example/source/ACTUAL_REVISION \
  --source-revision FULL_40_HEX_REVISION
```

Use the revision and accessible corresponding-source URL for the conveyed binary.
The generator verifies vendored notice hashes and the exact pinned NDK notice,
copies complete legal texts without timestamps/private paths, and emits schema-1
`manifest.json` with sorted `files` path/hash records and `source_reference` /
`source_revision`. Bundle filenames are stable. The Android assets consumer can
display the texts offline and link to the publisher's source reference. Repeating
the command verifies a byte-identical existing bundle; differing contents require
a fresh generated-assets destination. Generation does not verify the publisher's
URL availability or complete source offer. See [source distribution](SOURCE-DISTRIBUTION.md).

Synthetic format/transaction tests are source-only evidence. The format test
compares all 50 NAND metadata pages against the independent upstream legacy
golden hashes, including the corrected GPT geometry. The synthetic HFSX test
exercises case sensitivity, owners/modes, extended attributes, symlinks, catalog
growth/overflow extents and offsets above 4 GiB, and proves XPwn exit(0) returns
a failure through the C guard. Hard-link/resource-fork copy routes remain explicit
unsupported worker operations; preparing in place retains stock filesystem data. A real stock
prepare followed by a guest boot on Linux and Android is the decisive gate. A
prepared Mac fixture or host-only library test does not establish that gate.

Optional private acceptance checks (outside public CI):

```sh
python3 Portable/firmware/tests/private-acceptance.py /private/new-prepared-device
python3 Portable/firmware/tests/private-cancel.py /private/build/firmware/liblighttouch_firmware.so \
  LightTouchMac/Resources/firmware-catalog.json /private/stock.ipsw \
  /private/verified/guest-tools /private/verified/inventory.json /private/cancelled-output
```

The cancellation check stops at NAND writing after real system edits and verifies
source hashes, stage removal and absence of promotion. XPwn diagnostic output may
precede the final JSON result. Keep the raw diagnostics as local evidence and
capture the last JSON line separately. Both checks explicitly leave guest boot
and Android preparation untested; they cannot establish those acceptance gates.
