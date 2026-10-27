#!/usr/bin/env python3
"""Synthetic offline reviewer regressions; never guest acceptance."""
from copy import deepcopy
from pathlib import Path
import runpy
import unittest

module = runpy.run_path(str(Path(__file__).with_name('review-burst-media.py')), run_name='contracts')


def samples():
    rows = []
    for second in range(1201):
        audio = {'pulledNonzeroSamples': (second // 30) * 100,
                 'writtenNonzeroSamples': (second // 30) * 100,
                 'playedFrames': second * 44, 'writeFailures': 0,
                 'nativePullFailures': 0, 'underruns': second // 30, 'shortWrites': 0}
        facts = {'producedFrames': second * 10, 'presentedFrames': second * 10,
                 'windowFailures': 0, 'frameCopyFailures': 0, 'nativePcmFailures': 0,
                 'audio': audio}
        rows.append({'label': 'measurement-start' if second == 0 else 'measurement-end' if second == 1200 else 'running',
                     'hostElapsedMs': 100000 + second * 1000,
                     'status': {'state': 'running', 'error': '', 'pid': 73, 'uid': 10001, 'observations': facts}})
    return rows


class BurstReviewTests(unittest.TestCase):
    def test_every_window_has_burst_and_frames_without_claiming_audibility(self):
        result = module['analyze'](samples())
        self.assertTrue(result['host_window_checks_pass'])
        self.assertEqual(40, len(result['windows']))
        self.assertEqual(1, result['windows'][0]['counter_deltas']['audio.underruns'])

    def test_one_early_pulse_can_pass_aggregate_but_not_twenty_minutes(self):
        rows = samples()
        for i, row in enumerate(rows):
            facts = row['status']['observations']
            for key in ('producedFrames', 'presentedFrames'):
                facts[key] = min(i, 1)
            for key in ('pulledNonzeroSamples', 'writtenNonzeroSamples', 'playedFrames'):
                facts['audio'][key] = min(i, 1)
        result = module['analyze'](rows)
        self.assertTrue(result['aggregate_only_would_show_activity'])
        self.assertFalse(result['host_window_checks_pass'])
        self.assertEqual(39, sum(not row['host_window_checks_pass'] for row in result['windows']))

    def test_missing_zero_error_facts_are_not_success(self):
        rows = samples()
        del rows[600]['status']['observations']['audio']['writeFailures']
        self.assertFalse(module['analyze'](rows)['host_window_checks_pass'])

    def test_clock_gaps_counter_reset_and_pid_reuse_rejected(self):
        for mutation in ('gap', 'reset', 'pid', 'backwards'):
            rows = samples()
            if mutation == 'gap': rows = rows[:100] + rows[110:]
            if mutation == 'reset': rows[600]['status']['observations']['producedFrames'] = 0
            if mutation == 'pid': rows[600]['status']['pid'] = 74
            if mutation == 'backwards': rows[600]['hostElapsedMs'] = rows[599]['hostElapsedMs'] - 1
            self.assertFalse(module['analyze'](rows)['host_window_checks_pass'], mutation)

    def test_stale_post_segment_and_recycled_identity_do_not_qualify_resume(self):
        sustained = module['analyze'](samples())
        # A complete short segment from an earlier time has positive local deltas.
        rows = samples()[:31]; rows[-1]['label'] = 'measurement-end'
        earlier = module['analyze'](rows, expected_ms=30000)
        self.assertTrue(earlier['host_window_checks_pass'])
        self.assertFalse(module['check_post'](sustained, [earlier]))
        future = samples()[:31]; future[-1]['label'] = 'measurement-end'
        for row in future:
            row['hostElapsedMs'] += 1_201_000
            facts = row['status']['observations']
            for key in ('producedFrames', 'presentedFrames'): facts[key] += 12000
            for key in ('pulledNonzeroSamples', 'writtenNonzeroSamples'): facts['audio'][key] += 4000
            facts['audio']['playedFrames'] += 52800
        segment = module['analyze'](future, expected_ms=30000)
        self.assertTrue(module['check_post'](sustained, [segment]))
        segment = module['analyze'](future, expected_ms=30000); segment['worker_pid_uid'] = (74, 10001)
        self.assertFalse(module['check_post'](sustained, [segment]))
        self.assertFalse(module['check_post'](sustained, []))

    def test_short_segment_and_worker_error_rejected(self):
        rows = deepcopy(samples()[:31]); rows[-1]['label'] = 'measurement-end'
        self.assertFalse(module['analyze'](rows)['host_window_checks_pass'])
        self.assertTrue(module['analyze'](rows, expected_ms=30000)['host_window_checks_pass'])
        rows[5]['status']['error'] = 'guest failure'
        self.assertFalse(module['analyze'](rows, expected_ms=30000)['host_window_checks_pass'])


if __name__ == '__main__':
    unittest.main()
