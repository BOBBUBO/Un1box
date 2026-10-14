#!/usr/bin/env python3
"""Check all APK native entries: no compression and ELF PT_LOAD 16KiB alignment."""
import argparse
import struct
import zipfile

parser = argparse.ArgumentParser()
parser.add_argument("apk")
args = parser.parse_args()
count = 0
with zipfile.ZipFile(args.apk) as archive:
    with open(args.apk, "rb") as apk:
        for entry in archive.infolist():
            if not entry.filename.startswith("lib/") or not entry.filename.endswith(".so"):
                continue
            count += 1
            assert entry.compress_type == zipfile.ZIP_STORED, f"Compressed {entry.filename}"
            apk.seek(entry.header_offset)
            local = apk.read(30)
            name_length, extra_length = struct.unpack_from("<HH", local, 26)
            offset = entry.header_offset + 30 + name_length + extra_length
            assert offset % 16384 == 0, f"Unaligned APK entry {entry.filename}: {offset}"
            data = archive.read(entry)
            assert data[:6] == b"\x7fELF\x02\x01", f"Expected little-endian ELF64: {entry.filename}"
            phoff = struct.unpack_from("<Q", data, 32)[0]
            phsize, phnum = struct.unpack_from("<HH", data, 54)
            for i in range(phnum):
                position = phoff + i * phsize
                kind = struct.unpack_from("<I", data, position)[0]
                if kind == 1:
                    file_offset, virtual = struct.unpack_from("<QQ", data, position + 8)
                    alignment = struct.unpack_from("<Q", data, position + 48)[0]
                    assert alignment >= 16384, f"4KiB ELF LOAD in {entry.filename}: {alignment}"
                    assert (virtual - file_offset) % 16384 == 0, f"Unaligned ELF LOAD: {entry.filename}"
            print(entry.filename, "uncompressed, APK/ELF aligned 16KiB")
assert count, "APK has no native libraries"
print(f"Checked {count} shared libraries; runtime/device acceptance still required")
