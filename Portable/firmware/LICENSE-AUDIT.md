# Selected native firmware licenses

This audit covers the firmware ELF built from source commit
`9d09845a56c19ec03905ef846a73b1f202deb7cf`, not the entire APK. The
selected compilation inputs and hashes are recorded in `selected-sources.json`.
That file records archive inputs; section garbage collection means it does not
assert that every object in an input archive survives in the final ELF.

LightTouch's root README explicitly grants GPL version 2 **or any later version**.
The existing CActivation implementation has no narrower per-file notice. The
firmware wrappers, activation and derived project algorithms can therefore use
the GPLv3 option. Pinned XPwn's README specifies GPL version 3; its LICENSE is
GPLv3, with no project-level later-version option identified. OpenSSL 3.6.3 is
Apache-2.0. These grants allow a GPLv3 firmware combination; a GPLv2-only label
would not describe this combination correctly. See the
[GNU version compatibility explanation](https://www.gnu.org/licenses/gpl-faq.en.html#v2v3Compatibility),
[Apache's compatibility statement](https://www.apache.org/licenses/GPL-compatibility.html)
and [OpenSSL's versioned licenses](https://openssl-library.org/source/license/index.html).
This conclusion does not resolve the bounded Apple provenance gap below.

| Selected input | Grant / retained notice |
| --- | --- |
| LightTouch CActivation, portable wrappers and derived recipe/format algorithms | Project GPL-2.0-or-later; elect GPLv3 for this combination |
| XPwn common/HFS/DMG workers at `20c32e5c12d1b22a9d55a59a0ff6267f539b77f4` | GPL-3.0; `vendor/XPwn-LICENSE`, upstream notices and corresponding source |
| Generated B-tree/extent repairs, guard and HFS growth adapter | Changed-source descriptions and CMake generation recipe; Windows audit origin `7aecedc273f66d3a4aeeb27fee750629c6fffc3c` |
| XPwn minizip 1.01e, Gilles Vollant 1998–2005 | zlib-style grant; `vendor/minizip-LICENSE.txt` |
| Retained minizip crypt/decrypt adaptations from Info-ZIP | `vendor/Info-ZIP-LICENSE.txt`; preserve the source's original 1990–2000 attribution as well |
| DMG checksum CRC adapted from zlib 1.1.3 | Mark Adler 1995–1998 attribution and zlib notice retained in the source |
| nlohmann/json 3.12.0 | MIT; `vendor/json-LICENSE.MIT` |
| tinyxml2 11.0.0 | zlib; `vendor/tinyxml2-LICENSE.txt` |
| Target OpenSSL 3.6.3 `libcrypto.a` | Apache-2.0; `vendor/OpenSSL3-LICENSE.txt` and source-file notices |
| Android platform `libz`, `libdl`, `libm`, `libc`; packaged `libc++_shared` | Platform/toolchain dependencies; retain applicable NDK runtime notices in the APK's dependency inventory |

`ltf_common` compiles `abstractfile.c` and `base64.c`; `ltf_hfs` compiles generated
`btree.c` and `rawfile.c`, plus catalog, extents, xattr, fastunicodecompare,
flatfile, hfslib, utility, volume and hfscompress. `ltf_dmg` compiles checksum,
dmgfile, dmglib, filevault, io, partition, resources and udif. `ltf_zip` compiles
unzip and ioapi, including their headers and crypt.h. CMake links those archives,
the portable sources, tinyxml2, CActivation and target libcrypto into
`liblighttouch_firmware.so`. The original pinned checkout remains unchanged;
generated fixes and compiler guard redirects are part of corresponding source.

The actual ARM64 link includes libcrypto and the NDK platform libz, with
`--gc-sections`; it does not link libssl, QEMU, XPwn command-line applications,
USB/DFU/pwn tools or IMG2 CLI workers. Runtime dependencies are libz.so, libdl.so,
libm.so, libc++_shared.so and libc.so. Private stock firmware and release guest
files are read as preparation data, not linked host ELF inputs. Their availability
and rights are separate from this host-library audit.

The pinned minizip unzip.c asks for an Info-ZIP license dated 2000-Apr-09 or
later, but XPwn's accompanying LICENSE is GPLv3 rather than that text. The added
2009-Jan-02 notice is transcribed from the license block on
[Info-ZIP's own site](https://infozip.sourceforge.net/license.html), preserving
the copyright, author definition, disclaimer and all four conditions. The plain
text link on that site failed certificate validation during this audit; the
HTTPS SourceForge page was used without bypassing TLS checks. This is the altered
XPwn/minizip adaptation, not an official Info-ZIP product. Archive encryption is
rejected by the preparer, but password-decryption code and
`unzOpenCurrentFilePassword` remain in the ELF, so their notice is retained.

XPwn fastunicodecompare.c states that it adapts Apple's TN1150. Its
FastUnicodeCompare function and gLowerCaseTable remain exported in the actual
ARM64 ELF, even though this preparation route uses case-sensitive HFSX. The
[official TN1150 sample](https://developer.apple.com/library/archive/technotes/tn/tn1150.html)
was downloaded from Apple's linked BinHex archive and decoded with header/data
CRC checks. The extracted FastUnicodeCompare.c has no copyright, license or
permission grant. All 2,816 table entries exactly match XPwn's retained table.
The downloaded sample and decoded source hashes are recorded in the manifest;
the sample is audit evidence outside Git and is not a new compilation input.
The upstream GPLv3 umbrella and this sample do not establish the original grant
for the Apple material. This is a specific unresolved provenance/permission gap,
not evidence of an identified incompatible license. Obtain a documented grant
or replace/exclude that material before claiming firmware distribution closure.

For a release, include these notices and the complete GPLv3 corresponding source,
including generated-source recipes and adapters. This document adds source-side
notices; it does not prove that the APK already carries them. The separate
emulator DSO and its selected dependencies require their own audit. The product's
firmware JNI DSO links this library and runs in a dedicated firmware process;
the emulator JNI DSO links the engine separately. APK co-packaging alone is not
evidence that GPLv2-only emulator code has been linked into this firmware ELF.
