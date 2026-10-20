"""Synthetic common importer tests; no firmware, guest or speed qualification."""
import copy
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

from lighttouch_desktop.prepared_import import (
    CHUNK, ImportCancelled, PAGES_PER_CS, RECORD_SIZE, SLOTS, import_packed_folder, import_prepared, packed_folder,
)
from lighttouch_desktop.storage import StorageOps


def sha(data):
    return hashlib.sha256(data).hexdigest()


class RecordingStorage(StorageOps):
    def __init__(self):
        self.opens = []
        self.flushed = 0

    def open_regular(self, path, flags=os.O_RDONLY, mode=0o600):
        self.opens.append((Path(path), flags))
        return super().open_regular(path, flags, mode)

    def flush_file(self, fd):
        self.flushed += 1
        super().flush_file(fd)


class PreparedImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.serial = 0

    def fixture(self, board="n72ap", pages=None):
        if pages is None:
            pages = ({"cs3/524287.page": bytes([3]) * RECORD_SIZE,
                      "cs0/10.page": bytes([10]) * RECORD_SIZE,
                      "cs0/2.page": bytes([2]) * RECORD_SIZE}
                     if board == "n72ap" else {"0.pages": bytes(range(256)) * 9000, "1.spare": b"s" * 200})
        files = {"nor.bin": b"nor", "identity.json": b'{"name":"synthetic"}'}
        outputs = {"nor": {"path": "nor.bin", "sha256": sha(files["nor.bin"])}}
        for key, name in (("gid_blobs", "gid-blobs.bin"), ("iboot", "iBoot.bin")) if board == "n72ap" else (("kboot", "kboot.bin"),):
            files[name] = name.encode()
            outputs[key] = {"path": name, "sha256": sha(files[name])}
        listing = b"".join((name + " " + sha(data) + "\n").encode() for name, data in sorted(pages.items()))
        outputs["nand"] = {"path": "nand", "listing_sha256": sha(listing), "pages": len(pages)}
        lock = {"format": 1, "board": board, "build": "TEST", "outputs": outputs,
                "identity": {"sha256": sha(files["identity.json"])}, "recipe_extra": {"keep": True}}
        files.update(("nand/" + name, data) for name, data in pages.items())
        # Keep source lock LAST: packing must not require lock-first archives.
        files["device.lock.json"] = json.dumps(lock).encode()
        return files, lock

    def archive(self, files, *, extra=b"", compressed_suffix=b"", index_entries=None):
        self.serial += 1
        entries = index_entries if index_entries is not None else [{"name": n, "size": len(v), "mode": 0o644} for n, v in files.items()]
        index = json.dumps({"entries": entries}).encode()
        raw = b"ITPACK01" + struct.pack("<I", len(index)) + index + zlib.compress(b"".join(files.values()) + extra) + compressed_suffix
        path = self.root / f"source-{self.serial}.itbase"
        path.write_bytes(raw)
        return path, sha(raw)

    def run_import(self, files, **kwargs):
        path, digest = self.archive(files)
        destination = self.root / ("stage-" + str(self.serial))
        original_bytes = path.read_bytes()
        signature = (path.stat().st_mtime_ns, path.stat().st_size)
        lock = import_prepared(path, destination, digest, **kwargs)
        self.assertEqual(path.read_bytes(), original_bytes)
        self.assertEqual((path.stat().st_mtime_ns, path.stat().st_size), signature)
        return path, destination, lock

    def test_n72_sparse_packed_byte_layout_and_source_lock(self):
        files, original = self.fixture()
        storage = RecordingStorage()
        events = []
        _, stage, result = self.run_import(files, storage=storage, progress=lambda *event: events.append(event))
        packed = (stage / "nand").read_bytes()
        self.assertEqual(struct.unpack("<8s4I", packed[:24]), (b"ITNAND01", 4096, 64, 4, PAGES_PER_CS))
        self.assertEqual(len(packed), 24 + SLOTS * 4 + 3 * RECORD_SIZE)
        self.assertEqual(struct.unpack_from("<I", packed, 24)[0], 0)
        expected_records = [files[n] for n in files if n.startswith("nand/")]
        for slot, record in ((3 * PAGES_PER_CS + 524287, 1), (10, 2), (2, 3)):
            self.assertEqual(struct.unpack_from("<I", packed, 24 + slot * 4)[0], record)
            start = 24 + SLOTS * 4 + (record - 1) * RECORD_SIZE
            self.assertEqual(packed[start:start + RECORD_SIZE], expected_records[record - 1])
        self.assertEqual((stage / "source-device.lock.json").read_bytes(), files["device.lock.json"])
        self.assertEqual(result["recipe_extra"], original["recipe_extra"])
        self.assertEqual(result["outputs"]["nand"], {"path": "nand", "format": "ITNAND01", "sha256": sha(packed), "pages": 3,
                                                     "source_listing_sha256": original["outputs"]["nand"]["listing_sha256"]})
        self.assertEqual(json.loads((stage / "device.lock.json").read_bytes()), result)
        self.assertFalse(any(path.suffix == ".page" for path, _ in storage.opens))
        self.assertTrue(storage.flushed)
        self.assertEqual(events[-1][0], "complete")
        for phase in {event[0] for event in events}:
            counts = [done for name, done, _ in events if name == phase]
            self.assertEqual(counts, sorted(counts))

    def test_page_count_does_not_increase_output_file_operations(self):
        files, _ = self.fixture(pages={f"cs0/{n}.page": bytes([n % 256]) * RECORD_SIZE for n in range(300)})
        storage = RecordingStorage()
        _, stage, result = self.run_import(files, storage=storage)
        writes = [path for path, flags in storage.opens if flags & os.O_CREAT]
        self.assertEqual(len(writes), 7)  # NAND, nor, gid, iboot, identity, two locks
        self.assertEqual(result["outputs"]["nand"]["pages"], 300)
        self.assertEqual(len(list(stage.rglob("*"))), 7)

    def test_n81_large_files_stay_byte_exact_with_directory_nand(self):
        files, original = self.fixture("n81ap")
        events = []
        _, stage, result = self.run_import(files, progress=lambda *event: events.append(event))
        self.assertTrue((stage / "nand").is_dir())
        self.assertTrue(any(1024 * 1024 <= done < len(files["nand/0.pages"])
                            for phase, done, _ in events if phase == "extracting"))
        self.assertEqual(result, original)
        for name, content in files.items():
            if name != "device.lock.json":
                self.assertEqual((stage / name).read_bytes(), content)

    def test_empty_nand_packs_only_n72(self):
        for board in ("n72ap", "n81ap"):
            files, _ = self.fixture(board, pages={})
            _, stage, _ = self.run_import(files)
            self.assertEqual((stage / "nand").is_file(), board == "n72ap")

    def test_archive_digest_and_trailer_corruption_never_finish(self):
        files, _ = self.fixture()
        for corruption in ("digest", "trailer", "truncated", "extra-plain", "extra-compressed", "concatenated"):
            with self.subTest(corruption=corruption):
                source, digest = self.archive(files, extra=b"extra" if corruption == "extra-plain" else b"",
                    compressed_suffix=b"tail" if corruption == "extra-compressed" else zlib.compress(b"") if corruption == "concatenated" else b"")
                raw = source.read_bytes()
                if corruption == "trailer":
                    source.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
                    digest = sha(source.read_bytes())
                elif corruption == "truncated":
                    source.write_bytes(raw[:-4])
                    digest = sha(source.read_bytes())
                elif corruption == "digest":
                    digest = "0" * 64
                stage = self.root / ("bad-" + str(self.serial))
                with self.assertRaises(ValueError):
                    import_prepared(source, stage, digest)
                self.assertFalse((stage / "device.lock.json").exists())

    def test_source_authoritative_hash_listing_count_and_identity(self):
        files, lock = self.fixture()
        for kind in ("listing", "pages", "nor", "identity"):
            with self.subTest(kind=kind):
                altered = copy.deepcopy(lock)
                if kind == "listing":
                    altered["outputs"]["nand"]["listing_sha256"] = "0" * 64
                elif kind == "pages":
                    altered["outputs"]["nand"]["pages"] += 1
                elif kind == "nor":
                    altered["outputs"]["nor"]["sha256"] = "0" * 64
                else:
                    altered["identity"]["sha256"] = "0" * 64
                changed = dict(files, **{"device.lock.json": json.dumps(altered).encode()})
                with self.assertRaises(ValueError):
                    self.run_import(changed)

    def test_page_geometry_and_aliases_rejected(self):
        for page, size in (("cs0/524288.page", RECORD_SIZE), ("cs0/01.page", RECORD_SIZE),
                           ("cs4/1.page", RECORD_SIZE), ("cs0/0.page", 4096), ("cs0/not-a-page", RECORD_SIZE)):
            files, _ = self.fixture(pages={page: b"x" * size})
            with self.subTest(page=page, size=size), self.assertRaises(ValueError):
                self.run_import(files)

    def test_unsafe_duplicate_conflicting_and_reserved_paths(self):
        files, _ = self.fixture()
        for bad in ("../escape", "/absolute", "nand\\evil", "AUX.txt", "x.", "NAND/CS0/10.PAGE", "source-device.lock.json"):
            changed = dict(files)
            changed[bad] = b"x"
            with self.subTest(path=bad), self.assertRaises(ValueError):
                self.run_import(changed)
        changed = dict(files)
        changed["nor.bin/child"] = b"x"
        with self.assertRaises(ValueError):
            self.run_import(changed)
        source, digest = self.archive(files, index_entries=[{"name": "device.lock.json", "size": 0}] * 2)
        with self.assertRaises(ValueError):
            import_prepared(source, self.root / "duplicate", digest)

    def test_cancellation_and_existing_destination(self):
        files, _ = self.fixture()
        source, digest = self.archive(files)
        existing = self.root / "existing"
        existing.mkdir()
        (existing / "keep").write_bytes(b"retain")
        with self.assertRaises(ValueError):
            import_prepared(source, existing, digest)
        self.assertEqual((existing / "keep").read_bytes(), b"retain")
        cancelled = False
        def progress(phase, done, total):
            nonlocal cancelled
            if phase == "extracting" and done:
                cancelled = True
        stage = self.root / "cancelled"
        with self.assertRaises(ImportCancelled):
            import_prepared(source, stage, digest, progress=progress, cancelled=lambda: cancelled)
        self.assertFalse((stage / "device.lock.json").exists())
        # All file handles are closed even though adapter cleanup is deferred.
        if (stage / "nand").exists():
            (stage / "nand").unlink()

    def test_cancellation_during_packed_hash_has_no_final_lock(self):
        files, _ = self.fixture()
        source, digest = self.archive(files)
        cancelled = False
        def progress(phase, done, total):
            nonlocal cancelled
            if phase == "packing":
                cancelled = True
        stage = self.root / "cancel-packing"
        with self.assertRaises(ImportCancelled):
            import_prepared(source, stage, digest, progress=progress, cancelled=lambda: cancelled)
        self.assertFalse((stage / "device.lock.json").exists())
        self.assertTrue((stage / "source-device.lock.json").is_file())

    def test_source_mutation_on_same_handle_is_rejected(self):
        files, _ = self.fixture()
        source, digest = self.archive(files)
        modified = False
        def progress(phase, done, total):
            nonlocal modified
            if not modified and phase == "extracting":
                modified = True
                os.utime(source, ns=(1_000_000_000, 1_000_000_000))
        with self.assertRaises(ValueError):
            import_prepared(source, self.root / "mutation", digest, progress=progress)


