#!/usr/bin/env python3
"""Offline watchdog receipt checks; no processes, engine or guest acceptance."""
import importlib.util
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import time
import unittest

spec = importlib.util.spec_from_file_location('visual_gates', Path(__file__).with_name('watch-visual-gates.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def n72_bitmap():
    """Host-only BMP fixture; no scene content or guest acceptance assertion."""
    data = bytearray(54 + 320 * 480 * 4)
    data[:2] = b'BM'
    struct.pack_into('<I', data, 2, len(data))
    struct.pack_into('<I', data, 10, 54)
    struct.pack_into('<IiiHHII', data, 14, 40, 320, -480, 1, 32, 0, 320 * 480 * 4)
    data[54:] = bytes(range(256)) * (320 * 480 * 4 // 256)
    return bytes(data)


def changed_pixel(data, x, y, channel=0):
    result = bytearray(data)
    result[54 + (y * 320 + x) * 4 + channel] ^= 1
    return bytes(result)


class HomeClockImages(unittest.TestCase):
    def test_only_declared_clock_region_can_differ(self):
        old = n72_bitmap()
        for x, y in ((130, 1), (145, 4), (189, 18)):
            result = module.image_continuity(old, changed_pixel(old, x, y), 'Home Screen')
            self.assertTrue(result['matched'])
            self.assertFalse(result['full_BMP_identical'])
            self.assertTrue(result['no_new_scene_asserted'])
            self.assertEqual(result['excluded_clock_box'], [130, 1, 190, 19])

    def test_clock_boundary_grid_dock_battery_wifi_and_alpha_changes_reject(self):
        old = n72_bitmap()
        for x, y in ((129, 1), (190, 1), (130, 0), (130, 19), (30, 40),
                     (150, 240), (50, 420), (306, 9), (48, 8)):
            for channel in range(4):
                self.assertFalse(module.image_continuity(old, changed_pixel(old, x, y, channel),
                                                        'Home Screen')['matched'], (x, y, channel))

    def test_black_or_partial_icon_grid_rejects(self):
        old = n72_bitmap()
        black = bytearray(old)
        black[54 + 20 * 320 * 4:54 + 385 * 320 * 4] = bytes(365 * 320 * 4)
        self.assertFalse(module.image_continuity(old, bytes(black), 'Home Screen')['matched'])
        partial = changed_pixel(old, 45, 60)
        self.assertFalse(module.image_continuity(old, partial, 'Home Screen')['matched'])

    def test_editor_and_other_scenes_keep_full_image_equality(self):
        old = n72_bitmap()
        for scene in ('Notes focused new editor', 'Notes written marker', 'Notes saved marker', 'Lock Screen'):
            self.assertFalse(module.image_continuity(old, changed_pixel(old, 145, 4), scene)['matched'])

    def test_shape_format_header_truncation_and_trailing_bytes_reject(self):
        old = n72_bitmap()
        for current in (old[:-1], old + b'x', b'unknown image'):
            self.assertFalse(module.image_continuity(old, current, 'Home Screen')['matched'])
        for offset, fmt, value in ((18, '<i', 321), (22, '<i', -479), (28, '<H', 24),
                                    (30, '<I', 1), (10, '<I', 55), (38, '<I', 1)):
            current = bytearray(changed_pixel(old, 145, 4))
            struct.pack_into(fmt, current, offset, value)
            self.assertFalse(module.image_continuity(old, bytes(current), 'Home Screen')['matched'])


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

    def watch_fixture(self, home, saved=False):
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
                (root / 'inputs.json').write_text(json.dumps({'marker': 'HOST fixture marker, not a guest'}))
                result = subprocess.run([sys.executable, str(Path(module.__file__)), '--run', str(root), '--execute',
                                         *(['--saved-marker'] if saved else [])],
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                receipt = json.loads((root / 'visual-gate-stop.json').read_text())
                self.assertEqual(receipt['identity']['frontend_pid'], fixture.pid)
                self.assertEqual(receipt['identity']['start_ticks'], ticks)
                self.assertEqual(receipt['deadline_monotonic_ns'], hello + (179 if home else 129) * 1_000_000_000)
                self.assertEqual(receipt['missing_gate'], ('saved-marker-gate.json' if saved else 'editor-gate.json') if home else 'home-gate.json')
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

    def test_read_worker_uses_saved_marker_deadline_not_new_editor(self):
        self.watch_fixture(True, saved=True)


class ContinuityGates(unittest.TestCase):
    def setUp(self):
        self.identity = dict(frontend_pid=12, start_ticks='34', hello_monotonic_ns=1_000_000_000)
        self.original = dict(observed_monotonic_ns=100_000_000_000, sequence=10,
                             frame_monotonic_ns=99_900_000_000, diagnostics_monotonic_ns=99_950_000_000,
                             sleeping=0, agent=1, backlight=245)
        self.current = dict(self.original, observed_monotonic_ns=115_000_000_000, sequence=11,
                            copy_started_monotonic_ns=114_990_000_000,
                            copy_finished_monotonic_ns=114_999_000_000,
                            saved_monotonic_ns=115_005_000_000, coherent_readback=1, shared_generation=40,
                            frame_monotonic_ns=114_900_000_000, diagnostics_monotonic_ns=114_950_000_000)
        self.proof = dict(self.identity, owner_checked_monotonic_ns=115_010_000_000,
                          worker_pid=13, worker_start_ticks='35',
                          verified_monotonic_ns=115_020_000_000, window=42, viewport=[480, 800],
                          policy='coherent-copy-static-awake-v2',
                          metadata_readback=dict(sequence=11, frame_monotonic_ns=114_900_000_000,
                                                 shared_generation=40),
                          status=dict(state=2, observed_monotonic_ns=115_000_000_000,
                                      frame_sequence=11, frame_monotonic_ns=114_900_000_000,
                                      sleeping=0, agent=1, backlight=245))
        self.gate = dict(self.identity, model_inspected=True, scene='Home Screen',
                         worker_pid=13, worker_start_ticks='35',
                         inspected_monotonic_ns=110_000_000_000, window=42, viewport=[480, 800],
                         continuity=self.proof)
        self.before = copy.deepcopy(self.gate)
        self.original_bytes = self.current_bytes = b'full native BMP host fixture, not a guest'

    def check(self, now=180_000_000_000):
        return module.valid_continuity_gate(
            self.gate, self.original, self.current, self.original_bytes,
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

    def test_blank_editor_assertion_cannot_be_invented_on_other_scene(self):
        self.gate['blank_focused_editor_model_assertion'] = True
        self.proof['blank_focused_editor_model_assertion'] = True
        self.assertFalse(self.check())

    def test_blank_editor_assertion_must_match_its_continuity_proof(self):
        self.gate['blank_focused_editor_model_assertion'] = True
        self.assertFalse(self.check())
        self.gate['blank_focused_editor_model_assertion'] = 1
        self.proof['blank_focused_editor_model_assertion'] = 1
        self.assertFalse(self.check())

    def test_home_clock_exception_requires_explicit_recomputed_proof(self):
        self.original_bytes = n72_bitmap()
        self.current_bytes = changed_pixel(self.original_bytes, 145, 4)
        self.assertFalse(self.check())
        self.proof['image_comparison'] = module.image_continuity(self.original_bytes, self.current_bytes,
                                                                'Home Screen')
        self.assertTrue(self.check())
        self.proof['image_comparison']['protected_current_sha256'] = 'forged'
        self.assertFalse(self.check())

    def test_clock_exception_never_relaxes_ownership_age_or_deadline(self):
        self.original_bytes = n72_bitmap()
        self.current_bytes = changed_pixel(self.original_bytes, 145, 4)
        self.proof['image_comparison'] = module.image_continuity(self.original_bytes, self.current_bytes,
                                                                'Home Screen')
        for owner, key, value in ((self.proof, 'worker_start_ticks', 'reused'),
                                   (self.current, 'sleeping', 1),
                                   (self.current, 'shared_generation', 41),
                                   (self.proof, 'verified_monotonic_ns', 130_000_000_001)):
            previous = owner[key]
            owner[key] = value
            self.assertFalse(self.check(), key)
            owner[key] = previous

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
        for field, value in [('frame_monotonic_ns', 116_000_000_000),
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

    def test_static_awake_same_worker_current_readback_admits_without_redraw(self):
        # An actual static screen can be retained for many seconds. Fresh
        # capture/readback/diagnostics, not publication age, proves observation.
        self.current['sequence'] = self.original['sequence']
        self.current['frame_monotonic_ns'] = self.original['frame_monotonic_ns'] = 500_000_000
        self.proof['metadata_readback']['sequence'] = self.proof['status']['frame_sequence'] = 10
        self.proof['metadata_readback']['frame_monotonic_ns'] = self.proof['status']['frame_monotonic_ns'] = 500_000_000
        self.assertTrue(self.check())  # First source may precede parent HELLO.
        self.assertEqual(self.gate['inspected_monotonic_ns'], 110_000_000_000)

    def test_capture_readback_sequence_timestamp_and_future_clock_reject(self):
        for owner, key, value in [(self.proof['metadata_readback'], 'sequence', 12),
                                  (self.proof['metadata_readback'], 'frame_monotonic_ns', 114_800_000_000),
                                  (self.current, 'frame_monotonic_ns', 0),
                                  (self.current, 'frame_monotonic_ns', 116_000_000_000),
                                  (self.current, 'copy_finished_monotonic_ns', 115_030_000_000),
                                  (self.current, 'copy_finished_monotonic_ns', 110_000_000_000)]:
            before = owner[key]
            owner[key] = value
            self.assertFalse(self.check(), key)
            owner[key] = before

    def test_status_sampler_can_lag_coherent_actual_copy(self):
        self.proof['status']['frame_sequence'] = 5
        self.proof['status']['frame_monotonic_ns'] = 90_000_000_000
        self.assertTrue(self.check())  # Status is diagnostics, not copy readback.

    def test_coherent_native_copy_proof_and_clock_order_required(self):
        for key, value in [('coherent_readback', 0), ('shared_generation', 41),
                           ('shared_generation', 0), ('copy_started_monotonic_ns', 115_001_000_000),
                           ('copy_started_monotonic_ns', 110_000_000_000),
                           ('copy_finished_monotonic_ns', 115_010_000_000),
                           ('saved_monotonic_ns', 114_000_000_000)]:
            before = self.current[key]
            self.current[key] = value
            self.assertFalse(self.check(), key)
            self.current[key] = before
        self.proof['metadata_readback']['shared_generation'] = 42
        self.assertFalse(self.check())

    def test_caret_retry_preserves_model_time_and_fixed_cadence(self):
        clock = [115_000_000_000]
        attempts = []
        inspected = self.gate['inspected_monotonic_ns']
        for attempt in module.capture_attempts(lambda: clock[0],
                                               lambda seconds: clock.__setitem__(0, clock[0] + int(seconds * 1e9)),
                                               127_000_000_000):
            attempts.append(clock[0])
            if attempt == 1:
                self.current_bytes = b'caret opposite phase'
                self.assertFalse(self.check())
            else:
                self.current_bytes = b'full native BMP host fixture, not a guest'
                self.assertTrue(self.check())
                break
            clock[0] += 100_000_000  # Actual capture work consumes the budget.
        self.assertEqual(attempts, [115_000_000_000, 115_500_000_000])
        self.assertEqual(self.gate['inspected_monotonic_ns'], inspected)

    def test_retry_exhaustion_never_extends_operation_or_original_gate(self):
        clock = [115_000_000_000]
        starts = []
        for _ in module.capture_attempts(lambda: clock[0],
                                         lambda seconds: clock.__setitem__(0, clock[0] + int(seconds * 1e9)),
                                         116_000_000_000):
            starts.append(clock[0])
            self.current_bytes = b'never matches inspected caret'
            self.assertFalse(self.check())
        self.assertEqual(starts, [115_000_000_000, 115_500_000_000])
        self.assertEqual(clock[0], 116_000_000_000)
        self.proof['verified_monotonic_ns'] = 130_000_000_001
        self.assertFalse(self.check())

    def test_consistent_but_rolled_back_source_rejects(self):
        self.current['sequence'] = self.proof['status']['frame_sequence'] = self.proof['metadata_readback']['sequence'] = 9
        self.assertFalse(self.check())
        self.current['sequence'] = self.proof['status']['frame_sequence'] = self.proof['metadata_readback']['sequence'] = 11
        self.current['frame_monotonic_ns'] = self.proof['status']['frame_monotonic_ns'] = self.proof['metadata_readback']['frame_monotonic_ns'] = 99_000_000_000
        self.assertFalse(self.check())

    def test_capture_and_diagnostics_age_never_relaxed_by_static_policy(self):
        for owner, key, value in [(self.proof, 'verified_monotonic_ns', 118_000_000_000),
                                  (self.proof['status'], 'observed_monotonic_ns', 110_000_000_000),
                                  (self.current, 'backlight', 0),
                                  (self.current, 'agent', 0),
                                  (self.current, 'sleeping', 1),
                                  (self.proof['status'], 'state', 5)]:
            before = owner[key]
            owner[key] = value
            self.assertFalse(self.check(), key)
            owner[key] = before

    def test_transition_requires_actual_changed_pixels_and_newer_source(self):
        before = dict(self.original, sequence=9, observed_monotonic_ns=90_000_000_000,
                      frame_monotonic_ns=89_000_000_000)
        self.assertTrue(module.valid_scene_transition(before, self.original, b'Lock fixture', b'Home fixture'))
        self.assertFalse(module.valid_scene_transition(before, self.original, b'same', b'same'))
        self.assertFalse(module.valid_scene_transition(self.original, self.original, b'Lock', b'Home'))
        self.assertFalse(module.valid_scene_transition(before, dict(self.original, sequence=8), b'Lock', b'Home'))
        self.assertFalse(module.valid_scene_transition(before, dict(self.original, frame_monotonic_ns=101_000_000_000), b'Lock', b'Home'))

    def test_loader_reads_both_bounded_hashed_captures_and_checks_actual_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'worker.json').write_text(json.dumps({'pid': 13, 'start_ticks': '35'}))
            for name, receipt in [('before', dict(self.original, sequence=9, observed_monotonic_ns=90_000_000_000, frame_monotonic_ns=89_000_000_000)), ('original', self.original), ('fresh', self.current)]:
                path = root / name
                path.mkdir()
                (path / 'receipt.json').write_text(json.dumps(receipt))
                (path / 'native.bmp').write_bytes(b'pre-action fixture' if name == 'before' else self.current_bytes)
                description = dict(capture_directory=str(path),
                                   receipt_sha256=hashlib.sha256((path / 'receipt.json').read_bytes()).hexdigest(),
                                   bmp_sha256=hashlib.sha256((path / 'native.bmp').read_bytes()).hexdigest())
                if name == 'before':
                    self.gate['transition_from'] = description
                else:
                    (self.gate if name == 'original' else self.proof).update(description)
            (root / 'home-gate.json').write_text(json.dumps(self.gate))
            self.assertTrue(module.load_gate(root, 'home-gate.json', self.identity,
                                             'Home Screen', 130_000_000_000, 180_000_000_000))
            for key in ('written-marker', 'saved-marker'):
                name, scene, seconds = module.SCENES[key]
                marker = 'HOST full unique marker, no guest evidence'
                self.gate.update(scene=scene, observed_marker=marker)
                (root / name).write_text(json.dumps(self.gate))
                deadline = self.identity['hello_monotonic_ns'] + seconds * 1_000_000_000
                self.assertTrue(module.load_gate(root, name, self.identity, scene, deadline, 300_000_000_000, marker))
                self.assertFalse(module.load_gate(root, name, self.identity, scene, deadline, 300_000_000_000))
                self.assertFalse(module.load_gate(root, name, self.identity, scene, deadline, 300_000_000_000, marker + 'wrong'))
                self.gate['model_inspected'] = False
                (root / name).write_text(json.dumps(self.gate))
                self.assertFalse(module.load_gate(root, name, self.identity, scene, deadline, 300_000_000_000, marker))
                self.gate['model_inspected'] = True
            self.gate['scene'] = 'Home Screen'
            self.gate.pop('observed_marker')
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
