"""Synthetic archives/device API, never app payloads or physical USB operations."""
import ctypes as C
import json
import os
from pathlib import Path
import plistlib
import queue
import socket
import stat
import struct
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_services.catalog import Target
from app_services.ipa import InstallError, inspect, prepare
from app_services.mobile import Mobile, P
from app_services.bridge import Bridge, ServiceJob
from app_services import helper

N72 = Target("iPod2,1", "3.1.3", "armv6")
N81 = Target("iPod4,1", "4.2.1", "armv7")


def executable(subtype=6, encrypted=False):
    commands = struct.pack("<5I", 0x21, 20, 0, 0, 1) if encrypted else b""
    return struct.pack("<7I", 0xfeedface, 12, subtype, 2, bool(commands), len(commands), 0) + commands


def archive(path, *, subtype=6, encrypted=False, minimum="3.0", executable_mode=0o644, extra=None):
    info = {"CFBundleIdentifier": "org.example.synthetic", "CFBundleExecutable": "Test", "MinimumOSVersion": minimum}
    with zipfile.ZipFile(path, "w") as out:
        out.writestr("Payload/Test.app/Info.plist", plistlib.dumps(info))
        member = zipfile.ZipInfo("Payload/Test.app/Test")
        member.create_system = 3
        member.external_attr = (stat.S_IFREG | executable_mode) << 16
        out.writestr(member, executable(subtype, encrypted))
        if extra:
            out.writestr(*extra)


