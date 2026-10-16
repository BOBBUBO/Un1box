"""Host-only synthetic Surface/policy checks, never guest acceptance."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

import observed_lock as observed


def policy():
    path = Path(__file__).resolve().parents[2] / 'Linux/scripts/lock-continuity.py'
    spec = importlib.util.spec_from_file_location('shared_lock_policy', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SurfaceAnchors(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.original = Path(self.temporary.name) / 'original.png'
        self.current = Path(self.temporary.name) / 'current.png'
        self.view = [44, 787, 1036, 2048]
        # Deliberately synthetic; no image is labelled or inferred to be Lock.
        Image.new('RGBA', (1080, 2400), (10, 20, 30, 255)).save(self.original)

    def changed(self, point):
        from PIL import Image
        left, top, scale = observed.fit(self.view)
        with Image.open(self.original) as image:
            image.putpixel((round(left + point[0] * scale), round(top + point[1] * scale)),
                           (11, 20, 30, 255))
            image.save(self.current)
        return observed.compare_surface(self.original, self.current, self.view, policy())

    def test_clock_and_caption_pixels_are_outside_exact_anchors(self):
        for point in ((100, 50), (180, 435)):
            result = self.changed(point)
            self.assertTrue(result['matched'])
            self.assertTrue(result['noNewSceneAsserted'])
            self.assertFalse(result['guestAcceptance'])

    def test_each_protected_anchor_rejects_one_changed_actual_surface_pixel(self):
        for point in ((150, 250), (40, 430), (155, 9)):
            self.assertFalse(self.changed(point)['matched'])

    def test_invalid_view_and_outside_surface_reject(self):
        for view in ([0, 0, 0, 1], [-1, 0, 10, 10], [0., 0, 10, 10], [0, 0, 20]):
            with self.assertRaises(ValueError):
                observed.fit(view)
        with self.assertRaises(ValueError):
            observed.compare_surface(self.original, self.original, [0, 0, 5000, 5000], policy())


class WakeAdmission(unittest.TestCase):
    def setUp(self):
        self.identity = {'worker': {'pid': 7, 'startTicks': '8', 'uid': 9}, 'boot': 'synthetic'}
        self.gate = observed.WakeReadiness(20_000_000_000, 100_000_000_000, 40,
                                           self.identity, [44, 787, 1036, 2048], 0)
        self.capture = dict(identity=self.identity, rotation=0, view=[44, 787, 1036, 2048],
                            state='running', sleeping=False, captureEndNs=21_000_000_000,
                            producedFrames=41, anchorsMatched=True)

    def test_exact_anchors_without_new_producer_frame_are_insufficient(self):
        self.assertFalse(self.gate.observe(dict(self.capture, producedFrames=40), 21_000_000_001))
        self.assertTrue(self.gate.observe(self.capture, 21_000_000_001))

    def test_missing_or_ambiguous_ui_counter_rejects(self):
        self.assertEqual(observed.producer_frames(['contacts 5; frames 42; writable state dirty']), 42)
        for texts in ([], ['frames unavailable'], ['frames 4', 'frames 5']):
            with self.assertRaises(ValueError):
                observed.producer_frames(texts)

    def test_sleep_layout_or_bad_anchors_never_admit(self):
        for fields in (dict(sleeping=True), dict(view=[44, 853, 1036, 2048]),
                       dict(rotation=1), dict(state='paused'), dict(anchorsMatched=False)):
            self.assertFalse(self.gate.observe(dict(self.capture, **fields), 21_000_000_001))

    def test_identity_stale_future_or_counter_regression_reject(self):
        for fields in (dict(identity={}), dict(captureEndNs=1),
                       dict(captureEndNs=22_000_000_000), dict(producedFrames=39)):
            with self.assertRaises(ValueError):
                self.gate.observe(dict(self.capture, **fields), 21_000_000_001)

    def test_original_parent_deadline_wins_and_cannot_be_renewed(self):
        parent = observed.WakeReadiness(20_000_000_000, 21_000_000_000, 40,
                                        self.identity, [44, 787, 1036, 2048], 0)
        with self.assertRaisesRegex(ValueError, 'deadline'):
            parent.observe(self.capture, 21_000_000_001)
        self.assertEqual(self.gate.deadline_ns, 35_000_000_000)
        with self.assertRaisesRegex(ValueError, 'deadline'):
            self.gate.observe(self.capture, 35_000_000_001)


if __name__ == '__main__':
    unittest.main(verbosity=2)
