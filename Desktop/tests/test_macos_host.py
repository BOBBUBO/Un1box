"""Actual shared Mac adapter with synthetic files and real tiny subprocesses."""
import json
import os
from pathlib import Path
import plistlib
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lighttouch_desktop.macos import MacHost, MacSession, managed_record, clean_exit
from lighttouch_desktop.profiles import Profiles, SetupError


class HostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / "read-only base"
        self.base.mkdir()
        (self.base / "device.lock.json").write_text(json.dumps({"format": 1, "board": "n81ap", "build": "8C148", "boot_strategy": "kboot"}))
        self.base.chmod(0o555)
        self.addCleanup(self.base.chmod, 0o755)
        self.pack = self.root / "matching.itpack"; self.pack.write_bytes(b"synthetic package; native validation still required")
        self.runner = self.root / "native host ; literal"; self.runner.write_text("synthetic executable"); self.runner.chmod(0o700)
        self.args = SimpleNamespace(frontend=str(self.runner), engine=str(self.runner), helper=str(self.runner), firmwarekit=str(self.runner))
        self.profile = {"id": "a" * 32, "name": "A ; $(literal)", "base": str(self.base), "state": str(self.root / "instance-state"),
                        "guest_package": str(self.pack), "bootrom": "", "audio": True}

    def test_saved_configurations_share_base_and_map_to_distinct_native_owners_without_writes(self):
        store = Profiles(self.root / "new shared library")
        other = dict(self.profile, id="b" * 32, name="Second", state=str(self.root / "another-state"))
        store.save(self.profile); store.save(other)
        first, second = managed_record(self.profile), managed_record(other)
        self.assertNotEqual(first["native_id"], second["native_id"])
        self.assertNotEqual(first["record"], second["record"])
        self.assertFalse(Path(first["native_state"]).exists())
        self.assertFalse(Path(second["native_state"]).exists())
        reopened = Profiles(store.directory)
        self.assertEqual(len(reopened.devices), 2)
        self.assertEqual(reopened.devices[0]["base"], reopened.devices[1]["base"])

    def test_native_record_is_read_only_and_foreign_native_or_linux_state_is_rejected(self):
        mapping = managed_record(self.profile)
        record = Path(mapping["record"]); record.parent.mkdir(parents=True)
        value = {"format": 1, "id": mapping["native_id"], "board": "n81ap", "base": {"path": str(self.base)}}
        record.write_bytes(plistlib.dumps(value))
        original = record.read_bytes()
        self.assertEqual(managed_record(self.profile)["native_id"], mapping["native_id"])
        self.assertEqual(record.read_bytes(), original)
        value["id"] = "foreign owner"; record.write_bytes(plistlib.dumps(value))
        with self.assertRaisesRegex(SetupError, "another native device"):
            managed_record(self.profile)
        self.assertEqual(plistlib.loads(record.read_bytes())["id"], "foreign owner")

    def test_nonempty_unmanaged_and_symlink_state_are_never_adopted(self):
        state = Path(self.profile["state"]); state.mkdir(); marker = state / "device.json"; marker.write_text("native reference")
        with self.assertRaisesRegex(SetupError, "never converted"):
            managed_record(self.profile)
        self.assertEqual(marker.read_text(), "native reference")
        alias = self.root / "alias"; alias.symlink_to(state, target_is_directory=True)
        with self.assertRaises(ValueError):
            managed_record(dict(self.profile, state=str(alias)))

    def test_literal_command_has_managed_admission_inputs_and_no_arbitrary_qemu_configuration(self):
        command = MacHost.guest_command(self.args, self.profile, False)
        self.assertEqual(command[:2], [str(self.runner), "run"])
        self.assertEqual(command[command.index("--name") + 1], self.profile["name"])
        self.assertIn("--firmwarekit", command)
        self.assertIn("--helper", command)
        self.assertNotIn("--rom", command)
        self.assertNotIn("-M", command)
        self.assertNotIn("--recover", command)
        self.assertEqual(list(self.base.iterdir()), [self.base / "device.lock.json"])

    def test_profiles_preserve_original_format_and_reject_engine_injection(self):
        output = self.root / "config.json"; MacHost.export_profile(output, self.profile)
        loaded = MacHost.load_profile(output)
        self.assertEqual(loaded["base"], self.profile["base"])
        self.assertEqual(loaded["state"], self.profile["state"])
        value = json.loads(output.read_text()); value["engine"] = "/untrusted/engine"
        output.write_text(json.dumps(value))
        with self.assertRaisesRegex(SetupError, "Unsupported profile fields"):
            MacHost.load_profile(output)


class SessionTests(unittest.TestCase):
    def wait(self, session):
        deadline = time.monotonic() + 5
        while session.active and time.monotonic() < deadline:
            session.poll(); time.sleep(.01)
        self.assertFalse(session.active, "synthetic native host did not retire")
        session.poll()

    def test_actual_fresh_process_pipe_stop_and_full_terminal_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); session = MacSession(root / "application.log")
            source = """import os,sys
print('status state=2', flush=True)
assert 'LT_TCG_IR_CACHE_ENABLE' not in os.environ
assert 'LT_TCG_IR_CACHE' not in os.environ
assert sys.stdin.readline() == 'stop\\n'
print('FINAL status_available=1 state=0 confirmed=1 dirty=0 worker_reaped=1 worker_exit=0 worker_disposal_requested=0 frontend_return=0', flush=True)
"""
            environment = dict(os.environ, LT_TCG_IR_CACHE_ENABLE="unqualified", LT_TCG_IR_CACHE="/untrusted")
            session.start([sys.executable, "-c", source], root / "first.log", env=environment)
            first = session.child
            deadline = time.monotonic() + 3
            while session.status.get("state") != "2" and time.monotonic() < deadline:
                session.poll(); time.sleep(.01)
            self.assertEqual(session.status.get("state"), "2")
            with self.assertRaisesRegex(RuntimeError, "Stop the current"):
                session.start([sys.executable, "-c", "pass"], root / "must-not-exist.log")
            session.stop(); self.wait(session)
            self.assertTrue(clean_exit(session.returncode, session.final))
            session.start([sys.executable, "-c", "print('status state=1')"], root / "second.log")
            self.assertIsNot(session.child, first)
            self.assertIsNone(session.final)
            self.wait(session)
            self.assertFalse(clean_exit(session.returncode, session.final))
            self.assertEqual((root / "first.log").stat().st_mode & 0o777, 0o600)
            self.assertIn("native log", session.diagnostic_report().lower())

    def test_no_exit_zero_or_partial_receipt_becomes_clean_and_state_is_never_changed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); marker = root / ".dirty"; marker.write_text("unconfirmed")
            session = MacSession(root / "application.log")
            session.start([sys.executable, "-c", "print('FINAL confirmed=1 worker_exit=0')"], root / "failed.log")
            self.wait(session)
            self.assertEqual(session.returncode, 0)
            self.assertFalse(clean_exit(session.returncode, session.final))
            self.assertEqual(marker.read_text(), "unconfirmed")
            session.record_error("native", "shutdown unconfirmed")
            self.assertIn("shutdown unconfirmed", session.diagnostic_report())
            self.assertEqual((root / "application.log").stat().st_mode & 0o777, 0o600)
            self.assertIsNone(session.metrics()["worker"])


if __name__ == "__main__":
    unittest.main()
