#!/usr/bin/env python3
"""Host contracts using synthetic native records; no guest or engine loaded."""
import copy
import fcntl
import hashlib
import os
from pathlib import Path
import tempfile
import unittest

import cache_trial as trial
from exploration_common import digest, save


class CacheTrialTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / 'cache'
        self.directory.mkdir(mode=0o700)
        self.cfg = {'base': str(self.root / 'base'), 'seed': str(self.root / 'seed'),
            'state': str(self.root / 'state'), 'seedInventorySHA256': 'b' * 64,
            'pins': {key: {'sha256': 'a' * 64} for key in
                     ('engine', 'frontend', 'rom', 'package', 'baseLock', 'baseIdentity')},
            'cacheTrial': {'mode': 'cold', 'directory': str(self.directory)}}
        for key in ('base', 'seed', 'state'):
            Path(self.cfg[key]).mkdir(mode=0o700)

    def record(self, key='c' * 64, producer=None, payload=b'bounded symbolic IR'):
        raw = (b'LTIR\x01\0\0\0' + bytes.fromhex(producer or 'a' * 64)
               + bytes.fromhex(key) + hashlib.sha256(payload).digest() + payload)
        path = self.directory / key
        path.write_bytes(raw)
        path.chmod(0o600)
        lock = self.directory / '.lock'
        if not lock.exists():
            lock.touch(mode=0o600)
        return path

    def log(self, stored=1, hits=0):
        return ('LightTouch TCG IR cache: experimental enabled, mapped-image-sha256='
                + 'a' * 64 + '; 32MiB/4096 records maximum\n'
                + f'LightTouch TCG IR cache: hits={hits} misses=21 rejected=10 stored={stored}; frontend IR only, fresh native code\n')

    def produce(self):
        initial = trial.before(self.cfg)
        self.record()
        receipt = trial.finish(self.cfg, initial, self.log(), True, [44, 326])
        receipt_path = self.root / 'cache-trial.json'
        save(receipt_path, receipt)
        result_path = self.root / 'RESULT.json'
        result = {'status': 'SCOPED_PASS', 'cache_enabled': True,
            'cache_evidence_sha256': digest(receipt_path), 'confirmed_clean_receipt': True,
            'strict_retirement': True, 'dirty': False}
        if 'matchedDiagnostic' in self.cfg:
            result['matched_diagnostic'] = dict(self.cfg['matchedDiagnostic'],
                status='DIAGNOSTIC_SCHEDULE_COMPLETED')
        save(result_path, result)
        self.cfg['cacheTrial']['mode'] = 'reuse'
        self.cfg['pins']['cacheReuseReceipt'] = {'path': str(receipt_path), 'sha256': digest(receipt_path)}
        self.cfg['pins']['cacheReuseResult'] = {'path': str(result_path), 'sha256': digest(result_path)}
        return receipt

    def test_default_off_scrubs_both_inherited_selections_and_has_no_directory(self):
        cfg = dict(self.cfg)
        cfg.pop('cacheTrial')
        self.assertEqual(trial.environment(cfg, {'LT_TCG_IR_CACHE_ENABLE': 'experimental-v1',
            'LT_TCG_IR_CACHE': '/attacker', 'OTHER': 'retained'}), {'OTHER': 'retained'})
        self.assertEqual(trial.before(cfg), {'mode': 'off'})
        cfg['cacheTrial'] = {'mode': 'off', 'directory': str(self.directory)}
        with self.assertRaisesRegex(RuntimeError, 'OFF'):
            trial.selection(cfg)

    def test_on_requires_explicit_exact_selection_and_no_asset_overlap(self):
        env = trial.environment(self.cfg, {'LT_TCG_IR_CACHE': '/inherited'})
        self.assertEqual(env, {'LT_TCG_IR_CACHE_ENABLE': 'experimental-v1',
                              'LT_TCG_IR_CACHE': str(self.directory)})
        for path in (Path(self.cfg['base']), self.root, Path(self.cfg['state']) / 'inside'):
            path.mkdir(exist_ok=True)
            cfg = copy.deepcopy(self.cfg)
            cfg['cacheTrial']['directory'] = str(path)
            with self.subTest(path=path), self.assertRaisesRegex(RuntimeError, 'separate'):
                trial.selection(cfg)
        link = self.root / 'linked-cache'
        link.symlink_to(self.directory, target_is_directory=True)
        self.cfg['cacheTrial']['directory'] = str(link)
        with self.assertRaisesRegex(RuntimeError, 'canonical'):
            trial.selection(self.cfg)

    def test_cache_bytes_producer_and_checksum_are_verified(self):
        entry = self.record()
        actual = trial.snapshot(self.directory, 'a' * 64)
        self.assertEqual(actual['records'], 1)
        self.assertEqual(actual['entries'][entry.name]['sha256'], digest(entry))
        data = bytearray(entry.read_bytes())
        data[-1] ^= 1
        entry.write_bytes(data)
        with self.assertRaisesRegex(RuntimeError, 'checksum'):
            trial.snapshot(self.directory, 'a' * 64)

    def test_private_inode_unknown_file_hardlink_and_symlink_rejections(self):
        entry = self.record()
        for mutation in ('mode', 'unknown', 'hardlink', 'symlink'):
            with self.subTest(mutation=mutation):
                if mutation == 'mode':
                    entry.chmod(0o644)
                elif mutation == 'unknown':
                    (self.directory / '.tmp-stale').touch(mode=0o600)
                elif mutation == 'hardlink':
                    os.link(entry, self.root / 'hardlink')
                else:
                    (self.directory / ('d' * 64)).symlink_to(entry)
                with self.assertRaises((RuntimeError, OSError)):
                    trial.snapshot(self.directory, 'a' * 64)
                if mutation == 'mode':
                    entry.chmod(0o600)
                elif mutation == 'unknown':
                    (self.directory / '.tmp-stale').unlink()
                elif mutation == 'hardlink':
                    (self.root / 'hardlink').unlink()
                else:
                    (self.directory / ('d' * 64)).unlink()

    def test_live_native_lock_refuses_inventory_without_releasing_it(self):
        self.record()
        with (self.directory / '.lock').open('rb') as owned:
            fcntl.flock(owned, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                trial.snapshot(self.directory, 'a' * 64)
        self.assertEqual(trial.snapshot(self.directory, 'a' * 64)['records'], 1)

    def test_cold_is_empty_and_requires_actual_native_store_after_clean_guest(self):
        initial = trial.before(self.cfg)
        with self.assertRaisesRegex(RuntimeError, 'evidence missing'):
            trial.finish(self.cfg, initial, '', True, [44, 326])
        self.record()
        with self.assertRaisesRegex(RuntimeError, 'new empty'):
            trial.before(self.cfg)
        self.assertEqual(trial.finish(self.cfg, initial, self.log(), True, [44, 326])['status'], 'SCOPED_PASS')
        self.assertEqual(trial.finish(self.cfg, initial, self.log(), False, [44, 326])['status'], 'FAIL')
        self.assertEqual(trial.finish(self.cfg, initial, self.log(), True, [45, 326])['status'], 'FAIL')
        with self.assertRaisesRegex(RuntimeError, 'stored'):
            trial.finish(self.cfg, initial, self.log(stored=0), True, [44, 326])

    def test_reuse_binds_exact_clean_result_receipt_identity_and_actual_inventory(self):
        self.produce()
        self.assertEqual(trial.before(self.cfg)['records'], 1)
        for key in ('engine', 'frontend', 'rom', 'package', 'baseLock', 'baseIdentity'):
            cfg = copy.deepcopy(self.cfg)
            cfg['pins'][key]['sha256'] = 'f' * 64
            with self.subTest(pin=key), self.assertRaisesRegex(RuntimeError, 'producer|clean producer'):
                trial.before(cfg)
        self.record('d' * 64)
        with self.assertRaisesRegex(RuntimeError, 'inventory'):
            trial.before(self.cfg)

    def test_reuse_requires_native_hits_and_preserves_original_records(self):
        self.produce()
        initial = trial.before(self.cfg)
        with self.assertRaisesRegex(RuntimeError, 'hits missing'):
            trial.finish(self.cfg, initial, self.log(stored=0, hits=0), True, [44, 326])
        receipt = trial.finish(self.cfg, initial, self.log(stored=0, hits=2), True, [44, 326])
        self.assertEqual(receipt['native_final']['hits'], 2)
        self.record(payload=b'different checksummed IR')
        with self.assertRaisesRegex(RuntimeError, 'altered'):
            trial.finish(self.cfg, initial, self.log(stored=0, hits=2), True, [44, 326])

    def test_scheduled_reuse_requires_actual_matching_completed_producer(self):
        import matched_trial
        self.cfg['matchedDiagnostic'] = {'mode': matched_trial.MODE}
        self.produce()
        self.assertEqual(trial.before(self.cfg)['records'], 1)
        changed = copy.deepcopy(self.cfg)
        changed.pop('matchedDiagnostic')
        with self.assertRaisesRegex(RuntimeError, 'clean producer'):
            trial.before(changed)
        pin = self.cfg['pins']['cacheReuseResult']
        result = __import__('json').loads(Path(pin['path']).read_text())
        for schedule in [None, {'mode': 'other', 'status': 'DIAGNOSTIC_SCHEDULE_COMPLETED'},
                         {'mode': matched_trial.MODE, 'status': 'FAIL'}]:
            save(pin['path'], dict(result, matched_diagnostic=schedule))
            pin['sha256'] = digest(pin['path'])
            with self.assertRaisesRegex(RuntimeError, 'completed matching producer schedule'):
                trial.before(self.cfg)

    def test_reuse_never_accepts_dirty_or_unconfirmed_producer_with_updated_pin(self):
        self.produce()
        pin = self.cfg['pins']['cacheReuseResult']
        result = __import__('json').loads(Path(pin['path']).read_text())
        for mutation in [dict(dirty=True), dict(confirmed_clean_receipt=False),
                         dict(strict_retirement=False), dict(status='FAIL')]:
            save(pin['path'], dict(result, **mutation))
            pin['sha256'] = digest(pin['path'])
            with self.assertRaisesRegex(RuntimeError, 'clean producer'):
                trial.before(self.cfg)

    def test_native_telemetry_cannot_be_wrong_producer_duplicate_or_reset(self):
        initial = trial.before(self.cfg)
        self.record()
        for log in (self.log().replace('a' * 64, 'f' * 64), self.log() + self.log(),
                    self.log(stored=2) + self.log().split('\n', 1)[1]):
            with self.subTest(log=log), self.assertRaisesRegex(RuntimeError, 'evidence missing|reset'):
                trial.finish(self.cfg, initial, log, True, [44, 326])
        with self.assertRaisesRegex(RuntimeError, 'OFF'):
            trial.finish({'cacheTrial': {'mode': 'off'}}, {'mode': 'off'}, self.log(), True, None)


if __name__ == '__main__':
    unittest.main()
