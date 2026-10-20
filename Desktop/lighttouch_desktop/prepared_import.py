"""Common streaming .itbase extraction into an unpublished desktop base.

This layer interprets archive bytes only. The host adapter owns the private
parent/directory pins, failure cleanup, independent disk admission, freezing and
publication. A successful return never publishes a base or grants Start access.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import threading
import zlib

from .storage import DEFAULT_STORAGE

CHUNK = 1024 * 1024
MAX_INDEX = 64 * CHUNK
MAX_BYTES = 64 * 1024 * CHUNK
MAX_ENTRIES = 1_000_000
PAGES_PER_CS = 524288
SLOTS = 4 * PAGES_PER_CS
RECORD_SIZE = 4160
PACKED_HEADER = struct.pack("<8s4I", b"ITNAND01", 4096, 64, 4, PAGES_PER_CS)
_PAGE = re.compile(r"nand/cs([0-3])/(0|[1-9][0-9]*)\.page\Z")
_HEX = re.compile(r"[0-9a-fA-F]{64}\Z")
_CHUNK_NUMBER = re.compile(r"[0-9]{6}\Z")
WORKERS = 4
_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
_RESERVED.update(prefix + digit for prefix in ("COM", "LPT") for digit in "0123456789¹²³")


class ImportCancelled(Exception):
    """The caller cancelled an unpublished import; its adapter must clean up."""


def _name(name):
    if not isinstance(name, str) or not name or len(name.encode("utf-8")) >= 4096:
        raise ValueError("Invalid archive path.")
    for part in name.split("/"):
        if (part in ("", ".", "..") or part.endswith((".", " "))
                or any(ord(c) < 32 or ord(c) == 127 or c in '\\:<>"|?*' for c in part)
                or part.split(".", 1)[0].rstrip(" ").upper() in _RESERVED):
            raise ValueError("Unsafe archive path: " + name)
    return name


def _json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON field: " + key)
            result[key] = value
        return result
    def reject_constant(_):
        raise ValueError("Non-finite JSON number.")
    try:
        return json.loads(data, object_pairs_hook=unique, parse_constant=reject_constant)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Invalid prepared JSON.") from error


def _digest(value):
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise ValueError("Missing authoritative SHA-256 integrity record.")
    return value.lower()


class _Inflate:
    """Bulk bounded decompression, with small reads served from a shared chunk."""
    def __init__(self, read, checkpoint):
        self.read = read
        self.checkpoint = checkpoint
        self.z = zlib.decompressobj()
        self.pending = b""
        self.output = b""
        self.offset = 0

    def chunk(self):
        self.checkpoint()
        if self.offset < len(self.output):
            result = self.output[self.offset:]
            self.output = b""
            self.offset = 0
            return result
        while not self.z.eof:
            data = self.pending or self.read(256 * 1024)
            if not data:
                raise ValueError("Truncated prepared-device zlib stream.")
            try:
                result = self.z.decompress(data, CHUNK)
            except zlib.error as error:
                raise ValueError("Prepared-device zlib checksum or stream is invalid.") from error
            self.pending = self.z.unconsumed_tail
            if self.z.eof and self.z.unused_data:
                raise ValueError("Data follows the prepared-device zlib trailer.")
            if result:
                return result
            self.checkpoint()
        return b""

    def take(self, size):
        # Only small bounded metadata/page reads use this method.
        result = bytearray()
        while len(result) < size:
            if self.offset == len(self.output):
                self.output = self.chunk()
                self.offset = 0
                if not self.output:
                    raise ValueError("Truncated prepared-device payload.")
            count = min(size - len(result), len(self.output) - self.offset)
            result.extend(self.output[self.offset:self.offset + count])
            self.offset += count
        return bytes(result)


def import_prepared(source, destination, expected_sha256, *, storage=DEFAULT_STORAGE,
                    progress=None, cancelled=None):
    """Stream an ITPACK01 archive and return its resulting device-lock dict.

    ``destination`` must be absent in an adapter-owned private parent. It is
    deliberately left unpublished on success AND failure. ``progress`` receives
    (phase, done, total); ``cancelled`` returns true to raise ImportCancelled.
    StorageOps supplies secure regular descriptors, directories and file flushes.
    Memory is bounded by the 64 MiB index, its <=1M metadata records, an 8 MiB
    packed slot table and fixed I/O chunks; file payloads never accumulate.
    Source SHA-256, signature, exact payload/EOF and authoritative source-lock
    asset/listing hashes all gate success. The adapter must independently admit
    resulting on-disk bytes and permissions before publishing.
    """
    expected_sha256 = _digest(expected_sha256)
    destination = Path(destination)
    if not destination.is_absolute() or os.path.lexists(destination):
        raise ValueError("Choose an absent absolute import destination.")

    def checkpoint():
        if cancelled is not None and cancelled():
            raise ImportCancelled("Prepared-device import cancelled.")

    def report(phase, done, total):
        checkpoint()
        if progress is not None:
            progress(phase, done, total)

    def create(name, mode="wb"):
        path = destination / name
        storage.ensure_directory(path.parent)
        flags = os.O_CREAT | os.O_EXCL | (os.O_RDWR if mode == "w+b" else os.O_WRONLY)
        return os.fdopen(storage.open_regular(path, flags), mode, buffering=CHUNK)

    with ExitStack() as stack:
        checkpoint()
        raw = stack.enter_context(os.fdopen(storage.open_regular(source), "rb", buffering=CHUNK))
        signature = storage.file_signature(raw.fileno())
        source_size = os.fstat(raw.fileno()).st_size
        source_hash = hashlib.sha256()
        source_done = 0

        def read(size):
            nonlocal source_done
            checkpoint()
            data = raw.read(size)
            source_hash.update(data)
            source_done += len(data)
            return data

        def exact(size):
            data = read(size)
            if len(data) != size:
                raise ValueError("Truncated prepared-device archive.")
            return data

        header = exact(12)
        magic, length = struct.unpack("<8sI", header)
        if magic != b"ITPACK01" or not 0 < length <= MAX_INDEX or source_size < 12 + length + 6:
            raise ValueError("Invalid prepared-device header or index length.")
        index = _json(exact(length))
        entries = index.get("entries") if isinstance(index, dict) else None
        if not isinstance(entries, list) or not 0 < len(entries) <= MAX_ENTRIES:
            raise ValueError("Invalid prepared-device entry count.")
        names = set()
        plan = []
        total = 0
        for entry in entries:
            checkpoint()
            if not isinstance(entry, dict):
                raise ValueError("Invalid prepared-device index entry.")
            name = _name(entry.get("name"))
            size = entry.get("size")
            mode = entry.get("mode", 0o600)
            if (type(size) is not int or size < 0 or size > MAX_BYTES
                    or type(mode) is not int or not 0 <= mode <= 0o777
                    or name.casefold() in names):
                raise ValueError("Invalid or duplicate prepared-device entry.")
            total += size
            if total > MAX_BYTES:
                raise ValueError("Prepared archive exceeds the 64 GiB import limit.")
            names.add(name.casefold())
            plan.append((name, size))
        if "device.lock.json" not in dict(plan) or "source-device.lock.json" in names:
            raise ValueError("Missing device lock or reserved source lock collision.")
        for name, _ in plan:
            parts = name.split("/")
            if any("/".join(parts[:n]).casefold() in names for n in range(1, len(parts))):
                raise ValueError("A file conflicts with an archive directory.")
        if dict(plan)["device.lock.json"] > CHUNK:
            raise ValueError("Device lock exceeds 1 MiB.")

        nand_entries = [(name, size) for name, size in plan if name.startswith("nand/")]
        # Source locks can occur last. The index supplies geometry before payload
        # extraction; board/lock agreement is checked before returning success.
        pack = any(name.startswith("nand/cs") or name.endswith(".page") for name, _ in nand_entries)
        slots = bytearray(SLOTS * 4) if pack else None
        if pack:
            for name, size in nand_entries:
                match = _PAGE.fullmatch(name)
                if match is None or size != RECORD_SIZE or int(match[2]) >= PAGES_PER_CS:
                    raise ValueError("Invalid n72 NAND page geometry.")
        storage.ensure_directory(destination)
        packed = None
        records = 0
        if pack:
            packed = stack.enter_context(create("nand", "w+b"))
            packed.write(PACKED_HEADER)
            packed.write(slots)
        inflater = _Inflate(read, checkpoint)
        hashes = {}
        source_lock = None
        done = 0
        last_percent = -1
        report("extracting", 0, total)

        def account(count):
            nonlocal done, last_percent
            done += count
            percent = done * 100 // max(total, 1)
            if percent != last_percent:
                report("extracting", done, total)
                last_percent = percent

        for name, size in plan:
            checkpoint()
            digest = hashlib.sha256()
            if name == "device.lock.json":
                source_lock = inflater.take(size)
                digest.update(source_lock)
                with create("source-device.lock.json") as output:
                    output.write(source_lock)
                    output.flush()
                    storage.flush_file(output.fileno())
                account(size)
            elif pack and name.startswith("nand/"):
                data = inflater.take(size)
                digest.update(data)
                records += 1
                match = _PAGE.fullmatch(name)
                slot = int(match[1]) * PAGES_PER_CS + int(match[2])
                struct.pack_into("<I", slots, slot * 4, records)
                packed.write(data)
                account(size)
            else:
                with create(name) as output:
                    remaining = size
                    while remaining:
                        checkpoint()
                        count = min(remaining, CHUNK)
                        data = inflater.take(count)
                        output.write(data)
                        digest.update(data)
                        remaining -= len(data)
                        account(len(data))
                    output.flush()
                    storage.flush_file(output.fileno())
            hashes[name] = digest.hexdigest()
        if inflater.chunk():
            raise ValueError("Unindexed prepared-device payload bytes.")
        # zlib verifies its Adler32 trailer. Reject concatenated streams, padding,
        # trailing compressed bytes and source mutation on the same open handle.
        if read(1) or source_done != source_size or storage.file_signature(raw.fileno()) != signature:
            raise ValueError("Prepared-device source changed or has trailing bytes.")
        if source_hash.hexdigest() != expected_sha256:
            raise ValueError("Prepared-device archive SHA-256 differs.")
        lock = _json(source_lock)
        _verify_lock(lock, hashes, pack, checkpoint)
        if lock["board"] == "n72ap":
            if not pack:
                if nand_entries or "nand" in hashes:
                    raise ValueError("n72 NAND must contain canonical 4160-byte csN/N.page files.")
                slots = bytearray(SLOTS * 4)
                packed = stack.enter_context(create("nand", "w+b"))
                packed.write(PACKED_HEADER)
            packed.seek(24)
            packed.write(slots)
            packed.flush()
            storage.flush_file(packed.fileno())
            packed.seek(0)
            digest = hashlib.sha256()
            packed_size = 24 + SLOTS * 4 + records * RECORD_SIZE
            hashed = 0
            report("packing", 0, packed_size)
            while data := packed.read(CHUNK):
                digest.update(data)
                hashed += len(data)
                report("packing", hashed, packed_size)
            original = lock["outputs"]["nand"]
            lock["outputs"]["nand"] = {"path": "nand", "format": "ITNAND01",
                "sha256": digest.hexdigest(), "pages": records,
                "source_listing_sha256": original["listing_sha256"]}
        else:
            storage.ensure_directory(destination / "nand")
        checkpoint()
        with create("device.lock.json") as output:
            output.write((json.dumps(lock, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
            output.flush()
            storage.flush_file(output.fileno())
        report("complete", total, total)
        return lock


def packed_folder(source):
    """Light Touch for Mac's packed base (FirmwareKit PreparedBase format 2): a folder, not an .itbase file."""
    source = Path(source)
    index = source / "index.json"
    return index.is_file() and not index.is_symlink() and (source / "files").is_dir()


