#!/usr/bin/env python3
"""Target-free stable-anchor and real admission-policy checks; no guest proof."""
import importlib.util
from pathlib import Path
import struct
import unittest


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


roi = load('lock-continuity')
policy = load('inspected-continuity')


def image():
    data = bytearray(54 + 320 * 480 * 4)
    data[:2] = b'BM'
    struct.pack_into('<I', data, 2, len(data))
    struct.pack_into('<I', data, 10, 54)
    struct.pack_into('<IiiHHII', data, 14, 40, 320, -480, 1, 32, 0, 320 * 480 * 4)
    # Host-only texture fixture, deliberately not an image claimed to be Lock.
    for i in range(54, len(data)):
        data[i] = i % 251
    return bytes(data)


def changed(data, x, y):
    value = bytearray(data)
    value[54 + (y * 320 + x) * 4] ^= 1
    return bytes(value)


class LockContinuity(unittest.TestCase):
    def setUp(self):
        self.original = image()

    def test_clock_and_shimmer_changes_do_not_change_the_inspected_anchors(self):
        current = changed(changed(self.original, 100, 50), 180, 435)
        result = roi.compare(self.original, current)
        self.assertTrue(result['matched'])
        self.assertFalse(result['full_BMP_identical'])
        self.assertTrue(result['no_new_scene_asserted'])
        self.assertFalse(result['guest_acceptance'])
        self.assertEqual([v['name'] for v in result['regions']], ['earth', 'slider_knob', 'lock_symbol'])

    def test_any_changed_protected_anchor_pixel_rejects(self):
        for x, y in ((150, 250), (40, 430), (155, 9)):
            self.assertFalse(roi.compare(self.original, changed(self.original, x, y))['matched'])

    def test_wrong_shape_format_offset_truncated_or_extra_data_rejects(self):
        self.assertFalse(roi.compare(self.original, self.original[:-1])['matched'])
        self.assertFalse(roi.compare(self.original, self.original + b'x')['matched'])
        for offset, format, value in ((18, '<i', 321), (22, '<i', -479), (28, '<H', 24),
                                      (30, '<I', 1), (10, '<I', 55), (2, '<I', 1)):
            current = bytearray(self.original)
            struct.pack_into(format, current, offset, value)
            self.assertFalse(roi.compare(self.original, bytes(current))['matched'])

    def admit(self, images, *, stale=False, resize=False):
        clock = [20_000_000_000]
        calls = []
        gate = dict(model_inspected=True, scene='Lock Screen', frontend_pid=12,
                    start_ticks='34', inspected_monotonic_ns=1_000_000_000)
        owner = dict(frontend_pid=12, start_ticks='34')
        index = [0]

        def capture(deadline):
            clock[0] += 10_000_000
            current = images[min(index[0], len(images) - 1)]
            index[0] += 1
            calls.append('retained-capture')
            return current, dict(observed_monotonic_ns=clock[0], frame_monotonic_ns=clock[0] - (3_000_000_000 if stale else 0),
                                 diagnostics_monotonic_ns=clock[0], sleeping=0, agent=1, backlight=245), str(index[0])

        def window():
            return (800, 480) if resize and index[0] else (480, 800)

        result = policy.admit_rewake(
            gate, self.original, owner, now=lambda: clock[0],
            status=lambda: dict(state=2, sleeping=0, agent=1, backlight=245, observed_monotonic_ns=clock[0]),
            pulse=lambda: calls.append('UNEXPECTED-POWER'), capture=capture,
            verify_window=window, wait=lambda seconds: clock.__setitem__(0, clock[0] + int(seconds * 1e9)),
            compare_scene=roi.compare)
        self.assertEqual(gate['inspected_monotonic_ns'], 1_000_000_000)
        self.assertNotIn('UNEXPECTED-POWER', calls)
        return result, calls, clock[0]

    def test_actual_policy_admits_changed_clock_without_new_scene_or_backdate(self):
        result, calls, clock = self.admit([changed(self.original, 100, 50)])
        self.assertTrue(result['comparison']['matched'])
        self.assertFalse(result['byte_identical'])
        self.assertFalse(result['guest_acceptance'])

    def test_transitional_anchor_mismatch_retained_then_exact_match_admits(self):
        result, calls, clock = self.admit([changed(self.original, 40, 430), self.original])
        self.assertEqual(calls, ['retained-capture', 'retained-capture'])
        self.assertEqual(result['saved_capture'], '2')
        self.assertGreaterEqual(clock, 20_500_000_000)

    def test_persistently_different_anchor_rejects_within_capture_budget(self):
        with self.assertRaisesRegex(policy.Rejected, '12s'):
            self.admit([changed(self.original, 150, 250)])

    def test_matching_anchors_still_require_fresh_source_and_same_viewport(self):
        with self.assertRaisesRegex(policy.Rejected, 'stale'):
            self.admit([self.original], stale=True)
        with self.assertRaisesRegex(policy.Rejected, 'viewport changed'):
            self.admit([self.original], resize=True)


if __name__ == '__main__':
    unittest.main()
