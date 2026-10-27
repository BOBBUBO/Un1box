"""Shared host wiring contracts, without claiming Win32 target execution."""
from pathlib import Path
from types import SimpleNamespace
import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from lighttouch_desktop import windows
from lighttouch_desktop.storage import StorageOps
from lighttouch_desktop.session import ManagedSession


class HostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.host = windows.WindowsHost(storage=StorageOps())
        self.args = SimpleNamespace(frontend=self.root/'helper.exe', worker=self.root/'window.exe')
        for path in [self.args.frontend, self.args.worker]: path.write_bytes(b'synthetic host fixture')
        self.profile = {'id': 'a'*32, 'name': 'My iPod', 'base': str(self.root/'base'),
                        'state': str(self.root/'state'), 'guest_package': str(self.root/'tools.itpack'),
                        'bootrom': str(self.root/'rom.bin'), 'audio': True}

    def test_same_managed_session_profile_and_archive_algorithms(self):
        self.assertIsInstance(self.host.create_session(self.root/'application.log'), ManagedSession)
        self.assertIsInstance(self.host.open_profiles(self.root/'library'), windows.Profiles)
        with patch.object(windows, 'Preparation') as prepare:
            self.host.prepare('selected.itbase')
            prepare.assert_called_once_with('selected.itbase', storage=self.host.storage)

    def test_run_dispatches_literal_headless_options_without_qemu_recipe(self):
        with patch.object(self.host, 'validate') as check:
            argv = self.host.guest_command(self.args, self.profile, True)
        check.assert_called_once_with(self.profile, allow_dirty=True)
        self.assertEqual(argv[:2], [str(self.args.frontend), 'run'])
        self.assertEqual(argv[argv.index('--worker')+1], str(self.args.worker))
        self.assertEqual(argv[argv.index('--recover')+1], 'yes')
        self.assertEqual(argv[argv.index('--name')+1], 'My iPod')
        self.assertFalse(any(value in argv for value in ['-M', '-display', '-audio', '-qmp']))

    def test_multiple_devices_retain_separate_state_and_identity(self):
        second = dict(self.profile, id='b'*32, state=str(self.root/'other-state'))
        with patch.object(self.host, 'validate'):
            one = self.host.guest_command(self.args, self.profile, False)
            two = self.host.guest_command(self.args, second, False)
        self.assertNotEqual(one[one.index('--id')+1], two[two.index('--id')+1])
        self.assertNotEqual(one[one.index('--state')+1], two[two.index('--state')+1])

    def test_bad_identity_and_missing_worker_fail_before_start(self):
        with patch.object(self.host, 'validate'):
            with self.assertRaises(windows.SetupError): self.host.guest_command(self.args, dict(self.profile, id='../other'), False)
            self.args.worker.unlink()
            with self.assertRaises(windows.SetupError): self.host.guest_command(self.args, self.profile, False)

    def test_import_uses_common_streamer_with_native_finalizer(self):
        source = self.root/'selected.itbase'; source.write_bytes(b'synthetic')
        destination = self.root/'new base'
        argv = self.host.import_command(self.args, str(source), destination, 'A'*64)
        self.assertEqual(argv[:2], [sys.executable, '-I'])
        self.assertEqual(argv[argv.index('--platform')+1], 'windows')
        self.assertEqual(argv[argv.index('--frontend')+1], str(self.args.frontend))
        self.assertEqual(argv[-2:], ['--sha256', 'a'*64])
        with self.assertRaises(windows.SetupError): self.host.import_command(self.args, str(source), destination, '')
        destination.mkdir()
        with self.assertRaises(windows.SetupError): self.host.import_command(self.args, str(source), destination, 'a'*64)

    def test_import_and_start_share_validation_results_and_allow_explicit_recheck(self):
        self.args.data_dir = self.root / "data"
        source = self.root / "system.itbase"; source.write_bytes(b"fixture")
        argv = self.host.import_command(self.args, str(source), self.root / "new", 'a' * 64)
        expected = str(self.args.data_dir / "system-validation")
        self.assertEqual(argv[argv.index('--validation-cache') + 1], expected)
        with patch.object(self.host, 'validate'):
            argv = self.host.guest_command(self.args, self.profile, False, recheck=True)
        self.assertEqual(argv[argv.index('--validation-cache') + 1], expected)
        self.assertEqual(argv[argv.index('--recheck') + 1], 'yes')

    def test_unconfirmed_preview_marker_requires_explicit_recovery(self):
        state = Path(self.profile['state']); state.mkdir(); (state/'shutdown-unconfirmed.json').write_text('{}')
        with patch.object(windows, 'validate', return_value={'board':'n72ap'}):
            with self.assertRaises(windows.SetupError): self.host.validate(self.profile)
            self.assertEqual(self.host.validate(self.profile, allow_dirty=True)['board'], 'n72ap')

    def test_unqualified_services_never_inherit_a_foreign_bridge(self):
        foreign = {"IT_USB_TCP": "127.0.0.1:55123", "usbmuxd_socket_address": "127.0.0.1:27015",
                   "USBMUXD_QEMU_ADDR": "127.0.0.1:55124", "ltw_usbmux_pid": "123",
                   "LTW_USBMUX_CREATED": "133795493571234567", "LtW_Session_Profile": "a"*32,
                   "LTW_SESSION_TOKEN": "b"*32, "LTW_SESSION_GENERATION": "1",
                   "LIGHTTOUCH_TEST_KEEP": "ordinary host setting"}
        # Execute a real tiny host child through the actual session owner. No
        # Windows APIs, daemon, network endpoint or guest is allocated here.
        code = "import os,json; print('ENV ' + json.dumps(dict(os.environ)), flush=True)"
        for explicit in (False, True):
            with self.subTest(explicit=explicit), patch.dict(os.environ, foreign):
                environment = dict(os.environ) if explicit else None
                before = dict(environment) if explicit else dict(os.environ)
                owner = self.host.create_session(self.root/'application.log')
                self.assertIsInstance(owner, windows.WindowsSession)
                owner.start([sys.executable, '-c', code], self.root/('environment-'+str(explicit)+'.log'), environment)
                deadline = time.monotonic() + 3
                lines = []
                try:
                    while owner.active and time.monotonic() < deadline:
                        lines.extend(owner.poll()); time.sleep(.005)
                    lines.extend(owner.poll())
                    self.assertFalse(owner.active)
                    self.assertEqual(owner.returncode, 0)
                    result = json.loads(next(line[4:] for line in lines if line.startswith('ENV ')))
                    self.assertEqual(result['LIGHTTOUCH_TEST_KEEP'], foreign['LIGHTTOUCH_TEST_KEEP'])
                    for key in foreign.keys() - {'LIGHTTOUCH_TEST_KEEP'}:
                        self.assertNotIn(key, result)
                    self.assertEqual(dict(environment) if explicit else dict(os.environ), before)
                    self.assertEqual(self.host.start_services(self.args, self.root, self.profile['id']), (None, None))
                finally:
                    if owner.child.poll() is None:
                        owner.child.kill(); owner.child.wait(timeout=1)


if __name__ == '__main__': unittest.main()