def _read_small(storage, path, limit):
    with os.fdopen(storage.open_regular(path), "rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Prepared file exceeds its size bound: " + str(path))
    return data


def import_packed_folder(source, destination, expected_sha256, *, storage=DEFAULT_STORAGE,
                         progress=None, cancelled=None, workers=WORKERS):
    """Unpack a packed folder and return its device-lock dict, like import_prepared.

    index.json names every whole file (under files/) and every NAND image as 1 MiB chunks, each
    zlib-compressed on its own under the image's name less ".pages" (nand/bus0-ce0.pages ->
    nand/bus0-ce0/000123.z); an all-zero chunk has no file and stays a hole. ``expected_sha256`` is
    index.json's digest. Each file and chunk must match the index's SHA-256, and the whole result the
    lock's. Images unpack on ``workers`` threads (zlib, hashlib and file writes release the GIL).
    """
    expected_sha256 = _digest(expected_sha256)
    source, destination = Path(source), Path(destination)
    if not destination.is_absolute() or os.path.lexists(destination):
        raise ValueError("Choose an absent absolute import destination.")

    def checkpoint():
        if cancelled is not None and cancelled():
            raise ImportCancelled("Prepared-device import cancelled.")

    raw_index = _read_small(storage, source / "index.json", MAX_INDEX)
    if hashlib.sha256(raw_index).hexdigest() != expected_sha256:
        raise ValueError("The prepared folder changed since it was chosen.")
    index = _json(raw_index)
    if not isinstance(index, dict) or index.get("format") != 2 or index.get("chunkBytes") != CHUNK:
        raise ValueError("Unsupported prepared folder format.")
    files, images = index.get("files"), index.get("images")
    if not isinstance(files, list) or not isinstance(images, list) or not 0 < len(files) + len(images) <= MAX_ENTRIES:
        raise ValueError("Invalid prepared folder index.")
    names, total = set(), 0
    plan_files, plan_images = [], []
    for entry in files + images:
        checkpoint()
        name = _name(entry.get("name") if isinstance(entry, dict) else None)
        size = entry.get("size")
        if type(size) is not int or not 0 <= size <= MAX_BYTES or name.casefold() in names or name == "source-device.lock.json":
            raise ValueError("Invalid or duplicate prepared folder entry.")
        names.add(name.casefold())
        if "chunks" in entry:
            chunks = entry["chunks"]
            if not name.startswith("nand/") or not name.endswith(".pages") or not isinstance(chunks, dict):
                raise ValueError("Invalid prepared NAND image: " + name)
            for number, digest in chunks.items():
                if not _CHUNK_NUMBER.fullmatch(number) or int(number) * CHUNK >= size:
                    raise ValueError("Invalid prepared NAND chunk: " + name + " " + str(number))
                _digest(digest)
                total += min(CHUNK, size - int(number) * CHUNK)
            plan_images.append((name, size, {int(n): d.lower() for n, d in chunks.items()}))
        else:
            plan_files.append((name, size, _digest(entry.get("sha256"))))
            total += size
        if total > MAX_BYTES:
            raise ValueError("Prepared folder exceeds the 64 GiB import limit.")
    for name in names:
        parts = name.split("/")
        if any("/".join(parts[:n]) in names for n in range(1, len(parts))):
            raise ValueError("A file conflicts with a prepared folder directory.")
    if "device.lock.json" not in names or dict((n, s) for n, s, _ in plan_files).get("device.lock.json", CHUNK + 1) > CHUNK:
        raise ValueError("Missing device lock, or it exceeds 1 MiB.")

    guard = threading.Lock()
    done, last_percent = 0, -1

    def account(count):
        nonlocal done, last_percent
        with guard:
            done += count
            percent = done * 100 // max(total, 1)
            if percent != last_percent and progress is not None:
                progress("extracting", done, total)
                last_percent = percent

    def create(name):
        path = destination / name
        storage.ensure_directory(path.parent)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
        return storage.open_regular(path, flags)

    storage.ensure_directory(destination)
    if progress is not None:
        progress("extracting", 0, total)
    hashes, source_lock = {}, None
    for name, size, expected in plan_files:
        checkpoint()
        digest = hashlib.sha256()
        with os.fdopen(storage.open_regular(source / "files" / name), "rb", buffering=0) as input:
            out = create("source-device.lock.json" if name == "device.lock.json" else name)
            with os.fdopen(out, "wb", buffering=0) as output:
                remaining = size
                while remaining:
                    checkpoint()
                    data = input.read(min(remaining, CHUNK))
                    if not data:
                        raise ValueError("Prepared file is truncated: " + name)
                    output.write(data)
                    digest.update(data)
                    remaining -= len(data)
                    account(len(data))
                    if name == "device.lock.json":
                        source_lock = (source_lock or b"") + data
                if input.read(1):
                    raise ValueError("Prepared file is longer than its index entry: " + name)
                storage.flush_file(output.fileno())
        if digest.hexdigest() != expected:
            raise ValueError("Prepared file checksum differs: " + name)
        hashes[name] = expected

    zeros = bytes(CHUNK)
    failed = threading.Event()

    def unpack(name, size, chunks):
        # One thread per image: its chunks go in order, so the whole image hashes as it is written.
        folder = source / name[:-len(".pages")]
        whole = hashlib.sha256()
        fd = create(name)
        try:
            storage.make_sparse(fd)
            os.ftruncate(fd, size)
            for number in range((size + CHUNK - 1) // CHUNK):
                checkpoint()
                if failed.is_set():
                    raise ImportCancelled("Another NAND image failed.")
                length = min(CHUNK, size - number * CHUNK)
                expected = chunks.get(number)
                if expected is None:
                    whole.update(memoryview(zeros)[:length])
                    continue
                packed = _read_small(storage, folder / ("%06d.z" % number), 2 * CHUNK)
                try:
                    data = zlib.decompress(packed, bufsize=CHUNK)
                except zlib.error as error:
                    raise ValueError("Corrupt prepared NAND chunk: %s %06d" % (name, number)) from error
                if len(data) != length or hashlib.sha256(data).hexdigest() != expected:
                    raise ValueError("Prepared NAND chunk checksum differs: %s %06d" % (name, number))
                os.lseek(fd, number * CHUNK, os.SEEK_SET)
                view = memoryview(data)
                while view:
                    view = view[os.write(fd, view):]
                whole.update(data)
                account(length)
            storage.flush_file(fd)
        finally:
            os.close(fd)
        return name, whole.hexdigest()

    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(plan_images) or 1))) as pool:
        futures = [pool.submit(unpack, *image) for image in plan_images]
        try:
            for future in futures:
                name, digest = future.result()
                hashes[name] = digest
        except BaseException:
            failed.set()
            for future in futures:
                future.cancel()
            raise
    checkpoint()
    lock = _json(source_lock)
    if isinstance(lock, dict) and lock.get("board") == "n72ap":
        raise ValueError("Add the iPod touch 2G from its .itbase file instead.")
    _verify_lock(lock, hashes, False, checkpoint)
    out = create("device.lock.json")
    with os.fdopen(out, "wb", buffering=0) as output:
        output.write(source_lock)
        storage.flush_file(output.fileno())
    if progress is not None:
        progress("complete", total, total)
    return lock


