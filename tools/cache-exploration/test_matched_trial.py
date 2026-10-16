"""Synthetic scheduler/receipt checks; no guest, GUI, compiler or native engine."""
import tempfile
from pathlib import Path
import unittest

import matched_trial as matched
from exploration_common import SECOND, save


class Tests(unittest.TestCase):
    hello = 1000 * SECOND

    def cfg(self):
        return {'cacheTrial': {'mode': 'cold'}, 'matchedDiagnostic': {'mode': matched.MODE}}

    def scenes(self, root):
        matched.input_claim(True, root, self.hello, 'home-unlock', self.hello + 70 * SECOND)
        save(root / 'home-inspected.json', {'model_inspected': True})
        save(root / 'settings-input.json', {'input_begin_ns': self.hello + 105 * SECOND})
        save(root / 'settings-inspected.json', {'observed_wifi_name': 'qemu-ios'})

    def rows(self, change=None):
        result = []
        for second in range(120, 136):
            fields = dict(observed_monotonic_ns=self.hello + second * SECOND,
                          state=2, agent=1, shutdown_phase=0, sleeping=0, backlight=255)
            if change and second == 127:
                fields.update(change)
            result.append('status ' + ' '.join(str(k) + '=' + str(v) for k, v in fields.items()))
        return result

    def test_default_preserves_old_mode_and_invalid_mode_rejected(self):
        self.assertFalse(matched.enabled({}))
        tracker = matched.Tracker({})
        self.assertIsNone(tracker.observe(None, None, 0, []))
        self.assertIsNone(tracker.finish(None, None, None))
        for cfg in [dict(matchedDiagnostic=None), dict(matchedDiagnostic={'mode': 'other'}),
                    dict(self.cfg(), cacheTrial={'mode': 'reuse'})]:
            with self.assertRaises(RuntimeError):
                matched.enabled(cfg)

    def test_inputs_have_original_clock_window_exact_pixel_and_one_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for now in [self.hello + 70 * SECOND - 1, self.hello + 71 * SECOND + 1]:
                with self.assertRaises(RuntimeError):
                    matched.input_claim(True, root, self.hello, 'home-unlock', now)
            self.assertEqual(list(root.iterdir()), [])
            matched.input_claim(True, root, self.hello, 'home-unlock', self.hello + 70 * SECOND)
            with self.assertRaises(FileExistsError):
                matched.input_claim(True, root, self.hello, 'home-unlock', self.hello + 70 * SECOND)
            with self.assertRaises(RuntimeError):
                matched.input_claim(True, root, self.hello, 'tap-settings', self.hello + 105 * SECOND, [1, 2])
            self.assertFalse((root / 'matched-tap-settings-input.json').exists())

    def test_personal_wifi_report_and_scene_deadlines_required(self):
        for scene, now, wifi in [('home', 96, None), ('settings', 121, 'qemu-ios'),
                                  ('settings', 119, 'Not Connected'), ('settings', 119, None)]:
            with self.assertRaises(RuntimeError):
                matched.attest(True, scene, {}, self.hello, self.hello + now * SECOND, wifi)
        value = matched.attest(True, 'settings', {}, self.hello, self.hello + 119 * SECOND, 'qemu-ios')
        self.assertEqual(value['observed_wifi_name'], 'qemu-ios')
        self.assertEqual(matched.attest(False, 'settings', {}, 0, 0), {})

    def test_complete_schedule_keeps_settings_running_until_original_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.scenes(root); tracker = matched.Tracker(self.cfg())
            self.assertIsNone(tracker.observe(root, self.hello, self.hello + 135 * SECOND, self.rows()))
            self.assertTrue(tracker.ready)
            self.assertEqual(tracker.observe(root, self.hello, self.hello + 180 * SECOND, self.rows()),
                             'matched scheduled Stop180s')
            result = tracker.finish(root, self.hello, self.hello + 180 * SECOND)
            self.assertFalse(result['causality_established'])
            self.assertFalse(result['performance_qualified'])

    def test_readiness_rejects_bad_state_and_missing_or_gapped_coverage(self):
        cases = [[], self.rows()[2:], self.rows()[:-2], self.rows()[:4] + self.rows()[8:]]
        cases.extend(self.rows({key: value}) for key, value in
                     [('state', 4), ('agent', 2), ('shutdown_phase', 1), ('sleeping', 1), ('backlight', 0)])
        for rows in cases:
            with self.subTest(rows=len(rows)), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); self.scenes(root); tracker = matched.Tracker(self.cfg())
                with self.assertRaises(RuntimeError):
                    tracker.observe(root, self.hello, self.hello + 135 * SECOND, rows)

    def test_changed_evidence_or_hello_and_early_stop_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.scenes(root); tracker = matched.Tracker(self.cfg())
            tracker.observe(root, self.hello, self.hello + 135 * SECOND, self.rows())
            for stop in [self.hello + 179 * SECOND, self.hello + 181 * SECOND + 1, None]:
                with self.assertRaises(RuntimeError):
                    tracker.finish(root, self.hello, stop)
            with self.assertRaises(RuntimeError):
                tracker.observe(root, self.hello + 1, self.hello + 180 * SECOND, self.rows())
            save(root / 'settings-inspected.json', {'observed_wifi_name': 'changed'})
            with self.assertRaises(RuntimeError):
                tracker.finish(root, self.hello, self.hello + 180 * SECOND)

    def test_missing_actual_input_or_unmatched_network_cannot_finish(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.scenes(root)
            save(root / 'settings-input.json', {'input_begin_ns': self.hello + 107 * SECOND})
            with self.assertRaises(RuntimeError):
                matched.Tracker(self.cfg()).observe(root, self.hello, self.hello + 135 * SECOND, self.rows())
            save(root / 'settings-input.json', {'input_begin_ns': self.hello + 105 * SECOND})
            save(root / 'settings-inspected.json', {'observed_wifi_name': 'Not Connected'})
            with self.assertRaises(RuntimeError):
                matched.Tracker(self.cfg()).observe(root, self.hello, self.hello + 135 * SECOND, self.rows())


if __name__ == '__main__':
    unittest.main()
