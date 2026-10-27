"""Real tiny native-host substitutes exercise the common desktop session owner."""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lighttouch_desktop.macos import MacSession
from lighttouch_desktop.session import ManagedSession, strict_clean_exit
from lighttouch_desktop.storage import StorageOps


class SessionTests(unittest.TestCase):
    def test_shared_process_lifecycle_uses_host_log_callback_and_requires_full_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); calls = []; ops = StorageOps()
            def open_log(path, *, exclusive):
                calls.append((Path(path), exclusive))
                return ops.open_log(path, exclusive=exclusive)
            session = ManagedSession(root / "application.log", open_log=open_log, label="Shared Windows adapter contract")
            source = """import sys
assert sys.stdin.readline() == 'stop\\n'
print('FINAL status_available=1 state=0 confirmed=1 dirty=0 worker_reaped=1 worker_exit=0 worker_disposal_requested=0 frontend_return=0', flush=True)
"""
            session.start([sys.executable, "-c", source], root / "worker.log")
            child = session.child
            try:
                session.stop()
                deadline = time.monotonic() + 3
                while session.active and time.monotonic() < deadline:
                    session.poll(); time.sleep(.005)
                session.poll()
                self.assertFalse(session.active)
                self.assertTrue(strict_clean_exit(session.returncode, session.final))
                session.record_error("test", "retained diagnostic")
                self.assertEqual(calls, [(root / "worker.log", True), (root / "application.log", False)])
                self.assertIn("Shared Windows adapter contract", session.diagnostic_report())
                self.assertIn("retained diagnostic", session.diagnostic_report())
                session.start([sys.executable, "-c", "print('status state=2')"], root / "next.log")
                deadline = time.monotonic() + 3
                while session.active and time.monotonic() < deadline:
                    session.poll(); time.sleep(.005)
                session.poll()
                self.assertEqual(session.status["state"], "2")
                report = session.diagnostic_report()
                self.assertIn("Previous setup/session log: " + str(root / "worker.log"), report)
                self.assertIn("FINAL status_available=1", report)
                self.assertFalse(strict_clean_exit(0, {"confirmed": "1"}))
                self.assertTrue(issubclass(MacSession, ManagedSession))
            finally:
                if child.poll() is None:
                    child.kill(); child.wait(timeout=1)


if __name__ == "__main__":
    unittest.main()
