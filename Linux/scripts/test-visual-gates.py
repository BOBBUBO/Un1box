#!/usr/bin/env python3
"""Offline watchdog receipt checks; no processes, engine or guest acceptance."""
import importlib.util
import copy
import hashlib
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


class ContinuityGates(unittest.TestCase):
    def setUp(self):
        self.identity = dict(frontend_pid=12, start_ticks='34', hello_monotonic_ns=1_000_000_000)
        self.original = dict(observed_monotonic_ns=100_000_000_000, sequence=10,
                             frame_monotonic_ns=99_900_000_000, diagnostics_monotonic_ns=99_950_000_000,
                             sleeping=0, agent=1, backlight=245)
        self.current = dict(self.original, observed_monotonic_ns=115_000_000_000, sequence=11,
                            frame_monotonic_ns=114_900_000_000, diagnostics_monotonic_ns=114_950_000_000)
        self.proof = dict(self.identity, owner_checked_monotonic_ns=115_010_000_000,
                          worker_pid=13, worker_start_ticks='35',
                          verified_monotonic_ns=115_020_000_000, window=42, viewport=[480, 800],
                          status=dict(state=2, observed_monotonic_ns=115_000_000_000,
                                      sleeping=0, agent=1, backlight=245))
        self.gate = dict(self.identity, model_inspected=True, scene='Home Screen',
                         worker_pid=13, worker_start_ticks='35',
                         inspected_monotonic_ns=110_000_000_000, window=42, viewport=[480, 800],
                         continuity=self.proof)
        self.before = copy.deepcopy(self.gate)
        self.current_bytes = b'full native BMP host fixture, not a guest'

    def check(self, now=180_000_000_000):
        return module.valid_continuity_gate(
            self.gate, self.original, self.current, b'full native BMP host fixture, not a guest',
            self.current_bytes, self.identity, 'Home Screen', 130_000_000_000, now)

    def test_true_inspection_ten_seconds_after_original_capture_has_current_proof(self):
        self.assertFalse(module.valid_gate(self.gate, self.original, self.identity,
                                          'Home Screen', 130_000_000_000, 180_000_000_000))
        self.assertTrue(self.check())
        self.assertEqual(self.gate, self.before)
        # A past admitted Home gate stays valid when Notes intentionally opens;
        # later polls do not reset an epoch or demand the guest remain Home.
        self.assertTrue(self.check(now=300_000_000_000))

    def test_changed_clock_or_any_byte_rejects(self):
        self.current_bytes += b'one clock pixel changed'
        self.assertFalse(self.check())

    def test_fake_scene_or_missing_model_observation_rejects(self):
        self.gate['model_inspected'] = False
        self.assertFalse(self.check())
        self.gate['model_inspected'] = True
        self.gate['scene'] = 'Lock Screen'
        self.assertFalse(self.check())

    def test_current_capture_must_follow_actual_inspection(self):
        self.gate['inspected_monotonic_ns'] = 116_000_000_000
        self.assertFalse(self.check())

    def test_current_verification_cannot_extend_original_deadline(self):
        self.proof['verified_monotonic_ns'] = 130_000_000_001
        self.assertFalse(self.check())
        self.proof['verified_monotonic_ns'] = 115_020_000_000
        self.gate['hello_monotonic_ns'] += 1
        self.assertFalse(self.check())

    def test_original_inspection_reuse_bounded_at_admission(self):
        self.gate['inspected_monotonic_ns'] = 54_000_000_000
        self.original['observed_monotonic_ns'] = 53_000_000_000
        self.original['frame_monotonic_ns'] = self.original['diagnostics_monotonic_ns'] = 53_000_000_000
        self.assertFalse(self.check())

    def test_stale_source_diagnostics_or_running_state_rejects(self):
        for field, value in [('frame_monotonic_ns', 100_000_000_000),
                             ('diagnostics_monotonic_ns', 110_000_000_000), ('sleeping', 1), ('agent', 2)]:
            before = self.current[field]
            self.current[field] = value
            self.assertFalse(self.check(), field)
            self.current[field] = before
        self.proof['status']['state'] = 4
        self.assertFalse(self.check())

    def test_owner_window_viewport_and_future_times_reject(self):
        for field, value in [('start_ticks', 'reused'), ('window', 43), ('viewport', [800, 480]),
                             ('worker_start_ticks', 'reused-guest'),
                             ('owner_checked_monotonic_ns', 115_030_000_000)]:
            before = self.proof[field]
            self.proof[field] = value
            self.assertFalse(self.check(), field)
            self.proof[field] = before
        self.assertFalse(self.check(now=114_000_000_000))

    def test_loader_reads_both_bounded_hashed_captures_and_checks_actual_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'worker.json').write_text(json.dumps({'pid': 13, 'start_ticks': '35'}))
            for name, receipt in [('original', self.original), ('fresh', self.current)]:
                path = root / name
                path.mkdir()
                (path / 'receipt.json').write_text(json.dumps(receipt))
                (path / 'native.bmp').write_bytes(self.current_bytes)
                description = dict(capture_directory=str(path),
                                   receipt_sha256=hashlib.sha256((path / 'receipt.json').read_bytes()).hexdigest(),
                                   bmp_sha256=hashlib.sha256((path / 'native.bmp').read_bytes()).hexdigest())
                (self.gate if name == 'original' else self.proof).update(description)
            (root / 'home-gate.json').write_text(json.dumps(self.gate))
            self.assertTrue(module.load_gate(root, 'home-gate.json', self.identity,
                                             'Home Screen', 130_000_000_000, 180_000_000_000))
            # Even after the fresh hash is updated, different actual pixels
            # cannot reuse the prior scene observation.
            path = root / 'fresh' / 'native.bmp'
            path.write_bytes(b'changed clock pixels')
            self.proof['bmp_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            (root / 'home-gate.json').write_text(json.dumps(self.gate))
            self.assertFalse(module.load_gate(root, 'home-gate.json', self.identity,
                                              'Home Screen', 130_000_000_000, 180_000_000_000))


if __name__ == '__main__':
    unittest.main()
