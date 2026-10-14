#!/usr/bin/env python3
"""Offline measurement-parser checks; never guest acceptance evidence."""
import json
import pathlib
import runpy
import unittest

tool = runpy.run_path(str(pathlib.Path(__file__).with_name('summarize-acceptance.py')), run_name='contracts')

class MeasurementTests(unittest.TestCase):
    def test_legacy_receipt_does_not_invent_cadence(self):
        result = tool['summarize']('status state=0 confirmed=1 dirty=0 frames=99\n')
        self.assertTrue(result['confirmed_clean_status_observed'])
        self.assertEqual(result['intervals'], [])

    def test_rates_skip_reversed_clock_and_keep_pause_context(self):
        result = tool['summarize'](
            'status observed_monotonic_ns=1000000000 state=2 frames=10 pcm_captured=44100\n'
            'status observed_monotonic_ns=2000000000 state=3 frames=12 pcm_captured=88200\n'
            'status observed_monotonic_ns=1500000000 state=0 confirmed=1 dirty=0 frames=12\n')
        self.assertEqual(len(result['intervals']), 1)
        self.assertEqual(result['intervals'][0]['rates_per_second'], {'frames': 2.0, 'pcm_captured': 44100.0})
        self.assertEqual(result['intervals'][0]['state_after'], 3)

    def test_recycled_pid_is_not_one_cpu_interval(self):
        rows = [dict(pid=5, start_ticks='1', observed_monotonic_ns=1000000000, cpu_user_ticks=10,
                     cpu_system_ticks=5, clock_ticks_per_second=100, rss_bytes=1000, pss_bytes=None, threads=2),
                dict(pid=5, start_ticks='1', observed_monotonic_ns=2000000000, cpu_user_ticks=60,
                     cpu_system_ticks=5, clock_ticks_per_second=100, rss_bytes=2000, pss_bytes=1200, threads=3),
                dict(pid=5, start_ticks='2', observed_monotonic_ns=3000000000, cpu_user_ticks=0,
                     cpu_system_ticks=0, clock_ticks_per_second=100, rss_bytes=500, pss_bytes=None, threads=1)]
        result = tool['worker_samples']('\n'.join(json.dumps(r) for r in rows))
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['cpu_percent_of_one_core'], 50.0)
        self.assertIsNone(result[1]['cpu_percent_of_one_core'])

if __name__ == '__main__':
    unittest.main()
