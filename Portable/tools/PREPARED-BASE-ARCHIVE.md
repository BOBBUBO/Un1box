# Streaming a prepared base for import

`write_prepared_base.py` is a Linux host transport tool, not firmware preparation
or a target runner. It currently supports the n81ap kboot profile only: the four
root files `device.lock.json`, `identity.json`, `kboot.bin`, `nor.bin`, and the
NAND: `geometry.json` with either the bus0/bus1 CE0..3 stores or the NAND container
`nand.qcow2` FirmwareKit now writes. Other boards/layouts fail closed. The archive
puts `geometry.json` right after the lock, so the importer packs chip files into the
record's container as they stream. No particular build, private fixture path or identity is embedded.

Before running it on a large source, obtain allocation for that read/compression
work and independently verify the trusted lock SHA256. Don't change the source while
it streams; the tool never changes its permissions. It rejects symlinks in every source/output-parent component, hard
links, special files, extra/missing entries and unsupported lock paths. It checks
the pinned lock, individual file hashes and authoritative NAND listing hash,
retains open descriptors and verifies inode/stat identity before publication.
`built_listing_sha256` is not the final NAND listing pin.

```sh
python3 Portable/tools/write_prepared_base.py \
  --source /absolute/read-only/prepared-n81-base \
  --lock-sha256 INDEPENDENTLY_VERIFIED_64_LOWERCASE_HEX \
  --output /absolute/private/new-transport-bundle
```

The NEW output directory contains `base.itbase` and `source-receipt.json`. The
receipt lists relative source names, sizes, modes and SHA256 values, the trusted
lock/listing pins and exact archive hash/size. No guest bytes, absolute source
paths or guest acceptance claim are put in the receipt. Data is read, hashed and
compressed in 64KiB chunks (plus bounded zlib state), without an expanded staging
copy or an all-payload RAM buffer. The small lock metadata is read once to build
the plan and again as an archive entry. Existing outputs are never replaced;
fsynced archive/receipt are published together using Linux
`renameat2(RENAME_NOREPLACE)`. An unsupported no-replace operation fails closed.
An error after publication, such as parent-directory fsync failure, may leave the
complete bundle present while the command reports failure; preserve and inspect
that result rather than replacing it.

The wire format is exactly the production `lt_import_archive` contract:
`ITPACK01`, a little-endian uint32 JSON-index length, a JSON `entries` array of
`name/size/mode`, then one zlib stream of file bytes in index order. The supplied
handoff tar and the matching guest-tools `.itpack` are different inputs; neither
is this prepared-base archive.

For Android, use the existing Import base archive flow after selecting the
matching armv7 guest-tools package. That flow stages the compressed transport,
then imports into a new independently validated record. The current
extractor writes the full logical base once on the target; this tool does not
make extraction sparse. Check real target capacity for the expanded base,
compressed staging, writable state and margin. Do not use directory import to
introduce an additional expanded staging copy. Existing record assets remain
independent; inspect the actual new record's board/build/package hashes before
booting. The n72-only `PrivateFixtureImportTest` is not an n81 import test.

## Tiny production-importer regressions

These tests use synthetic data under2MiB, not a private base. The adapter links
the actual `Storage.cpp`; tests do not substitute their own archive decoder.
Compile only in an allocated compiler slot:

```sh
c++ -std=c++17 -O2 -g -Wall -Wextra -Werror -fsanitize=address,undefined \
  -fno-omit-frame-pointer -ffunction-sections -fdata-sections -I Portable/include \
  Portable/tools/tests/import_prepared_archive.cpp Portable/src/Storage.cpp \
  -Wl,--gc-sections -lz -pthread -o /absolute/private/test-output/import-prepared-archive
python3 Portable/tools/tests/test_write_prepared_base.py \
  --importer /absolute/private/test-output/import-prepared-archive -v
```

Section garbage collection excludes unused device-boot entry points from this
Storage-only test executable; those entry points depend on the separate guest
package implementation. No storage/import symbol is stubbed or replaced.

Coverage includes accepted multi-chunk output and source/receipt integrity;
production rejection of truncated streams, wrong archive hashes, traversal and
payload/lock mismatches; refused symlink/hard-link/extra-entry sources;
unsupported trusted lock paths; no output inside the source or over existing
output; a no-replace publication race; and an injected midstream source change.
Failed imports must leave no published or partial base. None of these checks is
a large-source conversion, guest boot, GLES2, storage-capacity or target pass.
