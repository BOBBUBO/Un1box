import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from preparation import Preparation


def archive(path, payload=b'synthetic', size=None):
    index = json.dumps({'entries': [{'name': 'device.lock.json', 'size': len(payload) if size is None else size, 'mode': 256}]}).encode()
    path.write_bytes(b'ITPACK01' + len(index).to_bytes(4, 'little') + index + payload)
    return path


class PreparationTests(unittest.TestCase):
    def run_job(self, path, cancel=False):
        job = Preparation(path)
        if cancel:
            job.cancelled.set()
        job.start()
        job.thread.join(3)
        self.assertFalse(job.active)
        return job

    def test_readonly_archive_digest_and_expanded_space_are_automatic(self):
        with tempfile.TemporaryDirectory() as root:
            source = archive(Path(root) / 'system.itbase', b'x' * (3 * 1024**2))
            before = source.read_bytes()
            source.chmod(0o444)
            result = self.run_job(source)
            self.assertIsNone(result.error)
            self.assertEqual(result.result, {'sha256': hashlib.sha256(before).hexdigest(), 'expanded_bytes': 3 * 1024**2})
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(source.stat().st_mode & 0o777, 0o444)

    def test_wrong_file_truncation_and_oversize_fail_before_native_import(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / 'bad.itbase'
            for content in (b'PK fake zip', b'ITPACK01' + (100).to_bytes(4, 'little') + b'{}'):
                source.write_bytes(content)
                self.assertIsNotNone(self.run_job(source).error)
            archive(source, size=65 * 1024**3)
            self.assertIsNotNone(self.run_job(source).error)

    def test_cancel_does_not_publish_a_result_or_change_source(self):
        with tempfile.TemporaryDirectory() as root:
            source = archive(Path(root) / 'system.itbase')
            before = source.read_bytes()
            result = self.run_job(source, cancel=True)
            self.assertIsNone(result.result)
            self.assertIn('cancelled', result.error)
            self.assertEqual(source.read_bytes(), before)

    def test_symlink_refused(self):
        with tempfile.TemporaryDirectory() as root:
            source = archive(Path(root) / 'system.itbase')
            link = Path(root) / 'alias.itbase'
            link.symlink_to(source)
            self.assertIsNotNone(self.run_job(link).error)


if __name__ == '__main__':
    unittest.main()
