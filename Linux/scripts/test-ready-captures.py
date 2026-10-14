#!/usr/bin/env python3
"""Offline capture readiness regressions; no X display, engine or guest."""
import importlib.util
from pathlib import Path
import struct
import unittest

spec = importlib.util.spec_from_file_location('ready', Path(__file__).with_name('watch-ready-captures.py'))
ready = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ready)
NOW = 100 * ready.SECOND


def frame(seq=2, now=NOW):
    return f'trace_frame observed_monotonic_ns={now} sequence={seq} source_monotonic_ns={now - 1000000}'


def status(now=NOW, sleeping=0, agent=1, backlight=1746):
    return (f'status observed_monotonic_ns={now} state=2 sleeping={sleeping} '
            f'backlight={backlight} agent={agent}')


def gate():
    g = ready.Readiness()
    g.ingest('admission hello_verified_observed_monotonic_ns=1 worker_pid=123; guest readiness still pending')
    g.ingest(frame())
    g.ingest(status())
    return g


def bitmap(pixel):
    b = bytearray(58)
    b[:2] = b'BM'
    struct.pack_into('<I', b, 2, len(b))
    struct.pack_into('<I', b, 10, 54)
    struct.pack_into('<IiiHHI', b, 14, 40, 1, -1, 1, 32, 0)
    b[54:] = pixel
    return bytes(b)


class CaptureTests(unittest.TestCase):
    def test_split_lines_are_not_lost_or_admitted_early(self):
        tail = ready.CompleteLines()
        text = (frame() + '\n' + status() + '\n').encode()
        cut = text.index(b'status') + 13
        self.assertEqual(tail.feed(text[:cut]), [frame()])
        self.assertEqual(tail.feed(text[cut:]), [status()])
        self.assertEqual(tail.pending, b'')
        self.assertEqual(tail.feed(b'x' * 8193), [])
        self.assertEqual(tail.feed(b'status hidden tail\n' + text), [frame(), status()])
        self.assertEqual(tail.discarded, 1)

    def test_hello_and_current_frame_status_are_both_required(self):
        g = ready.Readiness()
        g.ingest(frame()); g.ingest(status())
        self.assertIsNone(g.candidate(NOW))
        self.assertIsNotNone(gate().candidate(NOW))
        self.assertIsNone(gate().candidate(NOW + ready.SECOND))
        self.assertIsNone(gate().candidate(NOW - 1))
        for line in [frame(seq=1), status(sleeping=1), status(agent=0), status(backlight=0),
                     status(now=NOW + 1), 'status observed_monotonic_ns=oops']:
            g = gate(); g.ingest(line)
            self.assertIsNone(g.candidate(NOW), line)

    def test_unique_sequences_and_two_hz_cap(self):
        g = gate(); g.attempt(g.candidate(NOW))
        self.assertIsNone(g.candidate(NOW))
        g.ingest(frame(seq=3, now=NOW + 1000000)); g.ingest(status(now=NOW + 1000000))
        self.assertIsNone(g.candidate(NOW + 1000000))
        later = NOW + ready.SECOND // 2
        g.ingest(frame(seq=3, now=later)); g.ingest(status(now=later))
        self.assertIsNotNone(g.candidate(later))
        g.last_attempt = 5  # Actual capture can advance beyond the trigger's frame.
        self.assertIsNone(g.candidate(later))

    def test_rgb_excludes_unused_alpha_and_bounds_are_checked(self):
        self.assertFalse(ready.native_bitmap_nonblack(bitmap(b'\0\0\0\xff')))
        self.assertTrue(ready.native_bitmap_nonblack(bitmap(b'\0\x01\0\0')))
        for b in [b'bad', bitmap(b'\0\0\0\0')[:-1]]:
            with self.assertRaises(ValueError): ready.native_bitmap_nonblack(b)
        b = bytearray(bitmap(b'\0\0\0\0'))
        struct.pack_into('<i', b, 22, 1)
        with self.assertRaises(ValueError): ready.native_bitmap_nonblack(b)

    def test_capture_receipt_can_reject_a_ready_trigger(self):
        candidate = gate().candidate(NOW)
        r = {'observed_monotonic_ns': str(NOW + 1000000), 'frame_monotonic_ns': str(NOW),
             'diagnostics_monotonic_ns': str(NOW), 'sequence': '2', 'sleeping': '0',
             'agent': '1', 'backlight': '1746'}
        self.assertTrue(all(ready.capture_flags(r, bitmap(b'\x01\0\0\0'), candidate).values()))
        self.assertFalse(ready.capture_flags(r, bitmap(b'\0\0\0\xff'), candidate)['rgb_nonzero'])
        for field, bad, flag in [('sleeping', '1', 'sleeping_zero'), ('agent', '0', 'agent_alive'),
                                 ('frame_monotonic_ns', str(NOW - 2 * ready.SECOND), 'fresh_frame'),
                                 ('diagnostics_monotonic_ns', str(NOW + 2000000), 'fresh_diagnostics'),
                                 ('sequence', '1', 'sequence_at_least_trigger')]:
            modified = dict(r); modified[field] = bad
            self.assertFalse(ready.capture_flags(modified, bitmap(b'\x01\0\0\0'), candidate)[flag])


if __name__ == '__main__':
    unittest.main()
