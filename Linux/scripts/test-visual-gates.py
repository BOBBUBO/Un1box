#!/usr/bin/env python3
"""Offline watchdog receipt checks; no processes, engine or guest acceptance."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

spec = importlib.util.spec_from_file_location('visual_gates', Path(__file__).with_name('watch-visual-gates.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Gates(unittest.TestCase):
    def setUp(self):
        self.identity = {'frontend_pid': 12, 'start_ticks': '34', 'hello_monotonic_ns': 1_000_000_000}
        self.gate = dict(self.identity, model_inspected=True, scene='Home Screen',
                         inspected_monotonic_ns=129_000_000_000)
        self.receipt = {'observed_monotonic_ns': 128_000_000_000,
                        'frame_monotonic_ns': 127_900_000_000,
                        'diagnostics_monotonic_ns': 127_950_000_000,
                        'sequence': 2, 'sleeping': 0, 'agent': 1, 'backlight': 245}

    def check(self):
        return module.valid_gate(self.gate, self.receipt, self.identity, 'Home Screen',
                                 130_000_000_000, 150_000_000_000)

    def test_valid_inspection_before_deadline(self):
        self.assertTrue(self.check())

    def test_counters_do_not_prove_scene(self):
        self.gate['model_inspected'] = False
        self.assertFalse(self.check())
        self.gate['model_inspected'] = True
        self.gate['scene'] = 'Lock Screen'
        self.assertFalse(self.check())

    def test_late_inspection_cannot_extend_epoch(self):
        self.gate['inspected_monotonic_ns'] = 131_000_000_000
        self.assertFalse(self.check())
        self.gate['inspected_monotonic_ns'] = 129_000_000_000
        self.gate['hello_monotonic_ns'] += 1
        self.assertFalse(self.check())

    def test_reused_pid_stale_or_sleeping_receipt(self):
        self.gate['start_ticks'] = '35'
        self.assertFalse(self.check())
        self.gate['start_ticks'] = '34'
        self.receipt['sleeping'] = 1
        self.assertFalse(self.check())
        self.receipt['sleeping'] = 0
        self.receipt['frame_monotonic_ns'] = 100_000_000_000
        self.assertFalse(self.check())

    def watch_fixture(self, home):
        # A disposable Python process, not SDL/QEMU/a guest. This exercises
        # production PID/startticks/argv/pidfd and deadline handling directly.
        script = ('import signal,time\n'
                  'def stop(*args): raise SystemExit(0)\n'
                  'signal.signal(signal.SIGINT,stop)\n'
                  'print("ready",flush=True)\n'
                  'time.sleep(60)\n')
        command = [sys.executable, '-c', script]
        fixture = subprocess.Popen(command, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(fixture.stdout.readline().strip(), 'ready')
            ticks = Path(f'/proc/{fixture.pid}/stat').read_text().rpartition(')')[2].split()[19]
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'started.json').write_text(json.dumps({
                    'uid': os.geteuid(), 'frontend': {'pid': fixture.pid, 'start_ticks': ticks}}))
                (root / 'command.json').write_text(json.dumps({'argv': command}))
                hello = time.monotonic_ns() - (180 if home else 130) * 1_000_000_000
                (root / 'session.log').write_text(
                    f'admission hello_verified_observed_monotonic_ns={hello} fixture=host-only\n')
                if home:
                    capture = root / 'host-fixture-capture'
                    capture.mkdir()
                    receipt = dict(self.receipt, observed_monotonic_ns=hello + 120_000_000_000,
                                   frame_monotonic_ns=hello + 119_900_000_000,
                                   diagnostics_monotonic_ns=hello + 119_950_000_000)
                    (capture / 'receipt.json').write_text(json.dumps(receipt))
                    (capture / 'native.bmp').write_bytes(b'host-fixture; not a guest image')
                    import hashlib
                    gate = dict(self.gate, frontend_pid=fixture.pid, start_ticks=ticks,
                                hello_monotonic_ns=hello, inspected_monotonic_ns=hello + 121_000_000_000,
                                capture_directory=str(capture),
                                receipt_sha256=hashlib.sha256((capture / 'receipt.json').read_bytes()).hexdigest(),
                                bmp_sha256=hashlib.sha256((capture / 'native.bmp').read_bytes()).hexdigest())
                    (root / 'home-gate.json').write_text(json.dumps(gate))
                result = subprocess.run([sys.executable, str(Path(module.__file__)), '--run', str(root), '--execute'],
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                receipt = json.loads((root / 'visual-gate-stop.json').read_text())
                self.assertEqual(receipt['identity']['frontend_pid'], fixture.pid)
                self.assertEqual(receipt['identity']['start_ticks'], ticks)
                self.assertEqual(receipt['deadline_monotonic_ns'], hello + (179 if home else 129) * 1_000_000_000)
                self.assertEqual(receipt['missing_gate'], 'editor-gate.json' if home else 'home-gate.json')
                self.assertFalse(receipt['guest_acceptance'])
                self.assertEqual(fixture.wait(timeout=5), 0)
        finally:
            if fixture.poll() is None:
                fixture.kill()
                fixture.wait(timeout=5)
            fixture.stdout.close()

    def test_missing_home_gate_signals_only_owned_host_fixture(self):
        self.watch_fixture(False)

    def test_home_gate_cannot_restart_missing_editor_deadline(self):
        self.watch_fixture(True)


if __name__ == '__main__':
    unittest.main()
