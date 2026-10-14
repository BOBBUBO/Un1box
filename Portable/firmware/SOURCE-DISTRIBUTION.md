# Firmware corresponding source and notices

The APK's firmware notices are produced by `tools/package-notices.py`. Supply the
existing pinned NDK's NOTICE.toolchain and a publisher-controlled HTTPS reference
to the exact corresponding source revision for the binary being conveyed. This
tool does not download source, discover private inputs, or establish that a URL
is accessible. The publisher must verify that reference before distribution.

The bundle contains COPYING.firmware-GPL-3.0.txt, nlohmann-json.MIT.txt,
tinyxml2.zlib.txt, XPwn.minizip.zlib.txt, XPwn.Info-ZIP.txt, XPwn.CRC32.zlib.txt,
OpenSSL3.Apache-2.0.txt, NDK.toolchain.NOTICE.txt, NOTICE.firmware.txt and
manifest.json. Full NDK toolchain notices are copied unmodified rather than
guessing which historical libc++ license blocks apply. The CRC notice is the
original zlib 1.1.3 header notice from the
[maintainer's source tag](https://github.com/madler/zlib/blob/v1.1.3/zlib.h),
matching the adaptation credited in XPwn checksum.c. The bundle is firmware
component material; it does not cover the emulator or complete APK.

Provide the complete portable source revision with its original project README
and LICENSE, CActivation, Portable/firmware, Android firmware JNI/service sources,
public dependency/build manifests and reproducible build commands. Include the
generated-source CMake recipes and original inputs used by those recipes, the
overflow-extent adapter, guard, HFSX growth code and all required XPwn headers.
GPL correspondence requires the actual modified inputs and build instructions,
not merely a link to unmodified XPwn or a generic upstream home page.

XPwn's pinned source is `20c32e5c12d1b22a9d55a59a0ff6267f539b77f4`.
Retain its GPLv3 license, README and relevant embedded third-party notices. The
binary HFSX library excludes fastunicodecompare.c and its folding caller; do not
bundle the unresolved Apple sample as purportedly cleared source. A selected
source export needs the CMake-read originals btree.c, rawfile.c, catalog.c and
volume.c, all compiled common/HFS/DMG/minizip inputs, required headers and license
texts. `selected-sources.json` documents the compilation inputs and historical
before/after exclusion artifacts; compiler dependency files establish the header
closure. The public `xpwn-selected.SHA256SUMS` pins 35 original files, including
all eight required headers plus upstream GPL/license documentation. CMake
authenticates their content in both the Git-checkout and plain-export routes.
The exporter records the real upstream pin in XPWN-COMMIT; it creates no Git
repository or fabricated commit identity. Its 41 files exclude unused folding
source, Git objects, applications and unrelated inputs. Generate the source
export from an existing pinned public-source checkout:

```sh
python3 Portable/firmware/tools/export-xpwn-source.py \
  /sources/xpwn-20c32e5 /build/public-source/xpwn-selected
cmake -S Portable/firmware -B /build/firmware \
  -DXPWN_SOURCE=/build/public-source/xpwn-selected
cmake --build /build/firmware -j1
ctest --test-dir /build/firmware --output-on-failure
```

The export and original routes were configured independently; all four generated
worker sources are byte-identical. A corrupted selected header is rejected.
Configuration/generation parity is verified; compilation from the exported
directory has not been separately run. The original-checkout host/ARM64 library
qualification is recorded in the manifest.

For a deterministic public source archive, include the project revision and this
export, dependency source/license manifests and instructions. Git archive omits
untracked local inputs; export only the public project revision being conveyed:

```sh
git archive --format=tar --prefix=LightTouch/ ACTUAL_SOURCE_REVISION > LightTouch.tar
tar -C /build/public-source --sort=name --mtime=@0 --owner=0 --group=0 \
  --numeric-owner -cf xpwn-selected.tar xpwn-selected
gzip -n xpwn-selected.tar
```

Include or provide matching dependency source as required by its terms; the
notice generator's publisher URL must identify this complete correspondence,
not just the project archive with its external XPwn inputs omitted.

Use the target dependency manifest and toolchain recipe for OpenSSL 3.6.3,
including its unmodified Apache license and retained source notices. Only libcrypto
is linked into firmware. Android uses platform libz; the standalone adapted CRC
source and its zlib terms still belong in the firmware source/notices. Vendored
JSON and tinyxml2 are already public source with their licenses and hashes.
The packaged libc++ runtime's applicable notices are included in the notice
bundle; its exact NDK version and runtime source provenance must match the APK.

Publish code, legal source and build recipes only. Stock IPSWs, ROMs, guest release
binaries, SDKs, private inventories, preparation logs, prepared NAND/NOR and target
captures are separate local acceptance inputs and are absent from this source
recipe. Android preparation and guest boot acceptance must name the actual APK
source/artifact; a notice bundle or cross-build proves neither runtime gate.
