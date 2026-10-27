"""Consumer contract only: real private sockets and a tiny owned host substitute."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_services.ipa import InstallError
from lighttouch_desktop.macos import MacHost, MacSession
from lighttouch_desktop.macos_services import (FORMAT, SESSION_ENV, NativeServicesState,
                                                NativeClientBridge, socket_identity)


class NativeServicesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lt-svc-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.root.chmod(0o700)
        self.path = self.root / "c.sock"
        self.socket = socket.socket(socket.AF_UNIX)
        self.socket.bind(str(self.path)); self.path.chmod(0o600)
        self.addCleanup(self.socket.close)
        self.state = NativeServicesState(); self.state.reset("a" * 32, "b" * 32)

    def receipt(self, generation=1, **extra):
        value = dict(format=FORMAT, version=1, launch_id="a" * 32, profile_id="b" * 32,
                     owner_pid=os.getpid(), generation=generation, state="ready", client_socket="UNIX:" + str(self.path))
        value.update(extra)
        if value["state"] != "ready":
            value.pop("client_socket", None)
        return "SERVICES " + json.dumps(value)

    def test_ready_receipt_binds_owner_generation_profile_and_private_socket(self):
        self.assertTrue(self.state.observe(self.receipt(), os.getpid()))
        self.assertEqual(self.state.current_endpoint(), self.path)
        self.assertFalse(self.state.observe(self.receipt(), os.getpid()))
        self.assertIsNone(self.state.current_endpoint())
        self.assertTrue(self.state.observe(self.receipt(2), os.getpid()))
        self.assertTrue(self.state.observe(self.receipt(3, state="retired"), os.getpid()))
        self.assertIsNone(self.state.current_endpoint())

    def test_malformed_or_mismatched_receipt_invalidates_previous_endpoint(self):
        invalid = ["SERVICES []", "SERVICES {", "SERVICES " + "x" * 8192,
                   self.receipt(owner_pid=True), self.receipt(owner_pid=os.getpid() + 1),
                   self.receipt(launch_id="old"), self.receipt(profile_id="c" * 32),
                   self.receipt(version=True), self.receipt(generation=True),
                   self.receipt(state="unknown"), self.receipt(extra="unexpected"),
                   self.receipt().replace('"version": 1', '"version": 1, "version": 1')]
        for line in invalid:
            with self.subTest(line=line[:120]):
                self.state.reset("a" * 32, "b" * 32)
                self.assertTrue(self.state.observe(self.receipt(), os.getpid()))
                self.assertFalse(self.state.observe(line, os.getpid()))
                self.assertIsNone(self.state.current_endpoint())
                self.assertTrue(self.state.error)

    def test_socket_admission_rejects_network_regular_symlink_and_public_paths(self):
        regular = self.root / "file"; regular.write_text("not a socket")
        link = self.root / "link"; link.symlink_to(self.path)
        for value in ("TCP:127.0.0.1:1", "UNIX:relative", "UNIX:" + str(regular), "UNIX:" + str(link),
                      "UNIX:" + str(self.root) + "/../" + self.root.name + "/c.sock"):
            with self.subTest(value=value), self.assertRaises((ValueError, OSError)):
                socket_identity(value)
        self.path.chmod(0o666)
        with self.assertRaises(ValueError): socket_identity("UNIX:" + str(self.path))
        self.path.chmod(0o600); self.root.chmod(0o755)
        with self.assertRaises(ValueError): socket_identity("UNIX:" + str(self.path))

    def test_socket_replacement_invalidates_without_unlinking_new_socket(self):
        self.assertTrue(self.state.observe(self.receipt(), os.getpid()))
        self.path.unlink()
        replacement = socket.socket(socket.AF_UNIX); self.addCleanup(replacement.close)
        replacement.bind(str(self.path)); self.path.chmod(0o600)
        self.assertIsNone(self.state.current_endpoint())
        self.assertTrue(self.path.exists())

    def bridge(self):
        library = self.root / "mobile.dylib"; library.write_bytes(b"not loaded")
        plist = self.root / "plist.dylib"; plist.write_bytes(b"not loaded")
        child = types.SimpleNamespace(pid=os.getpid(), poll=lambda: None)
        session = types.SimpleNamespace(services=self.state, child=child, stopping=False)
        return NativeClientBridge(session, "b" * 32, library, plist)

    def test_client_reuses_existing_finite_helper_and_never_owns_daemon(self):
        bridge = self.bridge()
        self.assertFalse(bridge.alive)
        with self.assertRaises(InstallError): bridge.command("info", self.root)
        self.assertTrue(self.state.observe(self.receipt(), os.getpid()))
        with mock.patch.object(subprocess, "Popen") as spawn:
            command = bridge.command("install", self.root, ipa=self.root / "a.ipa")
            self.assertEqual(command[:4], [sys.executable, "-m", "app_services.helper", "install"])
            self.assertEqual(command[command.index("--socket") + 1], str(self.path))
            bridge.close(); spawn.assert_not_called()
        self.assertFalse(bridge.alive); self.assertTrue(self.path.exists())
        self.assertEqual(self.state.current_endpoint(), self.path)

    def test_client_refuses_stopping_exited_or_other_profile_owner(self):
        for change in ("stop", "exit", "profile", "owner"):
            with self.subTest(change=change):
                self.state.reset("a" * 32, "b" * 32); self.state.observe(self.receipt(), os.getpid())
                bridge = self.bridge()
                if change == "stop": bridge.session.stopping = True
                if change == "exit": bridge.session.child.poll = lambda: 0
                if change == "profile": bridge.profile_id = "c" * 32
                if change == "owner": self.state.owner_pid += 1
                self.assertFalse(bridge.alive)
                with self.assertRaises(InstallError): bridge.command("info", self.root)

    def test_mac_session_real_child_receipt_is_bound_and_invalidated_before_stop(self):
        worker = self.root / "host.py"
        worker.write_text("""import json,os,sys
