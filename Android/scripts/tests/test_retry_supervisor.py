# SPDX-License-Identifier: GPL-2.0-or-later
import importlib.util
from pathlib import Path
import signal
import shlex
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("retry", Path(__file__).resolve().parents[1]/"supervise_16k_retry.py")
retry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retry)


class Supervision(unittest.TestCase):
    def arguments(self, directory):
        return SimpleNamespace(output=str(Path(directory)/"attempt"),adb="/synthetic/adb",
            emulator="/synthetic/emulator",avd="synthetic_16k",user="synthetic")

    def test_launch_pins_port_and_never_uses_auto_serial(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(retry.subprocess,"run",return_value=SimpleNamespace(returncode=0,stdout=b"List of devices attached\n")) as run, \
             mock.patch.object(retry.subprocess,"Popen") as popen, \
             mock.patch.object(retry.PROCESS_CONTEXT,"Process"):
            popen.return_value.pid = 987654
            attempt = retry.Attempt(self.arguments(directory))
            attempt.launch()
            self.assertEqual(run.call_args.args[0],["/synthetic/adb","devices","-l"])
            argv = popen.call_args.args[0]
            self.assertEqual(argv[argv.index("-port"):argv.index("-port")+2],["-port","5554"])
            self.assertEqual(argv[argv.index("-gpu"):argv.index("-gpu")+2],["-gpu","swiftshader"])

    def test_existing_serial_refuses_launch_in_every_adb_state(self):
        for state in ("device","offline","unauthorized","no permissions"):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as directory, \
                 mock.patch.object(retry.subprocess,"run",return_value=SimpleNamespace(returncode=0,
                    stdout=f"List of devices attached\nemulator-5554\t{state}\n".encode())), \
                 mock.patch.object(retry.subprocess,"Popen") as popen:
                attempt = retry.Attempt(self.arguments(directory))
                with self.assertRaisesRegex(RuntimeError,"already exists"):
                    attempt.launch()
                popen.assert_not_called()
                self.assertIsNone(attempt.avd)

    def test_failed_enumeration_refuses_launch(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(retry.subprocess,"run",return_value=SimpleNamespace(returncode=1,stdout=b"")), \
             mock.patch.object(retry.subprocess,"Popen") as popen:
            with self.assertRaisesRegex(RuntimeError,"Cannot enumerate"):
                retry.Attempt(self.arguments(directory)).launch()
            popen.assert_not_called()

    def test_cleanup_handles_watchdog_that_did_not_start(self):
        for watcher in (None,SimpleNamespace(pid=None)):
            with self.subTest(watcher=watcher), tempfile.TemporaryDirectory() as directory, \
                 mock.patch.object(retry,"retain_descendants"), mock.patch.object(retry,"signal_owned"):
                attempt = retry.Attempt(self.arguments(directory))
                attempt.avd = mock.Mock(pid=987654)
                attempt.avd.poll.return_value = 0
                attempt.watcher = watcher
                attempt.adb = mock.Mock(return_value=(0,b""))
                attempt.cleanup()
                self.assertTrue((attempt.output/"terminal.json").exists())

    def test_remote_path_presence_requires_sentinel_not_adb_exit_zero(self):
        cases = [(0,b"LT_PATH_PRESENT\n",True),(0,b"LT_PATH_ABSENT\n",False),
                 (0,b"stat: No such file or directory\n",None),(0,b"LT_PATH_UNKNOWN\n",None),
                 (1,b"LT_PATH_ABSENT\n",None),(0,b"",None)]
        for code,output,expected in cases:
            with self.subTest(output=output), tempfile.TemporaryDirectory() as directory:
                attempt = retry.Attempt(self.arguments(directory))
                attempt.adb = mock.Mock(return_value=(code,output))
                self.assertIs(attempt.path_presence("files/library/records/test/state/.dirty"),expected)
                self.assertEqual(attempt.adb.call_args.args[0],"shell")
                self.assertIn("LT_PATH_PRESENT",attempt.adb.call_args.args[1])

    def test_presence_shell_handles_existing_absent_inaccessible_parent_and_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            attempt = retry.Attempt(self.arguments(directory))
            root = Path(directory)
            (root/"present").write_text("retained")
            (root/"link").symlink_to(root/"absent")
            def shell(*argv):
                self.assertEqual(argv[0],"shell")
                words = shlex.split(argv[1])
                self.assertEqual(words[:3],["run-as",retry.PACKAGE,"sh"])
                run = subprocess.run(words[2:],capture_output=True)
                return run.returncode,run.stdout
            attempt.adb = shell
            self.assertIs(attempt.path_presence(str(root/"present")),True)
            self.assertIs(attempt.path_presence(str(root/"absent")),False)
            self.assertIs(attempt.path_presence(str(root/"missing-parent/absent")),None)
            self.assertIs(attempt.path_presence(str(root/"link")),True)

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