class PackedFolderTests(unittest.TestCase):
    """Light Touch for Mac's packed folder: whole files plus NAND images as 1 MiB zlib chunks, zero chunks absent."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def folder(self, board="n81ap"):
        size = 3 * CHUNK + 5
        image = bytearray(size)
        image[CHUNK:CHUNK + 4] = b"used"           # chunk 1 stored
        image[3 * CHUNK:] = b"tail!"              # the short last chunk stored
        pages = {"bus0-ce0.pages": bytes(image), "geometry.json": b'{"chips":1}'}
        files = {"nor.bin": b"nor", "identity.json": b'{"name":"synthetic"}', "kboot.bin": b"kboot",
                 "nand/geometry.json": pages["geometry.json"]}
        outputs = {"nor": {"path": "nor.bin", "sha256": sha(b"nor")}, "kboot": {"path": "kboot.bin", "sha256": sha(b"kboot")}}
        listing = b"".join((n + " " + sha(d) + "\n").encode() for n, d in sorted(pages.items()))
        outputs["nand"] = {"path": "nand", "listing_sha256": sha(listing)}
        lock = {"format": 1, "board": board, "build": "TEST", "outputs": outputs, "identity": {"sha256": sha(files["identity.json"])}}
        files["device.lock.json"] = json.dumps(lock).encode()
        source = self.root / "source.itbase"
        for name, data in files.items():
            (source / "files" / name).parent.mkdir(parents=True, exist_ok=True)
            (source / "files" / name).write_bytes(data)
        chunks = {}
        (source / "nand/bus0-ce0").mkdir(parents=True)
        for number in (1, 3):
            raw = bytes(image[number * CHUNK:(number + 1) * CHUNK])
            chunks["%06d" % number] = sha(raw)
            (source / "nand/bus0-ce0" / ("%06d.z" % number)).write_bytes(zlib.compress(raw))
        index = {"format": 2, "chunkBytes": CHUNK,
                 "files": [{"name": n, "size": len(d), "mode": 0o644, "sha256": sha(d)} for n, d in files.items()],
                 "images": [{"name": "nand/bus0-ce0.pages", "size": size, "mode": 0o644, "chunks": chunks}]}
        (source / "index.json").write_bytes(json.dumps(index).encode())
        return source, image, files

    def test_unpacks_holes_and_whole_files(self):
        source, image, files = self.folder()
        self.assertTrue(packed_folder(source))
        events = []
        stage = self.root / "stage"
        lock = import_packed_folder(source, stage, sha((source / "index.json").read_bytes()),
                                    progress=lambda *event: events.append(event))
        self.assertEqual((stage / "nand/bus0-ce0.pages").read_bytes(), bytes(image))
        self.assertLess(os.stat(stage / "nand/bus0-ce0.pages").st_blocks * 512, 3 * CHUNK)  # the zero chunks stay holes
        for name, data in files.items():
            self.assertEqual((stage / name).read_bytes(), data)
        self.assertEqual((stage / "source-device.lock.json").read_bytes(), files["device.lock.json"])
        self.assertEqual(lock["board"], "n81ap")
        self.assertEqual(events[-1], ("complete", CHUNK + 5 + sum(map(len, files.values())), CHUNK + 5 + sum(map(len, files.values()))))

    def test_rejects_changed_index_bad_chunk_and_n72(self):
        source, _, _ = self.folder()
        with self.assertRaisesRegex(ValueError, "changed"):
            import_packed_folder(source, self.root / "a", "0" * 64)
        chunk = source / "nand/bus0-ce0/000001.z"
        chunk.write_bytes(zlib.compress(b"x" * CHUNK))
        with self.assertRaisesRegex(ValueError, "checksum"):
            import_packed_folder(source, self.root / "b", sha((source / "index.json").read_bytes()))
        source.rename(self.root / "old")
        source, _, _ = self.folder(board="n72ap")
        with self.assertRaisesRegex(ValueError, ".itbase file"):
            import_packed_folder(source, self.root / "c", sha((source / "index.json").read_bytes()))


if __name__ == "__main__":
    unittest.main()
