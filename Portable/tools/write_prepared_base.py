#!/usr/bin/env python3
"""Stream a trusted n81 kboot base into an atomic import bundle.

No firmware preparation, source mutation, expanded copy, or target operation.
The supported profile deliberately has exactly thirteen known regular files.
"""

import argparse
import contextlib
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import struct
import sys
import zlib

CHUNK = 65536
MAX_BYTES = 64 << 30  # Same expanded-byte ceiling as lt_import_archive.
MAX_LOCK = 1 << 20
# A base's NAND: one file per chip, or (since FirmwareKit packs it) the NAND container, nand.qcow2.
NAND_LAYOUTS = (tuple(f"bus{bus}-ce{ce}.pages" for bus in range(2) for ce in range(4)) + ("geometry.json",),
                ("geometry.json", "nand.qcow2"))


def archive_files(nand_names):
    """Archive order: geometry.json before the chips, so the importer packs each chip into the record's NAND
    container as it streams (a chip ahead of it is staged on disk first)."""
    return tuple(sorted(("device.lock.json", "identity.json", "kboot.bin", "nor.bin") +
                        tuple("nand/" + name for name in nand_names),
                        key=lambda name: (name != "device.lock.json", name != "nand/geometry.json", name)))
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
FILE_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value), "invalid SHA256")
    return value


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def absolute_path(value):
    path = Path(value)
    require(path.is_absolute() and ".." not in path.parts, "absolute paths without traversal required")
    return path