assert sys.argv[1] == 'run'
value=dict(format='lighttouch-native-services',version=1,launch_id=os.environ['LIGHTTOUCH_SHARED_SERVICE_SESSION'],profile_id=sys.argv[3],owner_pid=os.getpid(),generation=1,state='ready',client_socket=os.environ['TEST_SOCKET'])
print('SERVICES '+json.dumps(value),flush=True)
assert sys.stdin.readline()=='stop\\n'
print('FINAL status_available=1 state=0 confirmed=1 dirty=0 worker_reaped=1 worker_exit=0 worker_disposal_requested=0 frontend_return=0',flush=True)
""")
        # An executable shim keeps native argv shape without shell execution.
        worker.write_text("#!" + sys.executable + "\n" + worker.read_text()); worker.chmod(0o700)
        session = MacSession()
        session.start([str(worker), "run", "--id", "b" * 32], self.root / "host.log",
                      dict(os.environ, TEST_SOCKET="UNIX:" + str(self.path), **{SESSION_ENV: "stale"}))
        try:
            deadline = time.monotonic() + 3
            while session.services.endpoint is None and time.monotonic() < deadline:
                session.poll(); time.sleep(.005)
            self.assertEqual(session.services.current_endpoint(), self.path)
            self.assertEqual(session.services.owner_pid, session.child.pid)
            self.assertNotEqual(session.services.launch_id, "stale")
            session.stop(); self.assertIsNone(session.services.current_endpoint())
            deadline = time.monotonic() + 3
            while session.active and time.monotonic() < deadline:
                session.poll(); time.sleep(.005)
            session.poll(); self.assertFalse(session.active)
            self.assertEqual(session.returncode, 0)
            self.assertIsNone(session.services.current_endpoint())
        finally:
            if session.child.poll() is None: session.child.kill(); session.child.wait(timeout=1)

    def test_invalid_native_receipt_is_durable_and_duplicate_error_is_bounded(self):
        worker = self.root / "invalid-owner.py"
        worker.write_text("#!" + sys.executable + "\n" + """import sys
