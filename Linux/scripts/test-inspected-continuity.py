#!/usr/bin/env python3
"""Target-free checks of the actual rewake admission policy. No guest claims."""
import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    'continuity', Path(__file__).with_name('inspected-continuity.py'))
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class Continuity(unittest.TestCase):
    def setUp(self):
        self.clock = 20_000_000_000
        self.gate = dict(model_inspected=True, scene='Lock Screen', frontend_pid=12,
                         start_ticks='34', inspected_monotonic_ns=1_000_000_000)
        self.owner = dict(frontend_pid=12, start_ticks='34')
        self.original = copy.deepcopy(self.gate)
        self.calls = []
        self.sleeping = 1
        self.changed = False
        self.frame_age = 0
        self.diag_age = 0
        self.capture_duration = 100_000_000
        self.viewport = (480, 800)
        self.viewport_calls = 0
        self.resize = False
        self.captured_sleeping = False

    def status(self):
        return dict(state=2, sleeping=self.sleeping, agent=1, backlight=245,
                    observed_monotonic_ns=self.clock)

    def pulse(self):
        self.calls.append('power150ms-balanced')
        self.clock += 150_000_000
        self.sleeping = 0

    def capture(self, deadline):
        self.assertEqual(deadline, self.capture_deadline)
        self.calls.append('retained-capture')
        self.clock += self.capture_duration
        receipt = dict(observed_monotonic_ns=self.clock, sleeping=int(self.captured_sleeping),
                       agent=1, backlight=245,
                       frame_monotonic_ns=self.clock - self.frame_age,
                       diagnostics_monotonic_ns=self.clock - self.diag_age)
        return (b'changed-clock-pixels' if self.changed else b'full-BMP'), receipt, 'saved'

    def window(self):
        self.viewport_calls += 1
        if self.resize and self.viewport_calls > 1:
            return (800, 480)
        return self.viewport

    def wait(self, seconds):
        self.clock += int(seconds * 1e9)

    def admit(self):
        self.capture_deadline = self.clock + policy.CAPTURE_BUDGET_NS
        return policy.admit_rewake(self.gate, b'full-BMP', self.owner,
                                   now=lambda: self.clock, status=self.status,
                                   pulse=self.pulse, capture=self.capture,
                                   verify_window=self.window, wait=self.wait)

    def test_sleeping_rewakes_once_identical_pixels_preserves_inspection(self):
        result = self.admit()
        self.assertEqual(self.calls, ['power150ms-balanced', 'retained-capture'])
        self.assertEqual(self.gate, self.original)
        self.assertEqual(result['original_inspected_monotonic_ns'], 1_000_000_000)
        self.assertFalse(result['guest_acceptance'])

    def test_awake_does_not_pulse(self):
        self.sleeping = 0
        self.assertFalse(self.admit()['pulsed'])
        self.assertNotIn('power150ms-balanced', self.calls)

    def test_changed_clock_or_any_bytes_require_new_inspection(self):
        self.changed = True
        with self.assertRaisesRegex(policy.Rejected, 'BMP changed'):
            self.admit()
        self.assertIn('retained-capture', self.calls)

    def test_inspection_expired_or_future_never_pulses(self):
        for timestamp in (-41_000_000_000, self.clock + 1):
            with self.subTest(timestamp=timestamp):
                self.gate['inspected_monotonic_ns'] = timestamp
                with self.assertRaisesRegex(policy.Rejected, '60s'):
                    self.admit()
                self.assertEqual(self.calls, [])

    def test_age_crosses_limit_during_capture(self):
        self.clock = 60_850_000_000
        with self.assertRaisesRegex(policy.Rejected, '60s'):
            self.admit()
        self.assertIn('retained-capture', self.calls)

    def test_stale_source_or_diagnostics_cannot_admit(self):
        for attr, age in [('frame_age', 2_000_000_001), ('diag_age', 1_000_000_001)]:
            with self.subTest(field=attr):
                setattr(self, attr, age)
                with self.assertRaisesRegex(policy.Rejected, 'stale'):
                    self.admit()
                setattr(self, attr, 0)

    def test_sleeping_captures_bounded_without_repeated_power(self):
        self.captured_sleeping = True
        with self.assertRaisesRegex(policy.Rejected, '12s'):
            self.admit()
        self.assertEqual(self.calls.count('power150ms-balanced'), 1)

    def test_slow_capture_or_resized_viewport_reject(self):
        self.capture_duration = 12_000_000_001
        with self.assertRaisesRegex(policy.Rejected, '12s'):
            self.admit()
        self.setUp()
        self.resize = True
        with self.assertRaisesRegex(policy.Rejected, 'viewport changed'):
            self.admit()

    def test_owner_and_scene_assertions_cannot_be_inferred(self):
        for key, value in [('start_ticks', 'reused'), ('model_inspected', False), ('scene', 'Home Screen')]:
            with self.subTest(field=key):
                self.gate[key] = value
                with self.assertRaises(policy.Rejected):
                    self.admit()
                self.gate = copy.deepcopy(self.original)
                self.assertEqual(self.calls, [])

    def test_stale_initial_status_does_not_pulse(self):
        status = self.status
        self.status = lambda: dict(status(), observed_monotonic_ns=self.clock - 1_000_000_001)
        with self.assertRaisesRegex(policy.Rejected, 'current status'):
            self.admit()
        self.assertEqual(self.calls, [])

    def test_display_changes_after_fresh_capture_rejects_gesture(self):
        status = self.status
        self.status = lambda: dict(status(), sleeping=1 if 'retained-capture' in self.calls else 0)
        with self.assertRaisesRegex(policy.Rejected, 'changed before gesture'):
            self.admit()
        self.assertEqual(self.calls, ['retained-capture'])


if __name__ == '__main__':
    unittest.main()
