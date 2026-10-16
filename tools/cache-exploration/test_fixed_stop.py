"""Fake-clock/filesystem controller contracts; no engine, display or guest."""
import copy
import unittest
from unittest.mock import patch

import explore
import image_operator
import matched_trial
import cache_trial
from exploration_common import SECOND, read, save
from test_image_operator import runtime_fixture


class Tests(unittest.TestCase):
    def setup_failure(self, root, cfg, hello, scene):
        cfg = copy.deepcopy(cfg)
        cfg['sceneFailurePolicy'] = matched_trial.SCENE_FAILURE_POLICY
        if scene == 'home':
            for name in ('home-admitted.json', 'settings-input.json', 'settings-admitted.json'):
                (root / name).unlink()
        else:
            (root / 'settings-admitted.json').unlink()
        tracker = matched_trial.Tracker(cfg)
        tracker.observe(root, hello, hello + (90 if scene == 'home' else 119) * SECOND, [])
        timed = image_operator.Operator(cfg)
        timed.home_sent = True
        timed.settings_sent = scene == 'settings'
        return cfg, tracker, timed

    def step(self, timed, tracker, root, hello, age):
        with patch.object(explore.time, 'monotonic_ns', return_value=hello + age * SECOND):
            return explore.diagnostic_step(timed, tracker, root, hello, hello + age * SECOND, [])

    def test_home_and_settings_failure_keep_original_stop_and_never_send_dependent_input(self):
        for scene, deadline in [('home', 95), ('settings', 120)]:
            with self.subTest(scene=scene), runtime_fixture() as (_, root, cfg, result):
                hello = result['first_hello_monotonic_ns']
                _, tracker, timed = self.setup_failure(root, cfg, hello, scene)
                with patch.object(explore, 'owner') as owner, \
                        patch.object(timed, 'tick', wraps=timed.tick) as tick, \
                        patch.object(image_operator.control, 'xcall') as inputs, \
                        patch.object(image_operator.control, 'home_unlock') as home:
                    self.assertIsNone(self.step(timed, tracker, root, hello, deadline))
                    original = (root / 'scene-failure.json').read_bytes()
                    for age in [deadline + 1, 129, 135, 179]:
                        self.assertIsNone(self.step(timed, tracker, root, hello, age))
                    self.assertEqual(self.step(timed, tracker, root, hello, 180),
                                     'matched scheduled Stop180s')
                    self.assertEqual(tick.call_count, 1)
                    self.assertEqual(owner.call_count, 6)
                    inputs.assert_not_called(); home.assert_not_called()
                self.assertEqual((root / 'scene-failure.json').read_bytes(), original)
                failed = read(root / 'scene-failure.json')
                self.assertEqual(failed['status'], 'FAIL')
                self.assertEqual(failed['original_deadline_ns'], hello + deadline * SECOND)
                self.assertEqual(failed['ordinary_stop_ns'], hello + 180 * SECOND)
                finished = tracker.finish(root, hello, hello + 180 * SECOND)
                self.assertEqual(finished['status'], 'SCENE_FAILED_SCHEDULE_COMPLETED')
                self.assertFalse(finished['guest_acceptance'])
                self.assertFalse(finished['performance_qualified'])
                self.assertFalse((root / 'matched-readiness.json').is_file() and tracker.ready)

    def test_default_still_stops_at_original_scene_failure_without_latching(self):
        with runtime_fixture() as (_, root, cfg, result):
            (root / 'home-admitted.json').unlink()
            tracker = matched_trial.Tracker(cfg); timed = image_operator.Operator(cfg)
            timed.home_sent = True
            with patch.object(explore, 'owner') as owner:
                with self.assertRaisesRegex(matched_trial.SceneDeadlineFailure, 'deadline95s'):
                    self.step(timed, tracker, root, result['first_hello_monotonic_ns'], 95)
                owner.assert_not_called()
            self.assertFalse((root / 'scene-failure.json').exists())

    def test_safety_resource_identity_and_capture_failures_escape_without_latch(self):
        for error in [RuntimeError('memory/display guard'), RuntimeError('worker identity changed'),
                      RuntimeError('source/copy receipt stale'), OSError('disk failure')]:
            with self.subTest(error=str(error)), runtime_fixture() as (_, root, cfg, result):
                cfg['sceneFailurePolicy'] = matched_trial.SCENE_FAILURE_POLICY
                tracker = matched_trial.Tracker(cfg); timed = image_operator.Operator(cfg)
                with patch.object(timed, 'tick', side_effect=error):
                    with self.assertRaises(type(error)):
                        self.step(timed, tracker, root, result['first_hello_monotonic_ns'], 95)
                self.assertIsNone(tracker.scene_failure)
                self.assertFalse((root / 'scene-failure.json').exists())

    def test_owner_failure_after_latch_is_immediate_and_no_new_input_occurs(self):
        with runtime_fixture() as (_, root, cfg, result):
            hello = result['first_hello_monotonic_ns']
            _, tracker, timed = self.setup_failure(root, cfg, hello, 'home')
            with patch.object(explore, 'owner'):
                self.step(timed, tracker, root, hello, 95)
            with patch.object(explore, 'owner', side_effect=RuntimeError('Worker identity changed')), \
                    patch.object(timed, 'tick') as tick:
                with self.assertRaisesRegex(RuntimeError, 'Worker identity changed'):
                    self.step(timed, tracker, root, hello, 96)
                tick.assert_not_called()

    def test_changed_evidence_late_admission_hello_or_failure_receipt_is_fatal(self):
        for mutation in ['late-home', 'old-input', 'failure', 'hello']:
            with self.subTest(mutation=mutation), runtime_fixture() as (_, root, cfg, result):
                hello = result['first_hello_monotonic_ns']
                _, tracker, timed = self.setup_failure(root, cfg, hello, 'home')
                with patch.object(explore, 'owner'):
                    self.step(timed, tracker, root, hello, 95)
                    if mutation == 'late-home':
                        save(root / 'home-admitted.json', {'late': True})
                    elif mutation == 'old-input':
                        save(root / 'matched-home-unlock-input.json', {'rewritten': True})
                    elif mutation == 'failure':
                        save(root / 'scene-failure.json', {'status': 'PASS'})
                    with self.assertRaises(RuntimeError):
                        self.step(timed, tracker, root, hello + (1 if mutation == 'hello' else 0), 96)

    def test_policy_is_explicit_and_bound_to_cache_workload_identity(self):
        with runtime_fixture() as (_, _, cfg, _):
            modified = dict(cfg, sceneFailurePolicy=matched_trial.SCENE_FAILURE_POLICY)
            self.assertNotEqual(cache_trial.identity(cfg), cache_trial.identity(modified))
            for invalid in [dict(cfg, sceneFailurePolicy='unknown'),
                            dict(sceneFailurePolicy=matched_trial.SCENE_FAILURE_POLICY)]:
                with self.assertRaises(RuntimeError):
                    matched_trial.Tracker(invalid)

    def test_scene_failure_cannot_adopt_already_rewritten_evidence(self):
        with runtime_fixture() as (_, root, cfg, result):
            hello = result['first_hello_monotonic_ns']
            _, tracker, timed = self.setup_failure(root, cfg, hello, 'home')
            save(root / 'matched-home-unlock-input.json', {'rewritten_before_failure': True})
            with self.assertRaisesRegex(RuntimeError, 'changed before scene failure'):
                self.step(timed, tracker, root, hello, 95)
            self.assertIsNone(tracker.scene_failure)
            self.assertFalse((root / 'scene-failure.json').exists())

    def test_failure_cannot_be_retimed_overwritten_or_finished_with_early_stop(self):
        with runtime_fixture() as (_, root, cfg, result):
            hello = result['first_hello_monotonic_ns']
            _, tracker, timed = self.setup_failure(root, cfg, hello, 'home')
            with self.assertRaises(RuntimeError):
                tracker.latch_scene_failure(root, hello, hello + 94 * SECOND,
                                            matched_trial.SceneDeadlineFailure('home'))
            with patch.object(explore, 'owner'):
                self.step(timed, tracker, root, hello, 95)
            for stop in [hello + 95 * SECOND, hello + 181 * SECOND + 1, None]:
                with self.assertRaises(RuntimeError):
                    tracker.finish(root, hello, stop)
            with self.assertRaises(RuntimeError):
                tracker.latch_scene_failure(root, hello, hello + 96 * SECOND,
                                            matched_trial.SceneDeadlineFailure('home'))


if __name__ == '__main__':
    unittest.main()