print('SERVICES []', flush=True)
print('SERVICES []', flush=True)
assert sys.stdin.readline()=='stop\\n'
""")
        worker.chmod(0o700)
        session = MacSession(self.root / "application.log")
        session.start([str(worker), "run", "--id", "b" * 32], self.root / "invalid-native.log")
        try:
            deadline = time.monotonic() + 3
            while len(session.tail) < 2 and time.monotonic() < deadline:
                session.poll(); time.sleep(.005)
            self.assertEqual(list(session.tail), ["SERVICES []", "SERVICES []"])
            self.assertIsNone(session.services.endpoint)
            self.assertIsNone(session.child.poll(), "optional service error must not stop the owner")
            log = (self.root / "application.log").read_text()
            self.assertEqual(log.count("APPLICATION_ERROR"), 1)
            self.assertIn("native app services", log)
            self.assertIn("receipt must be an object", log)
            self.assertIn("receipt must be an object", session.diagnostic_report())
            session.stop()
            while session.active and time.monotonic() < deadline:
                session.poll(); time.sleep(.005)
            session.poll(); self.assertFalse(session.active)
            self.assertIsNone(session.final)
        finally:
            if session.child.poll() is None: session.child.kill(); session.child.wait(timeout=1)

    def test_default_mac_apps_stays_unavailable_without_native_publisher(self):
        self.assertEqual(MacHost.start_services(None, self.root, "b" * 32), (None, None))
        with self.assertRaises(ValueError): MacHost.apps_window()

    def test_native_revocation_disables_bridge_while_owner_stays_alive(self):
        """Native teardown receipts must revoke clients before process exit.

        This real pipe/child/socket substitute checks the receiver, not the
        uncompiled Swift producer or actual guest protocol health.
        """
        worker = self.root / "owner.py"
        worker.write_text("#!" + sys.executable + "\n" + """import json,os,sys
value=dict(format='lighttouch-native-services',version=1,launch_id=os.environ['LIGHTTOUCH_SHARED_SERVICE_SESSION'],profile_id=sys.argv[3],owner_pid=os.getpid(),generation=1,state='ready',client_socket=os.environ['TEST_SOCKET'])
print('SERVICES '+json.dumps(value),flush=True)
for generation,state in ((2,'unavailable'),(3,'retired')):
    assert sys.stdin.readline()==state+'\\n'
    value.pop('client_socket',None)
    value.update(generation=generation,state=state)
    print('SERVICES '+json.dumps(value),flush=True)
assert sys.stdin.readline()=='stop\\n'
""")
        worker.chmod(0o700)
        session = MacSession()
        session.start([str(worker), "run", "--id", "b" * 32], self.root / "revocation.log",
                      dict(os.environ, TEST_SOCKET="UNIX:" + str(self.path)))
        library = self.root / "mobile.dylib"; library.write_bytes(b"not loaded")
        plist = self.root / "plist.dylib"; plist.write_bytes(b"not loaded")
        bridge = NativeClientBridge(session, "b" * 32, library, plist)

        def observed(generation):
            deadline = time.monotonic() + 3
            while session.services.generation < generation and time.monotonic() < deadline:
                session.poll(); time.sleep(.005)
            self.assertEqual(session.services.generation, generation)

        try:
            observed(1)
            self.assertTrue(bridge.alive)
            for generation, state in ((2, "unavailable"), (3, "retired")):
                session.child.stdin.write((state + "\n").encode()); session.child.stdin.flush()
                observed(generation)
                self.assertFalse(bridge.alive)
                with self.assertRaises(InstallError): bridge.command("info", self.root)
                self.assertIsNone(session.child.poll(), "native owner remains alive during cleanup")
                self.assertTrue(self.path.exists(), "receiver must never unlink the publisher's socket")
            session.stop()
            deadline = time.monotonic() + 3
            while session.active and time.monotonic() < deadline:
                session.poll(); time.sleep(.005)
            session.poll()
            self.assertFalse(session.active)
            self.assertEqual(session.returncode, 0)
            self.assertIsNone(session.final, "owner exit alone is never a clean guest shutdown")
        finally:
            if session.child.poll() is None: session.child.kill(); session.child.wait(timeout=1)


if __name__ == "__main__": unittest.main()
