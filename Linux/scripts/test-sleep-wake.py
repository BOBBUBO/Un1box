#!/usr/bin/env python3
"""Target-free wake admission regressions; no engine/GUI or scene assertions."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('wake', Path(__file__).with_name('watch-sleep-wake.py'))
wake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wake)


class WakeAdmission(unittest.TestCase):
    def test_missing_live_stale_or_any_agent_value_does_not_block_sleep_wake(self):
        status = dict(state=2, sleeping=1, observed_monotonic_ns=100)
        self.assertTrue(wake.sleep_candidate(status, 101))
        for agent in (0, 1, 2, 'unknown'):
            self.assertTrue(wake.sleep_candidate(dict(status, agent=agent), 101))

    def test_awake_stopping_failed_stale_or_future_cannot_admit(self):
        for status in (dict(state=2, sleeping=0, observed_monotonic_ns=100),
                       dict(state=4, sleeping=1, observed_monotonic_ns=100),
                       dict(state=5, sleeping=1, observed_monotonic_ns=100),
                       dict(state=2, sleeping=1, observed_monotonic_ns=-1_000_000_000),
                       dict(state=2, sleeping=1, observed_monotonic_ns=102), {}):
            self.assertFalse(wake.sleep_candidate(status, 101), status)

    def test_only_one_original_complete_HELLO_epoch(self):
        self.assertIsNone(wake.first_hello([b'status test']))
        row = b'admission hello_verified_observed_monotonic_ns=100 worker_pid=12'
        self.assertEqual(wake.first_hello([row, row]), 100)
        with self.assertRaisesRegex(RuntimeError, 'epoch changed'):
            wake.first_hello([row, row.replace(b'=100 ', b'=101 ')])

    def test_unverified_or_retired_owner_never_signaled(self):
        calls = []
        for verified, retired in ((False, False), (False, True), (True, True)):
            self.assertFalse(wake.request_owned_stop(verified, 9, lambda: retired,
                                                     lambda *args: calls.append(args)))
        self.assertEqual(calls, [])

    def test_verified_live_owner_gets_only_ordinary_stop(self):
        calls = []
        self.assertTrue(wake.request_owned_stop(True, 9, lambda: False,
                                                lambda *args: calls.append(args)))
        self.assertEqual(calls, [(9, wake.signal.SIGINT)])

    def test_owner_exit_race_is_not_reported_as_signal_delivery(self):
        def gone(*args):
            raise ProcessLookupError()
        self.assertFalse(wake.request_owned_stop(True, 9, lambda: False, gone))


if __name__ == '__main__':
    unittest.main()
