#!/usr/bin/env python3
"""Check all APK native entries: no compression and ELF PT_LOAD 16KiB alignment."""
import argparse
import hashlib
import json
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
    angle_names = {"libEGL_lighttouch_angle.so", "libGLESv1_CM_lighttouch_angle.so", "libGLESv2_lighttouch_angle.so"}
    present_angle = {name.rsplit("/", 1)[-1] for name in archive.namelist() if name.startswith("lib/") and name.rsplit("/", 1)[-1] in angle_names}
    if present_angle:
        assert present_angle == angle_names, "Partial packaged ANGLE provider"
        provider = json.loads(archive.read("assets/providers/angle-runtime.json"))
        assert provider["schema"] == 1 and provider["abi"] == "x86_64"
        assert set(provider["libraries"]) == angle_names
        notice = json.loads(archive.read("assets/licenses/angle/manifest.json"))
        assert notice["schema"] == 1
        for name, record in provider["libraries"].items():
            assert not any(f"lib/{abi}/{name}" in archive.namelist() for abi in ("arm64-v8a", "armeabi-v7a")), "Experimental provider packaged on ARM"
            data = archive.read(f"lib/x86_64/{name}")
            assert hashlib.sha256(data).hexdigest() == record["sha256"] == notice["libraries"][name], f"Packaged provider identity changed: {name}"
        for record in notice["files"]:
            name = record["path"]
            assert all(part not in ("", ".", "..") and "\\" not in part for part in name.split("/")), "Invalid provider notice path"
            assert hashlib.sha256(archive.read("assets/licenses/angle/" + name)).hexdigest() == record["sha256"]
        print("Verified all three x86 ANGLE packaged identities and declared notice hashes; actual app-UID provider remains unqualified")
assert count, "APK has no native libraries"
print(f"Checked {count} shared libraries; runtime/device acceptance still required")
