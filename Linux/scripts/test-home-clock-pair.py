#!/usr/bin/env python3
"""Offline comparison of retained actual Home BMPs; never writes guest evidence."""
import argparse
import importlib.util
from pathlib import Path
import struct
import unittest

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--original', type=Path, required=True)
parser.add_argument('--current', type=Path, required=True)
args, rest = parser.parse_known_args()
spec = importlib.util.spec_from_file_location('gates', Path(__file__).with_name('watch-visual-gates.py'))
gates = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gates)
ORIGINAL, CURRENT = args.original.read_bytes(), args.current.read_bytes()


def mutate(data, x, y, channel=0):
    data = bytearray(data)
    height = struct.unpack_from('<i', data, 22)[0]
    data[54 + ((y if height < 0 else 479-y) * 320 + x) * 4 + channel] ^= 1
    return bytes(data)


class ActualHomeClock(unittest.TestCase):
    def check(self, current):
        return gates.image_continuity(ORIGINAL, current, 'Home Screen')['matched']

    def test_actual_retained_A04_pair_matches_prospectively(self):
        self.assertNotEqual(ORIGINAL, CURRENT)
        self.assertTrue(self.check(CURRENT))
        self.assertEqual(gates.HOME_CLOCK_BOX, (129, 1, 190, 19))

    def test_left_and_bottom_adjacent_pixels_reject(self):
        for x, y in ((128, 5), (129, 19)):
            for channel in range(4):
                self.assertFalse(self.check(mutate(CURRENT, x, y, channel)), (x, y, channel))

    def test_every_clock_alpha_byte_remains_protected(self):
        for y in range(1, 19):
            for x in range(129, 190):
                self.assertFalse(self.check(mutate(CURRENT, x, y, 3)), (x, y))

    def test_icon_and_dock_changes_reject(self):
        for x, y in ((45, 60), (150, 240), (40, 420)):
            for channel in range(4):
                self.assertFalse(self.check(mutate(CURRENT, x, y, channel)), (x, y, channel))

    def test_written_and_saved_marker_do_not_get_Home_clock_exception(self):
        for scene in ('Notes written marker', 'Notes saved marker', 'Notes focused new editor'):
            self.assertFalse(gates.image_continuity(ORIGINAL, CURRENT, scene)['matched'])


if __name__ == '__main__':
    unittest.main(argv=[__file__] + rest)
