"""Exercise the compiled Linux finalizer CLI with synthetic packed NAND; no guest."""
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import tempfile
import unittest


FRONTEND = os.environ.get("LIGHTTOUCH_LINUX_FRONTEND")


@unittest.skipUnless(FRONTEND, "set LIGHTTOUCH_LINUX_FRONTEND to the compiled native frontend")
class FinalizeImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.cleanup)
        self.root = Path(self.temp.name)
        self.stage = self.root / ".lt-import-caller"
        self.stage.mkdir(mode=0o700)
        self.destination = self.root / "base"
        packed = bytearray(b"ITNAND01" + struct.pack("<IIII", 4096, 64, 4, 524288))
        packed += bytes(4 * 524288 * 4)
        struct.pack_into("<I", packed, 24 + 7 * 4, 1)
        packed += b"r" * 4160
        self.lock = dict(format=1, board="n72ap", build="7E18", boot_strategy="bootrom", outputs={
            "nand": dict(path="nand", format="ITNAND01", sha256=hashlib.sha256(packed).hexdigest(),
                         pages=1, source_listing_sha256="a" * 64)})
        for name, content in (("nand", packed), ("nor.bin", b"nor"), ("gid-blobs.bin", b"gid")):
            self.put(name, content)
            if name != "nand":
                self.lock["outputs"][name] = dict(path=name, sha256=hashlib.sha256(content).hexdigest())
        # Required output keys differ from file names.
        self.lock["outputs"]["nor"] = self.lock["outputs"].pop("nor.bin")
        self.lock["outputs"]["gid_blobs"] = self.lock["outputs"].pop("gid-blobs.bin")
        self.put("device.lock.json", json.dumps(self.lock).encode())
        self.put("source-device.lock.json", b"exact original lock bytes retained")

    def put(self, name, content):
        path = self.stage / name
        path.write_bytes(content)
        path.chmod(0o600)

    def cleanup(self):
        for directory, children, files in os.walk(self.temp.name):
            Path(directory).chmod(0o700)
        self.temp.cleanup()

    def run_finalize(self, *extra):
        return subprocess.run([FRONTEND, "--finalize-import", str(self.stage), "--base", str(self.destination), *extra],
                              capture_output=True, text=True, timeout=10)

    def test_publishes_same_inode_and_freezes_without_creating_guest_state(self):
        before = (self.stage / "nand").stat()
        result = self.run_finalize()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("prepared base imported:", result.stdout)
        self.assertIn("import phase=verifying", result.stderr)
        self.assertFalse(self.stage.exists())
        after = (self.destination / "nand").stat()
        self.assertEqual((before.st_dev, before.st_ino), (after.st_dev, after.st_ino))
        self.assertEqual(stat.S_IMODE(after.st_mode), 0o400)
        self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode), 0o500)
        self.assertEqual((self.destination / "source-device.lock.json").read_bytes(), b"exact original lock bytes retained")
        self.assertFalse((self.root / "state").exists())

    def test_existing_destination_and_bad_digest_retain_caller_stage(self):
        self.destination.mkdir()
        marker = self.destination / "keep"
        marker.write_bytes(b"existing base")
        self.assertNotEqual(self.run_finalize().returncode, 0)
        self.assertEqual(marker.read_bytes(), b"existing base")
        marker.unlink()
        self.destination.rmdir()
        with (self.stage / "nand").open("r+b") as output:
            output.seek(-1, 2)
            output.write(b"!")
        result = self.run_finalize()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum mismatch", result.stderr)
        self.assertTrue(self.stage.exists())
        self.assertFalse(self.destination.exists())

    def test_authentic_hash_cannot_admit_wrong_packed_geometry(self):
        packed = bytearray((self.stage / "nand").read_bytes())
        struct.pack_into("<I", packed, 16, 1)
        self.put("nand", packed)
        self.lock["outputs"]["nand"]["sha256"] = hashlib.sha256(packed).hexdigest()
        self.put("device.lock.json", json.dumps(self.lock).encode())
        result = self.run_finalize()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("address geometry", result.stderr)
        self.assertTrue(self.stage.exists())
        self.assertFalse(self.destination.exists())

    def test_finalize_never_combines_with_guest_launch(self):
        result = self.run_finalize("--engine", "/missing/engine")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--finalize-import requires", result.stderr)
        self.assertTrue(self.stage.exists())
        self.assertFalse(self.destination.exists())


if __name__ == "__main__":
    unittest.main()
