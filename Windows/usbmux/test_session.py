"""Small host contract tests; no Windows process, socket or guest is launched."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("lt_windows_daemon_session", HERE / "session.py")
session = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = session
spec.loader.exec_module(session)


class Receipts(unittest.TestCase):
    def setUp(self):
        self.alive = (123, "133795493571234567")
        self.options = dict(profile="a" * 32, token="b" * 32, generation=9, previous_generation=8,
                            pid=self.alive[0], created=self.alive[1], live_identity=lambda: self.alive)
        self.fields = dict(format="lighttouch-windows-usbmux", version=1, pid=self.alive[0],
                           created=self.alive[1], client_port=51001, guest_port=51002,
                           boundary="windows-owner-v1", profile="a" * 32, token="b" * 32, generation=9)

    def line(self, **changes):
        return "DAEMON " + json.dumps(dict(self.fields, **changes))

    def ready(self):
        daemon = session.DaemonSession(**self.options)
        daemon.accept_ready(self.line())
        return daemon

    def test_exact_configuration_and_no_default(self):
        daemon = session.DaemonSession(**self.options)
        with self.assertRaises(session.ServiceError):
            daemon.environment()
        daemon = self.ready()
        self.assertEqual(daemon.environment(), {"USBMUXD_SOCKET_ADDRESS": "127.0.0.1:51001",
                         "LTW_USBMUX_PID": "123", "LTW_USBMUX_CREATED": self.alive[1],
                         "IT_USB_TCP": "127.0.0.1:51002"})

    def test_stale_or_wrong_identity_and_boundary(self):
        for key, values in {"pid": [124, True], "created": ["133795493571234568", 133795493571234567],
                            "profile": ["c" * 32], "token": ["d" * 32], "generation": [8, True, 9.0],
                            "version": [2, True], "boundary": ["tcp", "windows-owner-v2"]}.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    daemon = session.DaemonSession(**self.options)
                    with self.assertRaises(session.ServiceError):
                        daemon.accept_ready(self.line(**{key: value}))
                    with self.assertRaises(session.ServiceError):
                        daemon.environment()

    def test_invalid_ports(self):
        for value in [0, -1, 65536, True, "51001", 51001.0, None]:
            for key in ["client_port", "guest_port"]:
                with self.subTest(value=value, key=key):
                    with self.assertRaises(session.ServiceError):
                        session.DaemonSession(**self.options).accept_ready(self.line(**{key: value}))
        with self.assertRaises(session.ServiceError):
            session.DaemonSession(**self.options).accept_ready(self.line(guest_port=51001))

    def test_malformed_duplicate_unknown_and_missing_fields(self):
        missing = dict(self.fields); del missing["token"]
        cases = ["", "DAEMON []", "DAEMON null", "DAEMON {", "DAEMON " + " " * 4096,
                 "DAEMON " + json.dumps(missing), self.line(extra="unexpected"),
                 self.line()[:-1] + ', "pid": 123}', self.line() + "\nDAEMON {}"]
        for line in cases:
            with self.subTest(line=line[:60]):
                with self.assertRaises(session.ServiceError):
                    session.DaemonSession(**self.options).accept_ready(line)

    def test_second_ready_invalidates_first(self):
        daemon = self.ready()
        with self.assertRaises(session.ServiceError):
            daemon.accept_ready(self.line())
        with self.assertRaises(session.ServiceError):
            daemon.environment()

    def test_reused_pid_or_exited_child_invalidates_endpoint(self):
        for alive in [None, (123, "133795493571234568"), (124, self.options["created"])]:
            with self.subTest(alive=alive):
                self.alive = (123, self.options["created"])
                daemon = self.ready()
                self.alive = alive
                with self.assertRaises(session.ServiceError):
                    daemon.environment()
                self.alive = (123, self.options["created"])
                with self.assertRaises(session.ServiceError):
                    daemon.environment()  # Cannot revive an invalidated launch.

    def test_identity_must_survive_both_readiness_checks(self):
        values = iter([self.alive, None])
        self.options["live_identity"] = lambda: next(values)
        with self.assertRaises(session.ServiceError):
            session.DaemonSession(**self.options).accept_ready(self.line())

    def test_stop_invalidates_before_exit_and_requires_owned_reap(self):
        daemon = self.ready()
        daemon.request_stop()
        with self.assertRaises(session.ServiceError):
            daemon.environment()
        self.alive = None
        self.assertTrue(daemon.finish(exit_code=0, reaped=True, output_closed=True,
                        retirement_line="DAEMON_RETIRED listeners_closed=1 control_closed=1"))

    def test_retirement_line_alone_never_qualifies(self):
        proof = dict(exit_code=0, reaped=True, output_closed=True,
                     retirement_line="DAEMON_RETIRED listeners_closed=1 control_closed=1")
        for changed in [dict(exit_code=2), dict(exit_code=True), dict(reaped=False),
                        dict(output_closed=False), dict(retirement_line="")]:
            self.alive = (123, self.options["created"])
            daemon = self.ready(); daemon.request_stop(); self.alive = None
            self.assertFalse(daemon.finish(**dict(proof, **changed)))
        self.alive = (123, self.options["created"])
        daemon = self.ready(); daemon.request_stop()
        self.assertFalse(daemon.finish(**proof))  # Still live.

    def test_failed_start_cannot_be_relabelled_clean(self):
        daemon = session.DaemonSession(**self.options)
        with self.assertRaises(session.ServiceError):
            daemon.accept_ready(self.line(token="e" * 32))
        daemon.request_stop(); self.alive = None
        self.assertFalse(daemon.finish(exit_code=0, reaped=True, output_closed=True,
                         retirement_line="DAEMON_RETIRED listeners_closed=1 control_closed=1"))

    def test_saved_devices_and_generations_remain_isolated(self):
        first = self.ready()
        options = dict(self.options, profile="c" * 32, token="d" * 32, generation=10, previous_generation=9)
        second = session.DaemonSession(**options)
        with self.assertRaises(session.ServiceError):
            second.accept_ready(self.line())
        self.assertEqual(first.environment()["IT_USB_TCP"], "127.0.0.1:51002")
        for change in [dict(generation=8), dict(generation=True), dict(previous_generation=9),
                       dict(created="01"), dict(created=str(2**64)), dict(pid=0),
                       dict(token="not hex"), dict(profile="../other")]:
            with self.subTest(change=change):
                with self.assertRaises(session.ServiceError):
                    session.DaemonSession(**dict(self.options, **change))


class SourcePatch(unittest.TestCase):
    def test_exact_pinned_source_patch_applies(self):
        """Check real dependency preimages, never mutate supplied source trees."""
        daemon = os.environ.get("LTW_TEST_DAEMON_SOURCE")
        library = os.environ.get("LTW_TEST_LIBUSBMUXD_SOURCE")
        if not daemon or not library:
            self.skipTest("Provide pinned daemon and libusbmuxd trees for source application check.")
        pins = json.loads((HERE / "SOURCES.json").read_text())
        for section, source, patch in [("daemon", Path(daemon), "windows-host.patch"),
                                       ("libusbmuxd", Path(library), "libusbmuxd-owned.patch")]:
            with tempfile.TemporaryDirectory(prefix="lt-windows-patch-") as scratch:
                stage = Path(scratch)
                for name, digest in pins[section]["preimage_sha256"].items():
                    path = source / name
                    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest, name)
                    target = stage / name; target.parent.mkdir(exist_ok=True, parents=True)
                    shutil.copyfile(path, target)
                # Never permit offset/fuzzy application or successful no-op.
                run = subprocess.run(["patch", "--batch", "--fuzz=0", "-p1", "-i", str(HERE / patch)],
                                     cwd=stage, capture_output=True, text=True, timeout=5)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                self.assertNotIn("offset", run.stdout)
                self.assertNotIn("fuzz", run.stdout)


if __name__ == "__main__":
    unittest.main()
