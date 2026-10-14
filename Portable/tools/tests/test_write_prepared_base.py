#!/usr/bin/env python3
"""Tiny synthetic assets only. --importer must use the production Storage.cpp."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zlib

TOOL = Path(__file__).resolve().parents[1] / "write_prepared_base.py"
spec = importlib.util.spec_from_file_location("prepared_base_writer", TOOL)
writer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(writer)
IMPORTER = None


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


class PreparedBaseWriter(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="lt-archive-test-")
        self.root = Path(self.temporary.name)
        self.base = self.root / "base"
        (self.base / "nand").mkdir(parents=True)
        self.payload = {"identity.json": b'{"synthetic":true}\n', "kboot.bin": b"synthetic kboot\0",
                        "nor.bin": b"synthetic nor\0"}
        for name in writer.NAND_NAMES:
            # Cross multiple compression/read chunk boundaries, still <2MiB total.
            self.payload["nand/" + name] = (name.encode() + b"\0") * 11000 if name != "geometry.json" else b"{}\n"
        nand_hashes = {name: sha(self.payload["nand/" + name]) for name in writer.NAND_NAMES}
        listing = "".join(name + " " + nand_hashes[name] + "\n" for name in sorted(nand_hashes))
        lock = {"format": 1, "board": "n81ap", "build": "TEST", "boot_strategy": "kboot",
                "identity": {"sha256": sha(self.payload["identity.json"])},
                "outputs": {"kboot": {"path": "kboot.bin", "sha256": sha(self.payload["kboot.bin"])},
                            "nor": {"path": "nor.bin", "sha256": sha(self.payload["nor.bin"])},
                            "nand": {"path": "nand", "files": nand_hashes, "listing_sha256": sha(listing.encode())}}}
        self.payload["device.lock.json"] = json.dumps(lock).encode()
        for name, data in self.payload.items():
            path = self.base / name
            path.write_bytes(data)
            path.chmod(0o400)
        (self.base / "nand").chmod(0o500)
        self.base.chmod(0o500)
        self.pin = sha(self.payload["device.lock.json"])
        self.output = self.root / "bundle"

    def tearDown(self):
        # Tests may run as a non-root user; production imported bases are immutable.
        for path, directories, _ in os.walk(self.root):
            os.chmod(path, 0o700)
            for name in directories:
                child = Path(path) / name
                if not child.is_symlink():
                    child.chmod(0o700)
        self.temporary.cleanup()

    def snapshot(self):
        result = {}
        for path in [self.base, self.base / "nand"] + [self.base / n for n in sorted(self.payload)]:
            st = path.lstat()
            result[str(path.relative_to(self.base))] = (st.st_dev, st.st_ino, st.st_mode, st.st_size,
                                                       st.st_mtime_ns, st.st_ctime_ns,
                                                       sha(path.read_bytes()) if path.is_file() else None)
        return result

    def run_writer(self, pin=None, source=None, output=None):
        return subprocess.run([sys.executable, str(TOOL), "--source", str(source or self.base),
                               "--lock-sha256", pin or self.pin, "--output", str(output or self.output)],
                              capture_output=True, text=True, timeout=30)

    def accepted_bundle(self):
        before = self.snapshot()
        result = self.run_writer()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(before, self.snapshot(), "Writer mutated verified source")
        return self.output / "base.itbase"

    def imported(self, archive, expected=None, success=True, label="imported"):
        destination = self.root / label
        run = subprocess.run([str(IMPORTER), str(archive), str(destination), expected or sha(archive.read_bytes())],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(0 if success else 1, run.returncode, run.stdout + run.stderr)
        self.assertEqual(success, destination.exists())
        self.assertFalse(any(self.root.glob(".lt-import-*")), "Production importer left partial staging")
        return destination, run.stdout

    def changed_archive(self, archive, *, index_change=None, payload_change=None, truncate=False):
        blob = archive.read_bytes()
        count = struct.unpack("<I", blob[8:12])[0]
        index = json.loads(blob[12:12 + count])
        stream = blob[12 + count:]
        if truncate:
            stream = stream[:-5]
        if index_change:
            index_change(index)
        if payload_change:
            stream = zlib.compress(payload_change(zlib.decompress(stream), index))
        encoded = json.dumps(index).encode()
        target = self.root / "changed.itbase"
        target.write_bytes(b"ITPACK01" + struct.pack("<I", len(encoded)) + encoded + stream)
        return target

    def test_stream_roundtrip_through_production_importer_and_receipt(self):
        archive = self.accepted_bundle()
        receipt = json.loads((self.output / "source-receipt.json").read_text())
        self.assertEqual(self.pin, receipt["trusted_lock_sha256"])
        self.assertEqual(sha(archive.read_bytes()), receipt["archive"]["sha256"])
        self.assertEqual(sum(map(len, self.payload.values())), receipt["expanded_bytes"])
        self.assertEqual(13, len(receipt["files"]))
        destination, _ = self.imported(archive)
        self.assertEqual(set(self.payload), {str(p.relative_to(destination)) for p in destination.rglob("*") if p.is_file()})
        for name, expected in self.payload.items():
            self.assertEqual(expected, (destination / name).read_bytes())
            self.assertFalse((destination / name).stat().st_mode & 0o222)

    def test_production_importer_rejects_truncated_stream_with_correct_archive_hash(self):
        archive = self.changed_archive(self.accepted_bundle(), truncate=True)
        self.imported(archive, success=False)

    def test_production_importer_rejects_wrong_pinned_archive_hash(self):
        _, error = self.imported(self.accepted_bundle(), expected="0" * 64, success=False)
        self.assertIn("archive SHA256 mismatch", error)

    def test_production_importer_rejects_traversal_despite_correct_archive_hash(self):
        archive = self.changed_archive(self.accepted_bundle(), index_change=lambda index: index["entries"][0].update(name="../escape"))
        self.imported(archive, success=False)
        self.assertFalse((self.root / "escape").exists())

    def test_production_importer_checks_lock_payload_not_just_archive_hash(self):
        def alter(data, index):
            offset = 0
            for entry in index["entries"]:
                if entry["name"] == "kboot.bin":
                    return data[:offset] + bytes([data[offset] ^ 1]) + data[offset + 1:]
                offset += entry["size"]
            raise AssertionError("No kboot entry")
        archive = self.changed_archive(self.accepted_bundle(), payload_change=alter)
        _, error = self.imported(archive, success=False)
        self.assertIn("checksum mismatch", error)

    def test_bad_lock_pin_and_modified_source_never_publish(self):
        before = self.snapshot()
        self.assertNotEqual(0, self.run_writer(pin="0" * 64).returncode)
        self.assertEqual(before, self.snapshot())
        target = self.base / "kboot.bin"
        target.chmod(0o600); target.write_bytes(b"wrong trusted payload"); target.chmod(0o400)
        before = self.snapshot()
        self.assertNotEqual(0, self.run_writer().returncode)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.output.exists())
        self.assertFalse(any(self.root.glob(".lt-base-*")))

    def test_writable_source_is_refused_without_chmod_or_content_mutation(self):
        (self.base / "nor.bin").chmod(0o600)
        before = self.snapshot()
        self.assertNotEqual(0, self.run_writer().returncode)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.output.exists())

    def test_symlink_and_extra_inventory_are_refused(self):
        alias = self.root / "alias"; alias.symlink_to(self.base, target_is_directory=True)
        self.assertNotEqual(0, self.run_writer(source=alias).returncode)
        self.base.chmod(0o700)
        (self.base / "extra").write_bytes(b"not permitted")
        self.base.chmod(0o500)
        self.assertNotEqual(0, self.run_writer().returncode)
        self.assertFalse(self.output.exists())

    def test_leaf_symlink_and_hard_link_are_refused(self):
        original = self.base / "nor.bin"
        other = self.root / "other-nor"
        other.write_bytes(self.payload["nor.bin"])
        self.base.chmod(0o700)
        original.unlink(); original.symlink_to(other)
        self.base.chmod(0o500)
        self.assertNotEqual(0, self.run_writer().returncode)
        self.base.chmod(0o700)
        original.unlink(); os.link(other, original)
        original.chmod(0o400)
        self.base.chmod(0o500)
        self.assertNotEqual(0, self.run_writer().returncode)
        self.assertEqual(self.payload["nor.bin"], other.read_bytes())
        self.assertFalse(self.output.exists())

    def test_trusted_but_unsupported_lock_path_is_refused(self):
        lock = json.loads(self.payload["device.lock.json"])
        lock["outputs"]["kboot"]["path"] = "../kboot.bin"
        path = self.base / "device.lock.json"
        path.chmod(0o600); path.write_text(json.dumps(lock)); path.chmod(0o400)
        before = self.snapshot()
        self.assertNotEqual(0, self.run_writer(pin=sha(path.read_bytes())).returncode)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.output.exists())

    def test_output_never_overwrites_or_enters_source(self):
        self.output.mkdir(); (self.output / "keep").write_bytes(b"preserve")
        before = self.snapshot()
        self.assertNotEqual(0, self.run_writer().returncode)
        self.assertEqual(b"preserve", (self.output / "keep").read_bytes())
        self.assertNotEqual(0, self.run_writer(output=self.base / "new").returncode)
        self.assertEqual(before, self.snapshot())

    def test_atomic_publication_race_never_replaces_new_destination(self):
        actual_publish = writer.publish
        before = self.snapshot()

        def racing_publish(parent_fd, staged, destination):
            self.output.mkdir()
            (self.output / "keep").write_bytes(b"racing output must survive")
            actual_publish(parent_fd, staged, destination)
        with mock.patch.object(writer, "publish", side_effect=racing_publish):
            with self.assertRaises(OSError):
                writer.write_bundle(self.base, self.pin, self.output)
        self.assertEqual(b"racing output must survive", (self.output / "keep").read_bytes())
        self.assertEqual(before, self.snapshot())
        self.assertFalse(any(self.root.glob(".lt-base-*")))

    def test_bounded_reads_and_midstream_mutation_prevent_publication(self):
        target = self.base / "nand/bus0-ce0.pages"
        target.chmod(0o600)
        external = os.open(target, os.O_WRONLY)
        target.chmod(0o400)
        actual_read = os.read
        changed = False

        def racing_read(fd, length):
            nonlocal changed
            self.assertLessEqual(length, 65536)
            data = actual_read(fd, length)
            if not changed and data.startswith(b"bus0-ce0.pages\0"):
                changed = True
                os.pwrite(external, b"X", len(self.payload["nand/bus0-ce0.pages"]) - 1)
            return data
        try:
            with mock.patch.object(writer.os, "read", side_effect=racing_read):
                with self.assertRaisesRegex(ValueError, "source changed|source hash mismatch"):
                    writer.write_bundle(self.base, self.pin, self.output)
        finally:
            os.close(external)
        self.assertTrue(changed)
        self.assertFalse(self.output.exists())
        self.assertFalse(any(self.root.glob(".lt-base-*")))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--importer", required=True, type=Path, help="compiled production Storage.cpp adapter")
    args, remaining = parser.parse_known_args()
    IMPORTER = args.importer.resolve(strict=True)
    unittest.main(argv=[sys.argv[0]] + remaining)
