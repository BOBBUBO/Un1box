#!/usr/bin/env python3
"""Offline capture readiness regressions; no X display, engine or guest."""
import importlib.util
from pathlib import Path
import struct
import unittest
import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
from unittest.mock import patch

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

    def test_qualified_second_preview_requires_closed_writer_and_complete_hashed_bytes(self):
        identity = {'frontend_pid': 12, 'start_ticks': '34', 'worker_pid': 13,
                    'worker_start_ticks': '35', 'window': 42}
        handoff = dict(capture_writer_exited=True, watcher_returncode=0, identity=identity)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for number, pixel in ((1, b'\0\0\0\xff'), (2, b'\x01\0\0\0')):
                p = root / f'capture-{number:02d}'; p.mkdir()
                data = bitmap(pixel); (p / 'native.bmp').write_bytes(data)
                (p / 'native.png').write_bytes(b'\x89PNG\r\n\x1a\nHOST_FIXTURE\0\0\0\0IEND\xaeB`\x82')
                receipt = dict(observed_monotonic_ns=NOW, frame_monotonic_ns=NOW,
                               diagnostics_monotonic_ns=NOW, sequence=2, sleeping=0,
                               agent=1, backlight=245, coherent_readback=1, shared_generation=4)
                (p / 'receipt.json').write_text(json.dumps(dict(receipt=receipt,
                    candidate=gate().candidate(NOW), copied_monotonic_ns=NOW,
                    sha256=hashlib.sha256(data).hexdigest())))
            selected = root / 'capture-02/native.png'
            handoff['preview_sha256'] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                        for p in root.glob('capture-*/native.png')}
            self.assertEqual(ready.ready_preview(root, handoff, identity, NOW), selected)
            self.assertIsNone(ready.ready_preview(root, dict(handoff, capture_writer_exited=False), identity, NOW))
            self.assertIsNone(ready.ready_preview(root, dict(handoff, watcher_returncode=1), identity, NOW))
            self.assertIsNone(ready.ready_preview(root, handoff, dict(identity, start_ticks='reused'), NOW))
            self.assertIsNone(ready.ready_preview(root, handoff, identity, NOW + 3 * ready.SECOND))
            self.assertIsNone(ready.ready_preview(root, handoff, identity, NOW - 1))
            selected.write_bytes(b'\x89PNG partial')
            self.assertIsNone(ready.ready_preview(root, handoff, identity, NOW))
            selected.write_bytes(b'\x89PNG\r\n\x1a\nCHANGED_FIXTURE\0\0\0\0IEND\xaeB`\x82')
            self.assertIsNone(ready.ready_preview(root, handoff, identity, NOW))
            selected.write_bytes(b'\x89PNG\r\n\x1a\nHOST_FIXTURE\0\0\0\0IEND\xaeB`\x82')
            (selected.parent / 'native.bmp').write_bytes(bitmap(b'\x02\0\0\0'))
            self.assertIsNone(ready.ready_preview(root, handoff, identity, NOW))
            (selected.parent / 'receipt.json').write_text('{partial')
            self.assertIsNone(ready.ready_preview(root, handoff, identity, NOW))

    def run_capture_main(self, continuous, black_first, explicit_stop=False, timeout=1.2):
        # Actual producer main, real pidfd and stdout identity; only XTest/frame
        # production and clock are host fixtures. No engine or guest is loaded.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); log = root / 'stdout'; native = root / 'native.bmp'
            clock = [NOW]; captures = []
            def frame_status(seq, timestamp):
                return frame(seq, timestamp) + '\n' + status(timestamp) + '\n'
            log.write_text('admission hello_verified_observed_monotonic_ns=1\n' + frame_status(2, NOW))
            with log.open('a') as stream:
                argv = [sys.executable, '-c', 'import time; time.sleep(20)', '--engine',
                        'HOST_NOT_LOADED', '--screenshot', str(native)]
                child = subprocess.Popen(argv, stdout=stream, env=dict(os.environ, DISPLAY=':host-fixture'))
                try:
                    # Wait only for exec to publish the actual fixture argv.
                    import time
                    until = time.monotonic() + 1
                    while ready.process_identity(child.pid)['argv'] != argv and time.monotonic() < until:
                        time.sleep(.005)
                    ticks = ready.process_identity(child.pid)['start_ticks']
                    def command(args, **kwargs):
                        if args[1] == 'getwindowpid': output = str(child.pid)
                        elif args[1] == 'getwindowfocus': output = '42'
                        else: output = ''
                        if args[1:] == ['key', 's']:
                            captures.append(clock[0]); seq = len(captures) + 1
                            black = black_first and len(captures) == 1
                            native.write_bytes(bitmap(b'\0\0\0\xff' if black else b'\x01\0\0\0'))
                            with log.open('a') as f:
                                f.write(f'screenshot sequence={seq} observed_monotonic_ns={clock[0]} '
                                        f'frame_monotonic_ns={clock[0]} diagnostics_monotonic_ns={clock[0]} '
                                        f'sleeping={int(black)} backlight={0 if black else 245} agent=1 path={native}\n')
                                f.write(frame_status(seq + 1, clock[0] + ready.SECOND // 2))
                        return subprocess.CompletedProcess(args, 0, output, '')
                    options = ['watch', '--pid', str(child.pid), '--start-ticks', ticks, '--window', '42',
                               '--log', str(log), '--output-dir', str(root / 'captures'), '--timeout', str(timeout),
                               '--max-captures', '20', '--execute',
                               *(['--capture-until-timeout'] if continuous else ['--stop-after-ready'] if explicit_stop else [])]
                    with patch.object(sys, 'argv', options), patch.dict(os.environ, DISPLAY=':host-fixture'), \
                         patch.object(ready.subprocess, 'run', command), \
                         patch.object(ready.time, 'monotonic_ns', lambda: clock[0]), \
                         patch.object(ready.time, 'monotonic', lambda: clock[0] / ready.SECOND), \
                         patch.object(ready.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + int(seconds * ready.SECOND))), \
                         contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(ready.main(), 0)
                    self.assertTrue(all(b - a >= ready.SECOND // 2 for a, b in zip(captures, captures[1:])))
                    records = sorted((root / 'captures').glob('capture-*/receipt.json'))
                    self.assertEqual(len(records), len(captures))
                    if black_first:
                        self.assertFalse(json.loads(records[0].read_text())['flags']['rgb_nonzero'])
                        self.assertTrue((records[0].parent / 'native.bmp').exists())
                    return len(captures)
                finally:
                    child.terminate(); child.wait(timeout=3)

    def test_actual_main_stops_after_first_qualified_capture_and_preserves_black(self):
        self.assertEqual(self.run_capture_main(False, True), 2)

    def test_actual_main_menu_mode_keeps_multiple_frames_until_fixed_timeout(self):
        self.assertEqual(self.run_capture_main(True, False), 3)

    def test_actual_menu_main_never_exceeds_twenty_frames(self):
        self.assertEqual(self.run_capture_main(True, False, timeout=10.2), 20)

    def test_explicit_stop_mode_preserves_default_first_ready_behavior(self):
        self.assertEqual(self.run_capture_main(False, True, explicit_stop=True), 2)


if __name__ == '__main__':
    unittest.main()
