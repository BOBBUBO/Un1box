# SPDX-License-Identifier: GPL-2.0-or-later
import importlib.util
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

spec = importlib.util.spec_from_file_location("retry", Path(__file__).resolve().parents[1]/"supervise_16k_retry.py")
retry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retry)


class Supervision(unittest.TestCase):
    def test_independent_deadline_disposes_hung_owned_child(self):
        # Actual OS subprocesses, no Android tools, compiler, window or guest.
        with tempfile.TemporaryDirectory() as directory:
            child = subprocess.Popen([sys.executable,"-c",
                "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(60)"],
                stdout=subprocess.PIPE,start_new_session=True)
            self.assertEqual(child.stdout.readline(),b"ready\n")
            done = retry.PROCESS_CONTEXT.Event()
            watcher = retry.PROCESS_CONTEXT.Process(target=retry.watchdog,
                args=(child.pid,time.monotonic()+.8,done,str(Path(directory)/"deadline.json")))
            watcher.start()
            try:
                self.assertIn(child.wait(timeout=3),(-signal.SIGTERM,-signal.SIGKILL))
                watcher.join(timeout=3)
                self.assertFalse(watcher.is_alive())
                self.assertTrue((Path(directory)/"deadline.json").exists())
            finally:
                done.set()
                if child.poll() is None:
                    child.kill()
                child.wait()
                child.stdout.close()
                watcher.join(timeout=3)

    def test_mismatching_start_ticks_cannot_signal_an_unowned_pid(self):
        child = subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])
        try:
            identity = retry.process_identity(child.pid)
            self.assertIsNotNone(identity)
            retry.signal_owned({child.pid:identity[1]+1},signal.SIGKILL)
            self.assertIsNone(child.poll())
        finally:
            child.kill(); child.wait()

    def test_clean_completion_disarms_deadline(self):
        child = subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])
        with tempfile.TemporaryDirectory() as directory:
            done = retry.PROCESS_CONTEXT.Event(); done.set()
            watcher = retry.PROCESS_CONTEXT.Process(target=retry.watchdog,
                args=(child.pid,time.monotonic(),done,str(Path(directory)/"deadline.json")))
            watcher.start(); watcher.join(timeout=3)
            try:
                self.assertFalse(watcher.is_alive())
                self.assertIsNone(child.poll())
                self.assertFalse((Path(directory)/"deadline.json").exists())
            finally:
                child.kill(); child.wait()

    def test_semantic_stop_uses_unique_enabled_control(self):
        xml = '<node package="org.lighttouch.android" clickable="true" enabled="true" bounds="[10,20][50,60]"><node text="Stop"/></node>'
        self.assertEqual(retry.semantic_stop_bounds(xml),(30,40))
        with self.assertRaises(ValueError):
            retry.semantic_stop_bounds(xml.replace('enabled="true"','enabled="false"'))
        with self.assertRaises(ValueError):
            retry.semantic_stop_bounds('<root>'+xml+xml.replace('[10,20][50,60]','[60,20][100,60]')+'</root>')
        with self.assertRaises(ValueError):
            retry.semantic_stop_bounds(xml.replace('org.lighttouch.android','another.application'))

    def test_reused_root_pid_cannot_adopt_an_unrelated_process_tree(self):
        child = subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])
        try:
            start = retry.process_identity(child.pid)[1]
            owned = {child.pid:start-1}
            retry.retain_descendants(child.pid,owned)
            self.assertEqual(owned,{child.pid:start-1})
            retry.signal_owned(owned,signal.SIGKILL)
            self.assertIsNone(child.poll())
        finally:
            child.kill(); child.wait()


if __name__ == "__main__":
    unittest.main()