def open_directory(path):
    """Retain a directory without following symlinks in any component."""
    fd = os.open("/", DIRECTORY_FLAGS)
    try:
        for part in path.parts[1:]:
            next_fd = os.open(part, DIRECTORY_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def identity(fd):
    st = os.fstat(fd)
    return (st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def regular(fd, directory=False):
    st = os.fstat(fd)
    require(stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode),
            "source must contain only directories and regular files")
    require(not st.st_mode & 0o7000, "special source permission bits refused")
    require(directory or st.st_nlink == 1, "source file hard links refused")
    require(st.st_size >= 0, "invalid source size")
    return st


def lock_plan(blob, pin, nand_names):
    require(hashlib.sha256(blob).hexdigest() == digest(pin), "trusted lock SHA256 mismatch")
    lock = json.loads(blob, object_pairs_hook=unique_object)
    require(isinstance(lock, dict), "lock must be an object")
    require(type(lock.get("format")) is int and lock["format"] == 1, "unsupported lock format")
    require(lock.get("board") == "n81ap" and lock.get("boot_strategy") == "kboot", "only n81ap kboot supported")
    require(isinstance(lock.get("build"), str) and re.fullmatch(r"[A-Za-z0-9._-]{1,64}", lock["build"]),
            "invalid build identity")
    outputs = lock["outputs"]
    require(isinstance(outputs, dict), "outputs must be an object")
    require(set(outputs) == {"kboot", "nor", "nand"}, "unsupported output inventory")
    expected = {"device.lock.json": pin, "identity.json": digest(lock["identity"]["sha256"])}
    for key, name in (("kboot", "kboot.bin"), ("nor", "nor.bin")):
        require(outputs[key]["path"] == name, "unexpected output path")
        expected[name] = digest(outputs[key]["sha256"])
    nand = outputs["nand"]
    require(nand["path"] == "nand" and set(nand["files"]) == set(nand_names), "unexpected NAND paths")
    for name in nand_names:
        expected["nand/" + name] = digest(nand["files"][name])
    require("pages" not in nand or (type(nand["pages"]) is int and nand["pages"] == len(nand_names)),
            "unexpected NAND listing count")
    return lock, expected, digest(nand["listing_sha256"])


def write_all(fd, data):
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        require(written > 0, "short output write")
        view = view[written:]


def publish(parent_fd, staged, destination):
    """Linux RENAME_NOREPLACE: publish archive and receipt together, never clobber."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    require(rename is not None, "atomic no-replace directory publication unavailable")
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(parent_fd, os.fsencode(staged), parent_fd, os.fsencode(destination), 1):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def write_bundle(source, pin, output):
    source, output = absolute_path(source), absolute_path(output)
    require(output.name not in ("", ".", ".."), "output must name a new bundle directory")
    require(not output.is_relative_to(source) and not source.is_relative_to(output), "source/output overlap refused")
    with contextlib.ExitStack() as owned:
        def keep(fd):
            owned.callback(os.close, fd)
            return fd
        root = keep(open_directory(source))
        nand = keep(os.open("nand", DIRECTORY_FLAGS, dir_fd=root))
        regular(root, True)
        regular(nand, True)
        roots = {"": (root, identity(root)), "nand": (nand, identity(nand))}

        layouts = [names for names in NAND_LAYOUTS if set(os.listdir(nand)) == set(names)]
        require(layouts, "unexpected NAND entry")
        nand_names = layouts[0]

        def check_inventory():
            require(set(os.listdir(root)) == {"nand", "device.lock.json", "identity.json", "kboot.bin", "nor.bin"},
                    "unexpected source entry")
            require(set(os.listdir(nand)) == set(nand_names), "unexpected NAND entry")
        check_inventory()
        order = archive_files(nand_names)
        files = {}
        total = 0
        for name in order:
            parent, leaf = (nand, name[5:]) if name.startswith("nand/") else (root, name)
            fd = keep(os.open(leaf, FILE_FLAGS, dir_fd=parent))
            st = regular(fd)
            require(st.st_size <= MAX_BYTES - total, "expanded source exceeds import limit")
            total += st.st_size
            files[name] = (fd, identity(fd), parent, leaf)
        lock_fd = files["device.lock.json"][0]
        require(files["device.lock.json"][1][3] <= MAX_LOCK, "lock exceeds metadata bound")
        blob = bytearray()
        while chunk := os.read(lock_fd, CHUNK):
            blob.extend(chunk)
            require(len(blob) <= MAX_LOCK, "lock grew beyond metadata bound")
        lock, expected, listing_pin = lock_plan(blob, pin, nand_names)
        entries = [{"name": name, "size": files[name][1][3], "mode": stat.S_IMODE(files[name][1][2])}
                   for name in order]
        index = json.dumps({"entries": entries}, separators=(",", ":")).encode()
        parent_fd = keep(open_directory(output.parent))
        try:
            os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError("output already exists")
        staged = ".lt-base-" + secrets.token_hex(16)
        os.mkdir(staged, 0o700, dir_fd=parent_fd)
        stage_fd = keep(os.open(staged, DIRECTORY_FLAGS, dir_fd=parent_fd))
        published = False
        try:
            archive_fd = keep(os.open("base.itbase", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                                      0o600, dir_fd=stage_fd))
            archive_hash = hashlib.sha256()
            archive_size = 0

            def emit(data):
                nonlocal archive_size
                write_all(archive_fd, data)
                archive_hash.update(data)
                archive_size += len(data)

            emit(b"ITPACK01" + struct.pack("<I", len(index)) + index)
            compressor = zlib.compressobj(6)
            receipts = []
            hashes = {}
            for entry in entries:
                name = entry["name"]
                fd, before, _, _ = files[name]
                os.lseek(fd, 0, os.SEEK_SET)
                file_hash = hashlib.sha256()
                consumed = 0
                while chunk := os.read(fd, CHUNK):
                    consumed += len(chunk)
                    require(consumed <= entry["size"], "source grew while streaming: " + name)
                    file_hash.update(chunk)
                    emit(compressor.compress(chunk))
                require(consumed == entry["size"] and identity(fd) == before, "source changed while streaming: " + name)
                actual = file_hash.hexdigest()
                require(actual == expected[name], "source hash mismatch: " + name)
                hashes[name] = actual
                receipts.append(dict(entry, sha256=actual))
            listing = "".join(name + " " + hashes["nand/" + name] + "\n" for name in sorted(nand_names))
            require(hashlib.sha256(listing.encode()).hexdigest() == listing_pin, "authoritative NAND listing mismatch")
            emit(compressor.flush())
            # Check held descriptors AND their original names before publication.
            check_inventory()
            for name, (fd, before, directory, leaf) in files.items():
                require(identity(fd) == before, "source changed before publication: " + name)
                current = os.stat(leaf, dir_fd=directory, follow_symlinks=False)
                require((current.st_dev, current.st_ino) == before[:2], "source entry replaced: " + name)
            for name, (fd, before) in roots.items():
                require(identity(fd) == before, "source directory changed")
                check_fd = open_directory(source / name)
                try:
                    require(identity(check_fd) == before, "source directory replaced")
                finally:
                    os.close(check_fd)
            receipt = {"schema": 1, "profile": "n81-kboot", "board": lock["board"], "build": lock["build"],
                       "trusted_lock_sha256": pin, "nand_listing_sha256": listing_pin,
                       "archive": {"path": "base.itbase", "sha256": archive_hash.hexdigest(), "size": archive_size},
                       "expanded_bytes": total, "chunk_bytes": CHUNK, "source_readonly_verified": True,
                       "files": receipts, "guest_acceptance": False}
            receipt_fd = keep(os.open("source-receipt.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                                      0o600, dir_fd=stage_fd))
            write_all(receipt_fd, (json.dumps(receipt, indent=2) + "\n").encode())
            os.fsync(archive_fd)
            os.fsync(receipt_fd)
            os.fsync(stage_fd)
            publish(parent_fd, staged, output.name)
            published = True
            os.fsync(parent_fd)
            return receipt
        finally:
            if not published:
                for name in ("base.itbase", "source-receipt.json"):
                    try:
                        os.unlink(name, dir_fd=stage_fd)
                    except FileNotFoundError:
                        pass
                os.rmdir(staged, dir_fd=parent_fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="absolute n81 kboot base directory")
    parser.add_argument("--lock-sha256", required=True, help="independently trusted device.lock.json SHA256")
    parser.add_argument("--output", required=True, help="absolute NEW bundle directory, outside the source")
    args = parser.parse_args()
    try:
        receipt = write_bundle(args.source, args.lock_sha256, args.output)
    except (OSError, ValueError, KeyError, TypeError, zlib.error) as failure:
        print("Prepared-base archive refused: " + str(failure), file=sys.stderr)
        return 1
    print(json.dumps({"archive_sha256": receipt["archive"]["sha256"], "expanded_bytes": receipt["expanded_bytes"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
