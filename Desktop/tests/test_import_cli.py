"""Shared import subprocess/publication/cancellation, with a synthetic finalizer.

These checks exercise the real archive streamer and CLI. The tiny native stand-in
tests protocol ownership; platform validators have their own native checks.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
import zlib

from lighttouch_desktop.import_cli import import_command, run
from lighttouch_desktop.profiles import SetupError


class ImportCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        sha = lambda b: hashlib.sha256(b).hexdigest()
        page = bytes(range(256)) * 16 + b'S' * 64
        files = {'nor.bin': b'nor', 'iBoot.bin': b'boot', 'gid-blobs.bin': b'gid', 'nand/cs0/7.page': page}
        lock = {'format': 1, 'board': 'n72ap', 'build': 'TEST', 'outputs': {
            key: {'path': name, 'sha256': sha(files[name])}
            for key, name in (('nor', 'nor.bin'), ('iboot', 'iBoot.bin'), ('gid_blobs', 'gid-blobs.bin'))}}
        lock['outputs']['nand'] = {'path': 'nand', 'pages': 1,
            'listing_sha256': sha(('cs0/7.page ' + sha(page) + '\n').encode())}
        files['device.lock.json'] = json.dumps(lock).encode()
        index = json.dumps({'entries': [{'name': k, 'size': len(v)} for k, v in files.items()]}).encode()
        raw = b'ITPACK01' + len(index).to_bytes(4, 'little') + index + zlib.compress(b''.join(files.values()))
        self.source = self.root/'system.itbase'; self.source.write_bytes(raw)
        self.digest = sha(raw)
        self.destination = self.root/'imported'
        self.frontend = self.root/'native-finalizer'
        self.frontend.write_text('#!' + sys.executable + '\n' + '''import json, pathlib, sys
args = sys.argv[1:]
assert args[0] == '--finalize-import'
stage, destination = pathlib.Path(args[1]), pathlib.Path(args[3])
assert args[2] == '--base' and stage.parent == destination.parent
assert (stage/'nand').is_file()
assert json.loads((stage/'device.lock.json').read_text())['outputs']['nand']['format'] == 'ITNAND01'
assert not list(stage.rglob('*.page'))
stage.rename(destination)
print('import phase=protecting percent=100 done=1 total=1', flush=True)
''')
        self.frontend.chmod(0o700)

    def args(self):
        return SimpleNamespace(source=str(self.source), destination=str(self.destination),
                               sha256=self.digest, platform='linux', frontend=str(self.frontend), validation_cache=None)

    def test_real_cli_streams_and_publishes_one_file_nand(self):
        command = import_command('linux', self.frontend, self.source, self.destination, self.digest)
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue((self.destination/'nand').is_file())
        self.assertEqual(list(self.root.glob('.lighttouch-import-*')), [])
        self.assertIn('[import-timing] phase=total', result.stdout)
        self.assertIn('import phase=extracting', result.stdout)

    def test_launcher_keeps_control_pipe_open_through_successful_exit(self):
        command = import_command('linux', self.frontend, self.source, self.destination, self.digest)
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            child.wait(timeout=10)
            output = child.stdout.read().decode()
            self.assertEqual(child.returncode, 0, output)
        finally:
            if child.poll() is None: child.kill(); child.wait()
            child.stdin.close(); child.stdout.close()

    def test_corrupt_source_and_native_rejection_leave_nothing_published(self):
        args = self.args(); args.sha256 = '0'*64
        with self.assertRaises(ValueError):
            run(args)
        self.assertFalse(self.destination.exists())
        self.assertEqual(list(self.root.glob('.lighttouch-import-*')), [])
        self.frontend.write_text('#!' + sys.executable + '\nimport sys; sys.exit(7)\n')
        with self.assertRaisesRegex(SetupError, 'exit 7'):
            run(self.args())
        self.assertFalse(self.destination.exists())
        self.assertEqual(list(self.root.glob('.lighttouch-import-*')), [])

    def test_stdin_cancellation_removes_stage_and_preserves_source(self):
        original = self.source.read_bytes()
        # The finalizer cooperates with Linux owned SIGINT, as the real helper.
        self.frontend.write_text('#!' + sys.executable + '\n' + '''import signal, sys, time
signal.signal(signal.SIGINT, lambda *args: sys.exit(2))
print('waiting-to-publish', flush=True)
while True: time.sleep(.01)
''')
        command = import_command('linux', self.frontend, self.source, self.destination, self.digest)
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            while True:
                line = child.stdout.readline()
                self.assertTrue(line, 'importer exited before cancellation')
                if line.strip() == 'waiting-to-publish': break
            output, _ = child.communicate('stop\n', timeout=10)
            self.assertNotEqual(child.returncode, 0, output)
        finally:
            if child.poll() is None: child.kill(); child.wait()
        self.assertFalse(self.destination.exists())
        self.assertEqual(list(self.root.glob('.lighttouch-import-*')), [])
        self.assertEqual(self.source.read_bytes(), original)


if __name__ == '__main__': unittest.main()