def _verify_lock(lock, hashes, packed, checkpoint):
    if (not isinstance(lock, dict) or type(lock.get("format")) is not int or lock["format"] != 1
            or lock.get("board") not in ("n72ap", "n81ap")
            or not isinstance(lock.get("build"), str) or not lock["build"].strip()
            or not isinstance(lock.get("outputs"), dict)):
        raise ValueError("Unsupported prepared base identity.")
    outputs = lock["outputs"]
    required = {"nor": "nor.bin"}
    if lock["board"] == "n72ap":
        required["gid_blobs"] = "gid-blobs.bin"
        if lock.get("boot_strategy", "iboot") == "iboot":
            required["iboot"] = "iBoot.bin"
    else:
        required["kboot"] = "kboot.bin"
        if packed:
            raise ValueError("n81 NAND cannot use n72 packed geometry.")
    for key, name in required.items():
        record = outputs.get(key)
        if not isinstance(record, dict) or record.get("path") != name or "sha256" not in record:
            raise ValueError("Missing authoritative prepared output: " + name)
    nand = outputs.get("nand")
    if not isinstance(nand, dict) or nand.get("path") != "nand" or "listing_sha256" not in nand:
        raise ValueError("Missing authoritative source NAND listing.")
    if "sha256" in nand or "nand" in hashes:
        raise ValueError("Source NAND must be a directory listing.")
    # The n72's listing counts its pages; the n81's lists a few whole-chip images instead.
    if lock["board"] == "n72ap" and (type(nand.get("pages")) is not int or nand["pages"] < 0):
        raise ValueError("Missing authoritative NAND page count.")
    for record in outputs.values():
        checkpoint()
        if not isinstance(record, dict):
            raise ValueError("Invalid prepared output integrity record.")
        name = _name(record.get("path"))
        if "sha256" in record:
            if hashes.get(name) != _digest(record["sha256"]):
                raise ValueError("Prepared output checksum differs: " + name)
        if "listing_sha256" in record:
            listing = hashlib.sha256()
            prefix = name + "/"
            children = sorted(key for key in hashes if key.startswith(prefix))
            for child in children:
                checkpoint()
                listing.update((child[len(prefix):] + " " + hashes[child] + "\n").encode("utf-8"))
            if listing.hexdigest() != _digest(record["listing_sha256"]):
                raise ValueError("Prepared listing checksum differs: " + name)
            if "pages" in record and (type(record["pages"]) is not int or record["pages"] != len(children)):
                raise ValueError("Prepared listing page count differs: " + name)
    identity = lock.get("identity")
    if isinstance(identity, dict) and "sha256" in identity:
        if hashes.get("identity.json") != _digest(identity["sha256"]):
            raise ValueError("Prepared identity checksum differs.")
