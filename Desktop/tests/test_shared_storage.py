"""Shared algorithms exercised through explicit platform I/O hooks."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lighttouch_desktop import profiles, storage
from lighttouch_desktop.preparation import Preparation
from lighttouch_desktop.storage import StorageOps


class RecordingStorage(StorageOps):
    """A host-contract probe on POSIX, not a claim of Windows API coverage."""
    def __init__(self):
        self.calls = []

    def open_regular(self, path, flags=os.O_RDONLY, mode=0o600):
        self.calls.append(("open", str(path)))
        return super().open_regular(path, flags, mode)

    def immutable_base(self, path, attributes):
        self.calls.append(("immutable", str(path)))
        return True  # Models an explicit host policy; no Windows ACL claim.

    def flush_file(self, fd):
        self.calls.append(("flush", fd))
        super().flush_file(fd)

    def replace_and_sync(self, source, destination):
        self.calls.append(("publish", str(destination)))
        super().replace_and_sync(source, destination)


class SharedStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def profile(self):
        base = self.root / "base"; base.mkdir()
        (base / "device.lock.json").write_text(json.dumps({"format": 1, "board": "n81ap", "build": "8C148", "boot_strategy": "kboot"}))
        package = self.root / "guest.itpack"; package.write_bytes(b"synthetic fixture")
        return {"id": "a" * 32, "name": "Shared", "base": str(base), "state": str(self.root / "state"),
                "guest_package": str(package), "bootrom": "", "audio": True}

    def test_host_immutable_policy_reuses_actual_shared_metadata_validation_and_exact_identity(self):
        profile = self.profile(); ops = RecordingStorage()
        with self.assertRaisesRegex(profiles.SetupError, "writable"):
            profiles.inspect_base(profile["base"])
        info = profiles.validate(profile, storage=ops)
        self.assertEqual(info["base_identity"], hashlib.sha256((Path(profile["base"]) / "device.lock.json").read_bytes()).hexdigest())
        self.assertEqual(info["strategy"], "kboot")
        opens = [path for kind, path in ops.calls if kind == "open"]
        self.assertEqual(opens.count(str(Path(profile["base"]) / "device.lock.json")), 1)
        (Path(profile["base"]) / "device.lock.json").write_text('{"format":1,"board":"unsupported","build":"bad"}')
        with self.assertRaisesRegex(profiles.SetupError, "unsupported"):
            profiles.validate(profile, storage=ops)

    def test_actual_library_roundtrip_routes_flush_and_publication_through_host(self):
        profile = self.profile(); ops = RecordingStorage()
        store = profiles.Profiles(self.root / "library", storage=ops)
        store.save(profile)
        reloaded = profiles.Profiles(store.directory, storage=ops)
        self.assertEqual(reloaded.devices, [profile])
        self.assertEqual(reloaded.selected, profile["id"])
        self.assertLess(next(i for i, call in enumerate(ops.calls) if call[0] == "flush"),
                        next(i for i, call in enumerate(ops.calls) if call[0] == "publish"))
        self.assertFalse(Path(profile["state"]).exists())
        self.assertFalse(list(store.directory.glob(".lighttouch-*")))

    def test_failed_publication_preserves_existing_library_and_in_memory_selection(self):
        profile = self.profile(); ops = RecordingStorage()
        store = profiles.Profiles(self.root / "library", storage=ops); store.save(profile)
        before = store.file.read_bytes()
        replacement = dict(profile, name="Must not become saved")
        with mock.patch.object(ops, "replace_and_sync", side_effect=OSError("host publication refused")):
            with self.assertRaisesRegex(OSError, "refused"):
                store.save(replacement)
        self.assertEqual(store.file.read_bytes(), before)
        self.assertEqual(store.devices, [profile])
        self.assertEqual(store.selected, profile["id"])
        self.assertFalse(list(store.directory.glob(".lighttouch-*")))

    def test_preparation_uses_host_open_and_preserves_original_archive(self):
        index = json.dumps({"entries": [{"name": "example", "size": 3}]}).encode()
        raw = b"ITPACK01" + len(index).to_bytes(4, "little") + index + b"abc"
        path = self.root / "system.itbase"; path.write_bytes(raw)
        ops = RecordingStorage(); job = Preparation(path, storage=ops)
        job.start(); job.thread.join(3)
        self.assertFalse(job.active); self.assertIsNone(job.error)
        self.assertEqual(job.result["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertIn(("open", str(path)), ops.calls)
        self.assertEqual(path.read_bytes(), raw)

    def test_missing_windows_backend_fails_closed_before_a_posix_open(self):
        with mock.patch.object(storage, "os", SimpleNamespace(name="nt")):
            with self.assertRaisesRegex(OSError, "Windows host"):
                StorageOps().open_regular("unused")
            with self.assertRaisesRegex(OSError, "Windows host"):
                StorageOps().replace_and_sync("unused", "unused")

    def test_real_log_symlink_and_multiple_hard_links_are_rejected_without_writes(self):
        target = self.root / "original.log"; target.write_bytes(b"preserve")
        link = self.root / "alias.log"; link.symlink_to(target)
        with self.assertRaises(OSError):
            StorageOps().open_log(link, exclusive=False)
        link.unlink(); os.link(target, link)
        with self.assertRaisesRegex(OSError, "multiple hard links"):
            StorageOps().open_log(link, exclusive=False)
        self.assertEqual(target.read_bytes(), b"preserve")


if __name__ == "__main__":
    unittest.main()
