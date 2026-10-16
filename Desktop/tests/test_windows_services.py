"""Owned Windows bridge wiring with a tiny synthetic process, not a guest."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lighttouch_desktop.windows_services import WindowsBridge, ServiceError
from lighttouch_desktop.windows import WindowsSession, WindowsHost
from lighttouch_desktop.storage import StorageOps
from app_services.bridge import ServiceJob


class ServicesTests(unittest.TestCase):
    def test_apps_enabled_launch_preserves_windows_runtime_environment(self):
        host = WindowsHost(storage=StorageOps())
        session = host.create_session(None)
        owned = {'IT_USB_TCP':'127.0.0.1:50102', 'LTW_USBMUX_PID':'123', 'LTW_USBMUX_CREATED':'456'}
        bridge = SimpleNamespace(start=lambda: owned, service_environment=lambda: owned)
        system = {'SystemRoot':r'C:\Windows', 'PATH':r'C:\Windows\System32',
                  'TEMP':r'C:\Users\Blake\Temp', 'USERPROFILE':r'C:\Users\Blake',
                  'USBMUXD_SOCKET_ADDRESS':'foreign'}
        with patch.dict(os.environ, system, clear=True), \
             patch('lighttouch_desktop.windows_services.WindowsBridge', return_value=bridge), \
             patch('lighttouch_desktop.windows.ManagedSession.start') as launch:
            _, environment = host.start_services(SimpleNamespace(services_runtime='runtime'), 'data', 'a'*32)
            session.start(['host.exe', 'run'], 'guest.log', environment)
            command, _, inherited = launch.call_args.args
            for key in ('SystemRoot', 'PATH', 'TEMP', 'USERPROFILE'):
                self.assertEqual(inherited[key], system[key])
            self.assertNotIn('USBMUXD_SOCKET_ADDRESS', inherited)
            self.assertNotIn('IT_USB_TCP', inherited)
            self.assertEqual(command[command.index('--usb-endpoint')+1], owned['IT_USB_TCP'])

    def test_real_child_readiness_commands_and_cooperative_retirement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); runtime = root / 'runtime'; runtime.mkdir()
            for name in ('lighttouch-usbmuxd.exe', 'libimobiledevice-1.0.dll', 'libplist-2.0.dll'):
                (runtime / name).write_bytes(b'host test fixture')
            bridge = WindowsBridge(runtime, root / 'data', 'a' * 32, StorageOps())
            real_popen = __import__('subprocess').Popen
            def start(command, **kwargs):
                code = """import os,sys,json
print('DAEMON '+json.dumps(dict(format='lighttouch-windows-usbmux',version=1,pid=os.getpid(),created='12345',client_port=50101,guest_port=50102,boundary='windows-owner-v1',profile=os.environ['LTW_SESSION_PROFILE'],token=os.environ['LTW_SESSION_TOKEN'],generation=1)),flush=True)
assert sys.stdin.readline()=='stop\\n'
print('DAEMON_RETIRED listeners_closed=1 control_closed=1',flush=True)
"""
                return real_popen([sys.executable, '-c', code], **kwargs)
            def identity(child):
                return (child.pid, '12345') if child.poll() is None else None
            with patch('lighttouch_desktop.windows_services.subprocess.Popen', side_effect=start), \
                 patch('lighttouch_desktop.windows_services.child_identity', side_effect=identity):
                try:
                    env = bridge.start()
                    self.assertTrue(bridge.alive)
                    self.assertEqual(env['IT_USB_TCP'], '127.0.0.1:50102')
                    command = bridge.command('install', root / 'scratch', root / 'app.ipa')
                    self.assertEqual(command[command.index('--socket') + 1], '127.0.0.1:50101')
                    self.assertEqual(command[command.index('--daemon-pid') + 1], str(bridge.child.pid))
                    self.assertEqual(command[command.index('--ipa') + 1], str(root / 'app.ipa'))
                    child = bridge.child
                    bridge.close()
                    self.assertEqual(child.returncode, 0)
                    self.assertTrue(bridge.output_done.is_set())
                    self.assertEqual(bridge.session._state, 'retired')
                    self.assertFalse(bridge.alive)
                    with self.assertRaises(ValueError): bridge.command('info', root / 'scratch')
                finally:
                    bridge.close()

    def test_session_uses_owned_bridge_arguments_not_ambient_endpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = WindowsSession(storage=StorageOps())
            session.bridge = SimpleNamespace(service_environment=lambda: {
                'IT_USB_TCP':'127.0.0.1:50102', 'LTW_USBMUX_PID':'123', 'LTW_USBMUX_CREATED':'456'})
            with patch('lighttouch_desktop.windows.ManagedSession.start') as launch:
                session.start(['host.exe', 'run'], root/'guest.log', {'IT_USB_TCP':'foreign','KEEP':'yes'})
                argv, _, env = launch.call_args.args
                self.assertIn('--usb-endpoint', argv)
                self.assertEqual(argv[argv.index('--usb-endpoint')+1], '127.0.0.1:50102')
                self.assertEqual(env, {'KEEP':'yes'})
                # An import after a closed guest bridge must still be possible.
                session.bridge.service_environment = Mock(side_effect=AssertionError('closed guest'))
                session.start(['host.exe', 'import'], root/'import.log')
                self.assertEqual(launch.call_args.args[0], ['host.exe', 'import'])

    def test_finite_service_helper_receives_exact_owned_environment_and_result(self):
        with tempfile.TemporaryDirectory() as directory:
            code = "import os,json; print(json.dumps({'result':{'endpoint':os.environ['USBMUXD_SOCKET_ADDRESS']}}),flush=True)"
            job = ServiceJob([sys.executable, '-c', code], timeout=3,
                             env={'USBMUXD_SOCKET_ADDRESS':'127.0.0.1:50101'}, log_path=Path(directory)/'job.log')
            try:
                deadline = time.monotonic()+3
                while job.active and time.monotonic()<deadline:
                    time.sleep(.005)
                self.assertTrue(job.successful())
                self.assertEqual(job.result['endpoint'], '127.0.0.1:50101')
            finally:
                job.cancel(guest_ended=True)
