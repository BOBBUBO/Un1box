"""Archive checks must accept unchanged inputs and reject mutation/replacement."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lighttouch_desktop.preparation import Preparation
from lighttouch_desktop.storage import StorageOps
from lighttouch_desktop.windows_storage import WindowsStorage


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "system.itbase"
        index = json.dumps({"entries": [{"name": "example", "size": 7 * 1024**2}]}).encode()
        with self.path.open("wb") as stream:
            stream.write(b"ITPACK01" + len(index).to_bytes(4, "little") + index)
            stream.seek(7 * 1024**2, 1)
            stream.write(b"x")

    def check(self, job):
        job.start()
        job.thread.join(5)
        self.assertFalse(job.active)
        return job

    def prepare(self):
        storage = WindowsStorage() if os.name == "nt" else StorageOps()
        return Preparation(self.path, storage=storage)

    def test_unchanged_archive_finishes_and_keeps_latest_progress(self):
        job = self.check(self.prepare())
        self.assertIsNone(job.error)
        self.assertEqual(job.result["sha256"], hashlib.sha256(self.path.read_bytes()).hexdigest())
        updates = []
        while not job.events.empty():
            updates.append(job.events.get_nowait())
        self.assertEqual(len(updates), 4)
        self.assertEqual(updates[-1], (self.path.stat().st_size,) * 2)

    @unittest.skipIf(os.name == "nt", "POSIX-host model of the Windows CRT mismatch")
    def test_handle_and_path_use_same_signature_provider_despite_crt_difference(self):
        # Models Windows: CRT fstat gives inode zero; native file ID is not zero.
        # The previous mixed fstat/path-stat comparison rejected this input.
        fstat = os.fstat
        class NativeSignatures(StorageOps):
            def file_signature(inner, fd):
                s = fstat(fd)
                return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        def crt_fstat(fd):
            fields = list(fstat(fd)); fields[1] = 0
            return os.stat_result(fields)
        with patch("lighttouch_desktop.preparation.os.fstat", side_effect=crt_fstat):
            job = self.check(Preparation(self.path, storage=NativeSignatures()))
        self.assertIsNone(job.error)
        self.assertIsNotNone(job.result)

    def test_same_size_in_place_edit_is_rejected(self):
        job = self.prepare()
        update = job.update
        def mutate(done, total):
            if done == 1024**2:
                with self.path.open("r+b") as stream:
                    stream.seek(-1, 2); stream.write(b"y")
            update(done, total)
        job.update = mutate
        self.check(job)
        self.assertIn("changed during setup", job.error)
        self.assertIsNone(job.result)

    @unittest.skipIf(os.name == "nt", "Windows denies rename while the checked handle is open")
    def test_same_size_same_mtime_replacement_is_rejected(self):
        replacement = self.path.with_suffix(".replacement")
        replacement.write_bytes(self.path.read_bytes())
        info = self.path.stat()
        os.utime(replacement, ns=(info.st_atime_ns, info.st_mtime_ns))
        job = self.prepare()
        update = job.update
        def replace(done, total):
            if done == 1024**2:
                os.replace(replacement, self.path)
            update(done, total)
        job.update = replace
        self.check(job)
        self.assertIn("changed during setup", job.error)
        self.assertIsNone(job.result)

    def test_cancellation_does_not_publish_digest(self):
        job = self.prepare()
        job.cancelled.set()
        self.check(job)
        self.assertIn("cancelled", job.error)
        self.assertIsNone(job.result)

    @unittest.skipUnless(os.name == "nt", "requires real Windows file handles")
    def test_native_windows_archive_and_handle_path_identity(self):
        ops = WindowsStorage()
        fd = ops.open_regular(self.path)
        try:
            self.assertEqual(ops.file_signature(fd), ops.path_signature(self.path))
        finally:
            os.close(fd)
        job = self.check(Preparation(self.path, storage=ops))
        self.assertIsNone(job.error)
        self.assertIsNotNone(job.result)


if __name__ == "__main__":
    unittest.main()
