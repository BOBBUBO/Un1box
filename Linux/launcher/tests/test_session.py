from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import session


class SessionTests(unittest.TestCase):
    def test_application_error_survives_buffered_native_output_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = session.Session(root / "application.log")
            value.record_error("apps", "before guest: certificate verify failed")
            self.assertIn("certificate verify failed", value.diagnostic_report())
            self.assertEqual(value.application_log.stat().st_mode & 0o777, 0o600)
            # The initial native output stays in Python's buffered log writer.
            # A separate application append must survive the later flush.
            source = "import time; print('native-start',flush=True); time.sleep(.3); print('native-end',flush=True)"
            value.start([sys.executable, "-c", source], root / "guest.log")
            deadline = time.monotonic() + 3
            while "native-start" not in value.tail and time.monotonic() < deadline:
                value.poll()
                time.sleep(.01)
            self.assertIn("native-start", value.tail)
            value.record_error("apps", "during guest: TLS unavailable")
            value.thread.join(3)
            self.assertFalse(value.active)
            value.poll()
            log = value.log_path.read_text()
            for text in ("native-start", "native-end", "TLS unavailable"):
                self.assertIn(text, log)
                self.assertIn(text, value.diagnostic_report())
            value.start([sys.executable, "-c", "print('new worker')"], root / "second.log")
            value.thread.join(3)
            value.poll()
            self.assertIn("TLS unavailable", value.diagnostic_report())

    def test_application_log_is_bounded_and_cannot_follow_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.write_text("preserve")
            path = root / "application.log"
            path.symlink_to(target)
            value = session.Session(path)
            value.record_error("apps", "retained despite disk failure")
            self.assertEqual(target.read_text(), "preserve")
            self.assertIn("retained despite disk failure", value.diagnostic_report())
            path.unlink()
            path.write_bytes(b'x' * (1024 * 1024))
            value.record_error("apps", "new segment")
            self.assertLess(path.stat().st_size, 1024)
            self.assertTrue(path.with_suffix(".previous.log").exists())

    def test_exit_zero_without_final_is_unconfirmed(self):
        self.assertFalse(session.clean_exit(0, None))
        final = dict(status_available="1", state="0", confirmed="1", dirty="0", worker_reaped="1", worker_exit="0", worker_disposal_requested="0", frontend_return="0")
        self.assertTrue(session.clean_exit(0, final))
        for change in (dict(dirty="1"), dict(worker_disposal_requested="1"), dict(confirmed="0")):
            self.assertFalse(session.clean_exit(0, dict(final, **change)))
        self.assertFalse(session.clean_exit(1, final))

    def test_native_log_worker_identity_is_not_arbitrary_process(self):
        value = session.Session()
        value.child = type("Own", (), {"pid": -1})()
        value.queue.put("admission worker_pid=1 start_ticks=unrelated")
        value.child.poll = lambda: None
        value.poll()
        self.assertIsNone(value.identity)

    @unittest.skipUnless(hasattr(signal, "pidfd_send_signal"), "Linux pidfd required")
    def test_owned_stop_and_fresh_process_per_session(self):
        # A tiny synthetic Python child, never an emulator or guest.
        source = "import signal,time; signal.signal(signal.SIGINT, lambda *_: exit(0)); print('ready',flush=True); time.sleep(10)"
        with tempfile.TemporaryDirectory() as directory:
            value = session.Session()
            old_pid = None
            for index in range(2):
                value.start([sys.executable, "-c", source], Path(directory) / (str(index) + ".log"))
                self.assertNotEqual(value.child.pid, old_pid)
                old_pid = value.child.pid
                with self.assertRaisesRegex(RuntimeError, "already active"):
                    value.start(["unused"], Path(directory) / "unused.log")
                deadline = time.monotonic() + 3
                while "ready" not in value.tail and time.monotonic() < deadline:
                    value.poll()
                    time.sleep(.01)
                self.assertIn("ready", value.tail)
                value.stop()
                while value.active and time.monotonic() < deadline:
                    value.poll()
                    time.sleep(.01)
                value.poll()
                self.assertEqual(value.returncode, 0)
                self.assertFalse(session.clean_exit(value.returncode, value.final))
                self.assertEqual(value.log_path.stat().st_mode & 0o777, 0o600)
                self.assertFalse((Path(directory) / "unused.log").exists())


if __name__ == "__main__":
    unittest.main()
