#!/usr/bin/env python3
"""Host-only policy tests using two retained model-vetted BMPs; no guest writes."""
import argparse
import hashlib
import importlib.util
from pathlib import Path
import struct
import subprocess
import sys
import unittest

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--original', type=Path, required=True)
parser.add_argument('--current', type=Path, required=True)
parser.add_argument('--series', type=Path, help='Actual A03 continuity directory;23caret-only positives plus clock24negative')
args, rest = parser.parse_known_args()
spec = importlib.util.spec_from_file_location('gates', Path(__file__).with_name('watch-visual-gates.py'))
gates = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gates)
ORIGINAL, CURRENT = args.original.read_bytes(), args.current.read_bytes()
ORIGINAL_HASH, CURRENT_HASH = hashlib.sha256(ORIGINAL).hexdigest(), hashlib.sha256(CURRENT).hexdigest()
SCENE = 'Notes focused new editor'


def offset(data, x, y):
    width, height = struct.unpack_from('<ii', data, 18)
    return 54 + ((abs(height) - 1 - y if height > 0 else y) * width + x) * 4


def mutate(data, x, y, channel=0):
    value = bytearray(data)
    value[offset(data, x, y) + channel] ^= 1
    return bytes(value)


class EditorCaretTests(unittest.TestCase):
    def check_pair(self, old, new, **kwargs):
        return gates.image_continuity(old, new, SCENE, **kwargs)

    def test_actual_pair_has_explicit_66pixel_RGB_tolerance(self):
        for old, new in [(ORIGINAL, CURRENT), (CURRENT, ORIGINAL)]:
            result = self.check_pair(old, new, blank_focused_editor=True)
            self.assertTrue(result['matched'])
            self.assertFalse(result['full_BMP_identical'])
            self.assertEqual(result['policy'], 'n72-explicit-blank-editor-caret-RGB-v2')
            self.assertEqual(result['protected_original_sha256'], result['protected_current_sha256'])
            self.assertEqual(result['tolerated_RGB_pixels'],66)
            self.assertTrue(result['no_caret_shape_or_color_recognition'])
            self.assertTrue(result['all_alpha_protected'])

    def test_same_full_image_keeps_original_behavior(self):
        self.assertTrue(self.check_pair(ORIGINAL, ORIGINAL)['full_BMP_identical'])

    def test_explicit_blank_assertion_required(self):
        self.assertFalse(self.check_pair(ORIGINAL, CURRENT)['matched'])
        self.assertFalse(self.check_pair(ORIGINAL, CURRENT, blank_focused_editor=1)['matched'])

    def test_written_saved_and_home_do_not_get_caret_exception(self):
        for scene in ['Notes written marker', 'Notes saved marker', 'Home Screen']:
            self.assertFalse(gates.image_continuity(ORIGINAL, CURRENT, scene, True)['matched'])

    def test_RGB_inside_exact_ROI_is_explicitly_tolerated_not_recognized(self):
        for c in range(3):
            self.assertTrue(self.check_pair(ORIGINAL, mutate(CURRENT,33,110,c),blank_focused_editor=True)['matched'])

    def test_tolerance_is_symmetric_and_RGB_only(self):
        self.assertTrue(self.check_pair(mutate(ORIGINAL,33,110),CURRENT,blank_focused_editor=True)['matched'])
        self.assertFalse(self.check_pair(mutate(ORIGINAL,33,110,3),CURRENT,blank_focused_editor=True)['matched'])

    def test_every_boundary_and_other_body_pixel_protected(self):
        for x, y in [(31, 110), (35, 110), (33, 103), (33, 126), (40, 110), (80, 180)]:
            with self.subTest(x=x, y=y):
                self.assertFalse(self.check_pair(ORIGINAL, mutate(CURRENT, x, y), blank_focused_editor=True)['matched'])

    def test_editor_clock_and_keyboard_stay_exact(self):
        for x, y in [(145, 5), (100, 40), (50, 300), (160, 450)]:
            self.assertFalse(self.check_pair(ORIGINAL, mutate(CURRENT, x, y), blank_focused_editor=True)['matched'])

    def test_alpha_inside_and_outside_caret_protected(self):
        for x, y in [(33, 110), (150, 200)]:
            self.assertFalse(self.check_pair(ORIGINAL, mutate(CURRENT, x, y, 3), blank_focused_editor=True)['matched'])

    def test_header_byte_protected(self):
        changed = bytearray(CURRENT); changed[38] ^= 1
        self.assertFalse(self.check_pair(ORIGINAL, bytes(changed), blank_focused_editor=True)['matched'])

    def test_matching_but_invalid_dimensions_rejected(self):
        old, new = bytearray(ORIGINAL), bytearray(CURRENT)
        for data in [old, new]: struct.pack_into('<i', data, 18, 319)
        self.assertFalse(self.check_pair(bytes(old), bytes(new), blank_focused_editor=True)['matched'])

    def test_wrong_pixel_format_rejected(self):
        old, new = bytearray(ORIGINAL), bytearray(CURRENT)
        for data in [old, new]: struct.pack_into('<H', data, 28, 24)
        self.assertFalse(self.check_pair(bytes(old), bytes(new), blank_focused_editor=True)['matched'])

    def test_moved_caret_rejected(self):
        changed = bytearray(CURRENT)
        for y in range(104, 126):
            a = offset(ORIGINAL, 32, y); b = offset(CURRENT, 33, y)
            changed[b:b + 12] = ORIGINAL[a:a + 12]
        self.assertFalse(self.check_pair(ORIGINAL, bytes(changed), blank_focused_editor=True)['matched'])

    def test_cropped_black_and_partial_images_rejected(self):
        for data in [CURRENT[:-4], bytes(len(CURRENT)), CURRENT[:54] + bytes(len(CURRENT) - 54)]:
            self.assertFalse(self.check_pair(ORIGINAL, data, blank_focused_editor=True)['matched'])

    def test_actual_A03_continuum_and_clock_rejection(self):
        if args.series is None:self.skipTest('actual A03 series not supplied')
        paths=sorted(args.series.glob('attempt-*/native.bmp'))
        self.assertEqual(len(paths),24)
        for i,path in enumerate(paths,1):
            result=self.check_pair(ORIGINAL,path.read_bytes(),blank_focused_editor=True)
            self.assertEqual(result['matched'],i<24,path.parent.name)

    def test_equal_invalid_blank_BMP_still_requires_shape_validation(self):
        for data in (b'bad',ORIGINAL[:-1]):
            self.assertFalse(self.check_pair(data,data,blank_focused_editor=True)['matched'])

    def test_inputs_unchanged(self):
        self.assertEqual(hashlib.sha256(args.original.read_bytes()).hexdigest(), ORIGINAL_HASH)
        self.assertEqual(hashlib.sha256(args.current.read_bytes()).hexdigest(), CURRENT_HASH)

    def test_actual_recorder_refuses_assertion_on_marker_scene(self):
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('record-inspected-gate.py')),
                                 '--run', '/nonexistent-runtime-test', '--capture', '/nonexistent-runtime-test/image',
                                 '--scene', 'written-marker', '--output', 'test', '--blank-focused-editor'],
                                capture_output=True, text=True, timeout=2)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('restricted to the editor scene', result.stderr)


if __name__ == '__main__':
    unittest.main(argv=[__file__, *rest])