class ArchiveTests(unittest.TestCase):
    def test_private_snapshot_repairs_main_exec_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as directory:
            original, staged = Path(directory) / "source.ipa", Path(directory) / "staged.ipa"
            archive(original, extra=("Payload/Test.app/data.txt", "untouched"))
            before = original.read_bytes()
            result = prepare(original, staged, N72)
            self.assertEqual(original.read_bytes(), before)
            self.assertEqual(result["bundle_id"], "org.example.synthetic")
            self.assertFalse(result["repair"])
            self.assertEqual(staged.stat().st_mode & 0o777, 0o600)
            with zipfile.ZipFile(staged) as ipa:
                self.assertEqual((ipa.getinfo("Payload/Test.app/Test").external_attr >> 16) & 0o777, 0o755)
                self.assertEqual(ipa.read("Payload/Test.app/data.txt"), b"untouched")

    def test_minimum_os_processor_encryption_and_unsafe_zip_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "test.ipa"
            for options, message in (({"minimum": "4.0"}, "requires iOS"),
                                     ({"subtype": 9}, "newer processor"),
                                     ({"encrypted": True}, "encrypted"),
                                     ({"extra": ("../escape", "no")}, "unsafe")):
                archive(source, **options)
                with self.subTest(options=options), self.assertRaisesRegex(InstallError, message):
                    inspect(source, N72)
            archive(source, subtype=9, minimum="4.0")
            self.assertEqual(inspect(source, N81)["bundle_id"], "org.example.synthetic")

    def test_invalid_archive_and_cancel_remove_private_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            source, staged = Path(directory) / "test.ipa", Path(directory) / "out.ipa"
            source.write_bytes(b"not zip")
            with self.assertRaises(InstallError):
                inspect(source)
            archive(source)
            with self.assertRaisesRegex(InstallError, "cancelled"):
                prepare(source, staged, N72, cancelled=lambda: True)
            self.assertFalse(staged.exists())
            link = Path(directory) / "link.ipa"
            link.symlink_to(source)
            with self.assertRaises(InstallError):
                inspect(link)

    def test_zip_aliases_and_escaping_symlinks_cannot_override_guest_files(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "test.ipa"
            archive(source, extra=("Payload//Test.app/Test", "alias"))
            with self.assertRaisesRegex(InstallError, "unsafe"):
                inspect(source, N72)
            for target, allowed in (("../../../outside", False), ("/var/mobile/outside", False), ("data.txt", True)):
                archive(source)
                with zipfile.ZipFile(source, "a") as out:
                    member = zipfile.ZipInfo("Payload/Test.app/link")
                    member.create_system = 3
                    member.external_attr = (stat.S_IFLNK | 0o777) << 16
                    out.writestr(member, target)
                if allowed:
                    inspect(source, N72)
                else:
                    with self.assertRaisesRegex(InstallError, "symbolic link"):
                        inspect(source, N72)


class FakeLib:
    """Scripted public C API; pointer widths and partial writes are exercised."""
    def __init__(self, owner):
        self.owner = owner
        self.events = []
        self.upload = bytearray()
        self.write_limit = 7
        self.installed = True
        self.error = None
        self.zero_write = False
        self.free_bytes = C.create_string_buffer(b"1000000000")

    def afc_get_device_info_key(self, client, key, out):
        C.cast(out, C.POINTER(P))[0] = C.cast(self.free_bytes, P)
        return 0

    def afc_make_directory(self, *args):
        return 16  # Already exists, not a staging failure.

    def afc_file_open(self, client, remote, mode, out):
        self.events.append(("open", remote, mode))
        C.cast(out, C.POINTER(C.c_uint64))[0] = 2**40 + 123
        return 0

    def afc_file_write(self, client, handle, data, size, out):
        assert handle.value == 2**40 + 123
        count = 0 if self.zero_write else min(size, self.write_limit)
        self.upload.extend(data[:count])
        C.cast(out, C.POINTER(C.c_uint32))[0] = count
        return 0

    def afc_file_close(self, client, handle):
        self.events.append(("close", handle.value))
        return 0

    def afc_remove_path(self, client, remote):
        self.events.append(("remove", remote))
        return 0

    def afc_client_free(self, client):
        self.events.append(("free-afc",))
        return 0

    def instproxy_install(self, client, remote, options, callback, context):
        self.events.append(("install", remote))
        self.owner.replies = {1: {"Status": "Installing", "PercentComplete": 50},
                              2: {"Error": self.error} if self.error else {"Status": "Complete"}}
        callback(None, P(1), None)
        callback(None, P(2), None)
        return 0

    def instproxy_browse(self, client, options, out):
        self.events.append(("browse",))
        self.owner.replies[3] = [{"CFBundleIdentifier": "org.example.synthetic", "CFBundleVersion": "1"}] if self.installed else []
        C.cast(out, C.POINTER(P))[0] = P(3)
        return 0

    def instproxy_client_free(self, client):
        self.events.append(("free-instproxy",))
        return 0


class MobileTests(unittest.TestCase):
    def mobile(self):
        value = Mobile.__new__(Mobile)
        value.lib = FakeLib(value)
        value.libc = type("LibC", (), {"free": lambda self, p: None})()
        value.plist = type("Plist", (), {"plist_free": lambda self, p: None})()
        value.service = lambda name, prefix: P(100 if prefix == "afc" else 200)
        value.encode = lambda obj: P(500)
        value.decode = lambda pointer: value.replies[pointer.value if isinstance(pointer, P) else pointer]
        return value

    def test_partial_writes_complete_browse_and_cleanup_order(self):
        value = self.mobile()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.ipa"
            payload = bytes(range(256)) * 500
            path.write_bytes(payload)
            progress = []
            result = value.install(path, "org.example.synthetic", progress.append)
        self.assertEqual(value.lib.upload, payload)
        self.assertTrue(result["installed"])
        events = [event[0] for event in value.lib.events]
        self.assertLess(events.index("close"), events.index("install"))
        self.assertLess(events.index("install"), events.index("browse"))
        self.assertLess(events.index("install"), events.index("free-instproxy"))
        self.assertLess(events.index("free-instproxy"), events.index("browse"))
        self.assertEqual(events.count("free-instproxy"), 2)
        self.assertEqual(events[-2:], ["remove", "free-afc"])
        self.assertEqual(progress[-2]["phase"], "mutation")

    def test_callback_rejection_and_absent_browse_never_pass(self):
        for error, installed, message in (("PackageExtractionFailed", True, "PackageExtractionFailed"),
                                           (None, False, "absent")):
            value = self.mobile()
            value.lib.error, value.lib.installed = error, installed
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "test.ipa"
                path.write_bytes(b"synthetic")
                with self.assertRaisesRegex(InstallError, message):
                    value.install(path, "org.example.synthetic", lambda e: None)
            self.assertEqual([e[0] for e in value.lib.events][-2:], ["remove", "free-afc"])

    def test_invalid_write_stops_before_install_and_removes_staging(self):
        value = self.mobile()
        value.lib.zero_write = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.ipa"
            path.write_bytes(b"synthetic")
            with self.assertRaisesRegex(InstallError, "invalid upload"):
                value.install(path, "org.example.synthetic", lambda e: None)
        self.assertNotIn("install", [event[0] for event in value.lib.events])
        self.assertEqual([e[0] for e in value.lib.events][-3:], ["close", "remove", "free-afc"])

    def test_target_uses_actual_lockdown_and_rejects_unknown_board(self):
        value = self.mobile()
        value.value = lambda key: {"ProductType": "iPod4,1", "ProductVersion": "4.2.1"}[key]
        self.assertEqual(value.target(), N81)
        value.value = lambda key: {"ProductType": "iPhone99,1", "ProductVersion": "4.2.1"}[key]
        with self.assertRaisesRegex(InstallError, "n72/n81"):
            value.target()


class ProcessTests(unittest.TestCase):
    def wait(self, job):
        deadline = time.monotonic() + 3
        while job.active and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(job.active)

    def test_helper_exit_zero_needs_structured_result_and_timeout_is_finite(self):
        for source, success in (("print('not a receipt')", False),
                                ("print('{\"result\":{\"ready\":true}}')", True)):
            job = ServiceJob([sys.executable, "-c", source], timeout=2)
            self.wait(job)
            self.assertEqual(job.successful(), success)
        job = ServiceJob([sys.executable, "-c", "import time; time.sleep(10)"], timeout=.05)
        self.wait(job)
        self.assertFalse(job.successful())
        self.assertIn("unconfirmed", job.error)

    def test_install_owns_stop_gate_before_any_async_event(self):
        job = ServiceJob([sys.executable, "-c", "import time; time.sleep(10)"], owns_install=True)
        try:
            with self.assertRaisesRegex(InstallError, "installing"):
                job.cancel()
            job.cancel(guest_ended=True)
            self.wait(job)
        finally:
            if job.child.poll() is None:
                job.child.kill()
                job.child.wait()

    def test_private_bridge_lifecycle_with_synthetic_unix_listener(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            daemon = root / "synthetic-daemon"
            daemon.write_text("#!" + sys.executable + "\nimport os,socket,sys,time\n"
                              "sockets=[]\nfor path in [sys.argv[sys.argv.index('-S')+1],os.environ['USBMUXD_QEMU_ADDR']]:\n"
                              " s=socket.socket(socket.AF_UNIX); s.bind(path); os.chmod(path,0o600); s.listen(); sockets.append(s)\n"
                              "time.sleep(10)\n")
            daemon.chmod(0o700)
            library = root / "dummy.so"
            library.touch()
            bridge = Bridge(daemon, library, library, root / "pairing", root / "logs")
            try:
                env = bridge.start()
                self.assertEqual(env["IT_USB_TCP"], str(bridge.endpoint))
                self.assertEqual(bridge.directory.stat().st_mode & 0o777, 0o700)
                self.assertTrue(bridge.alive)
                command = bridge.command("info", root / "scratch")
                self.assertIn(str(bridge.socket), command)
                self.assertNotIn("/var/run/usbmuxd", command)
                owned_directory = bridge.directory
            finally:
                bridge.close()
            self.assertFalse(bridge.alive)
            self.assertFalse(owned_directory.exists())
            self.assertTrue((root / "pairing").exists())

    def test_helper_rejects_public_listener_before_loading_libraries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "socket"
            server = socket.socket(socket.AF_UNIX)
            server.bind(str(path))
            path.chmod(0o666)
            try:
                with patch.object(sys, "argv", ["helper", "info", "--socket", str(path), "--library", "missing",
                                                "--plist-library", "missing", "--scratch", directory]), \
                        patch("app_services.helper.Mobile") as mobile, patch("app_services.helper.emit") as emit:
                    self.assertEqual(helper.main(), 1)
                    mobile.assert_not_called()
                    self.assertIn("private", emit.call_args[0][0]["error"])
            finally:
                server.close()


if __name__ == "__main__":
    unittest.main()
