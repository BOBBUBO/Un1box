import json
from pathlib import Path
import tempfile
import unittest

from wake_diagnosis import SECOND, summarize


class RetainedWakeDiagnosis(unittest.TestCase):
    def fixture(self, root, *, wake=False, hits=0, future_source=False):
        home = 60 * SECOND
        begin = home + SECOND // 5
        self.window = (begin, begin + 2_500_000_000)
        def status(when, *, awake, state=2):
            return dict(state=str(state), sleeping=str(int(not awake)),
                        backlight='255' if awake else '0', agent='1', frames='90' if awake else '85',
                        frame_monotonic_ns=str(when + 1 if future_source else (when if awake else home - SECOND)),
                        observed_monotonic_ns=str(when))
        observations = [dict(poll_ns=begin, status=status(begin, awake=False)),
                        dict(poll_ns=begin + SECOND, status=status(begin + SECOND, awake=wake))]
        record = dict(home_input_begin_ns=home, home_released_ns=begin,
                      awake_wait_begin_ns=begin, awake_wait_deadline_ns=self.window[1],
                      awake_wait_end_ns=begin + (SECOND if wake else 2_500_000_000),
                      input_deadline_ns=home + 8 * SECOND,
                      awake_wait_observations=observations, slider_started=wake)
        stop = home + 3 * SECOND
        documents = {
            'hello.json': dict(monotonic_ns=10 * SECOND),
            'timed-lock-unlock-home-unlock.json': record,
            'RESULT.json': dict(signals=[dict(observed_monotonic_ns=stop)]),
            'cache-trial.json': dict(mode='cold', native_final=dict(hits=hits, stored=4096, misses=170824)),
        }
        for name, value in documents.items():
            (root / name).write_text(json.dumps(value))
        statuses = [x['status'] for x in observations]
        # A later shutdown-generated lit frame MUST NOT become wake evidence.
        statuses.append(status(stop + SECOND, awake=True, state=4))
        (root / 'session.log').write_text('\n'.join('status ' + ' '.join(k + '=' + v for k, v in s.items()) for s in statuses))
        (root / 'controls.jsonl').write_text('\n'.join(json.dumps(dict(
            argv=['/usr/bin/xdotool', action, 'h'], input_begin_ns=when, input_end_ns=when + 1))
            for action, when in [('keydown', home), ('keyup', begin - 1)]))

    def test_shutdown_lit_frame_cannot_rescue_original_failed_wake(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.fixture(root)
            actual = summarize(root)
            self.assertFalse(actual['original_window_predicate_observed'])
            self.assertTrue(actual['state_transitions_after_home'][-1]['after_stop_dispatch'])
            self.assertEqual(actual['wake_window_relative_hello_ns'], [x - 10 * SECOND for x in self.window])
            self.assertTrue(actual['home_key_pair_recorded'])
            self.assertFalse(actual['input_dispatch_is_guest_consumption'])
            self.assertFalse(actual['native_replay_observed'])
            self.assertFalse(actual['cause_assigned'])
            self.assertFalse(actual['guest_acceptance'])

    def test_actual_window_wake_and_hits_remain_scoped_telemetry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.fixture(root, wake=True, hits=3)
            actual = summarize(root)
            self.assertTrue(actual['original_window_predicate_observed'])
            self.assertEqual(actual['first_admitted_relative_home_ns'], 1_200_000_000)
            self.assertTrue(actual['native_replay_observed'])
            self.assertFalse(actual['performance_qualified'])
            self.assertFalse(actual['guest_acceptance'])

    def test_future_source_cannot_admit_wake(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.fixture(root, wake=True, future_source=True)
            self.assertFalse(summarize(root)['original_window_predicate_observed'])

    def test_window_extension_and_inconsistent_clocks_rejected(self):
        for key in ['awake_wait_deadline_ns', 'home_released_ns']:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); self.fixture(root)
                path = root / 'timed-lock-unlock-home-unlock.json'
                record = json.loads(path.read_text()); record[key] += SECOND
                path.write_text(json.dumps(record))
                with self.assertRaises(ValueError): summarize(root)

    def test_hashes_bind_original_files_without_writing_to_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.fixture(root)
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            first = summarize(root)
            self.assertEqual(before, {p.name: p.read_bytes() for p in root.iterdir()})
            path = root / 'RESULT.json'; path.write_text(path.read_text() + '\n')
            second = summarize(root)
            self.assertNotEqual(first['input_sha256']['RESULT.json'], second['input_sha256']['RESULT.json'])
            self.assertEqual(len(first['input_sha256']), 6)

    def test_symlink_to_input_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.fixture(root)
            (root / 'hello.json').rename(root / 'other.json')
            (root / 'hello.json').symlink_to(root / 'other.json')
            with self.assertRaises(ValueError): summarize(root)


if __name__ == '__main__':
    unittest.main()
