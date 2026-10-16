"""Synthetic filesystem/fake-clock/intercepted-input contracts, never guest proof."""
import contextlib
import copy
import struct
import tempfile
import unittest
from unittest.mock import patch

from exploration_common import *
import image_operator as operator
import exploration_control as control
import matched_trial
import postrun_review
import cache_trial


def bitmap(value=0):
    data = bytearray(54 + 320 * 480 * 4)
    data[:2] = b'BM'
    struct.pack_into('<I', data, 2, len(data))
    struct.pack_into('<I', data, 10, 54)
    struct.pack_into('<IiiHHI', data, 14, 40, 320, -480, 1, 32, 0)
    data[54 + (100 * 320 + 100) * 4] = value
    return data


def capture(directory, saved, data, frontend=41, worker=42, start='123'):
    directory.mkdir()
    (directory / 'native.bmp').write_bytes(data)
    # Converter calls are intercepted; this string makes no decoded-image claim.
    (directory / 'native.png').write_bytes(b'synthetic-not-real-PNG')
    receipt = dict(sequence='12', shared_generation='4', coherent_readback='1',
        frame_monotonic_ns=str(saved - 60 * SECOND),
        copy_started_monotonic_ns=str(saved - 200000000),
        copy_finished_monotonic_ns=str(saved - 150000000),
        observed_monotonic_ns=str(saved - 100000000), saved_monotonic_ns=saved,
        diagnostics_monotonic_ns=str(saved - 250000000), sleeping='0', agent='1',
        backlight='245', frontend_pid=frontend, worker_pid=worker, worker_start=start,
        sha256=digest(directory / 'native.bmp'))
    save(directory / 'receipt.json', receipt)
    return receipt


@contextlib.contextmanager
def runtime_fixture():
    with tempfile.TemporaryDirectory() as directory:
        packet = Path(directory); root = packet / 'exploration01'; root.mkdir()
        state = root / 'state'; state.mkdir(); (state / '.lease').write_bytes(b'')
        now = time.monotonic_ns(); hello = now - 200 * SECOND
        refs = {}
        for scene, value in [('home', 0), ('settings', 1)]:
            ref = packet / ('reference-' + scene)
            capture(ref, hello - 200 * SECOND, bitmap(value))
            refs[scene] = operator.prepare_reference(ref)
        cfg = dict(cacheTrial={'mode': 'off'}, matchedDiagnostic={'mode': matched_trial.MODE},
            imageBoundOperator={'mode': operator.MODE, 'references': refs}, state=str(state),
            seedInventorySHA256='synthetic', pins={key: {'sha256': 'a' * 64} for key in
                ('engine', 'frontend', 'rom', 'package', 'baseLock', 'baseIdentity')})
        save(packet / 'EXPLORATION-INPUTS.json', cfg)
        save(packet / 'EXPLORATION-SOURCE-SHA256.json', {'EXPLORATION-INPUTS.json': digest(packet / 'EXPLORATION-INPUTS.json')})
        for scene, second, value in [('home', 80, 0), ('settings', 112, 1)]:
            path = root / ('automatic-' + scene)
            saved = hello + second * SECOND
            receipt = capture(path, saved, bitmap(value))
            save(root / (scene + '-admitted.json'), dict(scope='AUTOMATIC_IMAGE_BOUND_ADMISSION_ONLY',
                scene=scene, model_inspected=False, scene_qualified=False, admitted_monotonic_ns=saved+1,
                hello_ns=hello, capture=path.name, bitmap_sha256=receipt['sha256'],
                png_sha256=digest(path / 'native.png'), receipt_sha256=digest(path / 'receipt.json'),
                reference=refs[scene], frontend={'pid': 41}, worker={'pid': 42, 'start_ticks': '123'}))
        matched_trial.input_claim(True, root, hello, 'home-unlock', hello + 50 * SECOND)
        save(root / 'settings-input.json', {'input_begin_ns': hello + 105 * SECOND, 'native_pixel': [44, 326], 'model_inspected': False})
        logs = ['status observed_monotonic_ns=%d state=2 agent=1 shutdown_phase=0 sleeping=0 backlight=255' % (hello + s * SECOND) for s in range(120, 136)]
        (root / 'session.log').write_text('\n'.join(logs) + '\n')
        tracker = matched_trial.Tracker(cfg)
        tracker.observe(root, hello, hello + 135 * SECOND, logs)
        schedule = tracker.finish(root, hello, hello + 180 * SECOND)
        absent = {'pid': 2147483646, 'start_ticks': '1', 'uid': os.geteuid(), 'argv': ['synthetic']}
        save(root / 'consumed.json', {'controller': absent})
        save(root / 'command.json', {'cgroup': '/sys/fs/cgroup/lighttouch-synthetic-test-absent'})
        receipt = {'mode': 'off', 'status': 'AWAITING_ROOT_REVIEW'}
        save(root / 'cache-trial.json', receipt)
        result = dict(status='AWAITING_ROOT_REVIEW', automatic_image_admission=True,
            home_operator_attestation=False, settings_operator_attestation=False, exit_code=0,
            final_receipt='FINAL status_available=1 state=0 confirmed=1 dirty=0 worker_reaped=1 worker_exit=0 worker_disposal_requested=0 frontend_return=0',
            error=None, dirty=False, signals=[], confirmed_clean_receipt=True, strict_retirement=True,
            lease_available=True, seed_unchanged=True, immutable_base_unchanged=True,
            home_image_admitted=True, settings_image_admitted=True, exact_candidate_mapping_observed=True,
            frontend=absent, worker=absent, first_hello_monotonic_ns=hello, matched_diagnostic=schedule,
            cache_evidence_sha256=digest(root / 'cache-trial.json'))
        save(root / 'RESULT.json', result)
        yield packet, root, cfg, result


class Tests(unittest.TestCase):
    def test_automatic_schedule_never_creates_personal_inspection(self):
        with runtime_fixture() as (_, root, cfg, result):
            self.assertEqual(result['matched_diagnostic']['status'], 'INPUT_SCHEDULE_COMPLETED_AWAITING_ROOT_REVIEW')
            self.assertFalse((root / 'home-inspected.json').exists())
            self.assertFalse((root / 'settings-inspected.json').exists())
            self.assertEqual(postrun_review.runtime_evidence(cfg, root)[0], result)

    def test_default_off_and_explicit_configuration_validation(self):
        self.assertFalse(operator.enabled({}))
        for cfg in [dict(imageBoundOperator={}), dict(imageBoundOperator={'mode': 'bad', 'references': {}})]:
            with self.assertRaises(RuntimeError): operator.enabled(cfg)
        with runtime_fixture() as (_, _, cfg, _):
            self.assertTrue(operator.enabled(cfg))
            self.assertNotEqual(cache_trial.identity(cfg), cache_trial.identity({k: v for k, v in cfg.items() if k != 'imageBoundOperator'}))

    def test_reference_rejects_changed_png_bitmap_receipt_and_sleep(self):
        for name in ['native.png', 'native.bmp', 'receipt.json']:
            with self.subTest(name=name), runtime_fixture() as (_, _, cfg, _):
                ref = cfg['imageBoundOperator']['references']['home']
                Path(ref[name]['path']).write_bytes(b'changed')
                with self.assertRaisesRegex(RuntimeError, 'reference changed'): operator.validate_reference(ref)
        with runtime_fixture() as (_, _, cfg, _):
            ref = cfg['imageBoundOperator']['references']['home']; path=Path(ref['receipt.json']['path'])
            row=read(path);row['sleeping']='1';save(path,row);ref['receipt.json']['sha256']=digest(path)
            with self.assertRaisesRegex(RuntimeError, 'awake coherent'):operator.validate_reference(ref)

    def test_admission_rejects_image_receipt_reference_and_personal_claim_changes(self):
        for name in ['native.png', 'native.bmp', 'receipt.json']:
            with self.subTest(name=name), runtime_fixture() as (_, root, _, _):
                (root / 'automatic-home' / name).write_bytes(b'changed')
                with self.assertRaises(RuntimeError): operator.validate_admission(root, 'home')
        for change in [dict(model_inspected=True), dict(scene_qualified=True), dict(capture='../escape'),
                       dict(admitted_monotonic_ns=1), dict(worker={'pid': 99, 'start_ticks': '123'})]:
            with self.subTest(change=change), runtime_fixture() as (_, root, _, _):
                path=root/'home-admitted.json';save(path,dict(read(path),**change))
                with self.assertRaises(RuntimeError): operator.validate_admission(root, 'home')

    def test_exact_comparer_masks_only_clock_rgb_not_wifi_or_alpha(self):
        original=bitmap()
        for x,y,channel,expected in [(150,10,0,True),(150,10,3,False),(30,10,0,False),(100,100,0,False)]:
            changed=bytearray(original);changed[54+(y*320+x)*4+channel]=1
            self.assertEqual(control.unchanged_except_clock(original,changed),expected)

    def test_current_automatic_capture_is_retained_without_inspected_receipt(self):
        with runtime_fixture() as (_, root, cfg, _):
            (root/'home-admitted.json').unlink()
            now=time.monotonic_ns(); output=root/'new-current'
            receipt=capture(output,now-50000000,bitmap())
            owner=({'frontend':{'pid':41}},{'pid':42,'start_ticks':'123'})
            with patch.object(control,'owner',return_value=owner),patch.object(control,'capture',return_value=(output,receipt)),patch.object(control,'convert_bitmap'):
                self.assertTrue(operator.Operator(cfg).admit(root,'home',now-80*SECOND))
            record=operator.validate_admission(root,'home')
            self.assertFalse(record['model_inspected']);self.assertNotIn('inspected_monotonic_ns',record)
            self.assertFalse((root/'home-inspected.json').exists())

    def test_automatic_capture_rejects_wrong_pixels_stale_and_changed_owner(self):
        for change in ['pixels','stale','owner']:
            with self.subTest(change=change),runtime_fixture() as (_,root,cfg,_):
                (root/'home-admitted.json').unlink();now=time.monotonic_ns();output=root/'new-current'
                receipt=capture(output,now-(3*SECOND if change=='stale' else 50000000),bitmap(1 if change=='pixels' else 0))
                owners=[({'frontend':{'pid':41}},{'pid':42,'start_ticks':'123'}),({'frontend':{'pid':41}},{'pid':99,'start_ticks':'123'})]
                with patch.object(control,'owner',side_effect=owners if change=='owner' else lambda:owners[0]),patch.object(control,'capture',return_value=(output,receipt)),patch.object(control,'convert_bitmap'):
                    if change=='pixels':self.assertFalse(operator.Operator(cfg).admit(root,'home',now-80*SECOND))
                    else:
                        with self.assertRaises(RuntimeError):operator.Operator(cfg).admit(root,'home',now-80*SECOND)
                self.assertFalse((root/'home-admitted.json').exists())

    def test_one_failed_home_attempt_is_not_retried(self):
        with runtime_fixture() as (_,root,cfg,result):
            timed=operator.Operator(cfg);hello=result['first_hello_monotonic_ns']
            with patch.object(control,'home_unlock',side_effect=RuntimeError('synthetic failed input')) as home:
                with self.assertRaises(RuntimeError):timed.tick(root,hello,hello+50*SECOND)
                timed.tick(root,hello,hello+51*SECOND)
                self.assertEqual(home.call_count,1)

    def test_automatic_continuity_does_not_write_personal_timestamp(self):
        with runtime_fixture() as (_,root,_,_):
            now=time.monotonic_ns();old=root/'old';current=root/'new'
            capture(old,now-SECOND,bitmap());receipt=capture(current,now-50000000,bitmap())
            owner=({'frontend':{'pid':41}},{'pid':42,'start_ticks':'123'})
            with patch.object(control,'R',root),patch.object(control,'owner',return_value=owner),patch.object(control,'capture',return_value=(current,receipt)):
                record=control.continuity('old','home','new',inspected=False)
            self.assertFalse(record['model_inspected'])
            self.assertNotIn('inspected_monotonic_ns',record)

    def test_settings_is_one_balanced_exact_scheduled_input_without_inspection(self):
        with runtime_fixture() as (_,root,cfg,result):
            timed=operator.Operator(cfg);timed.home_sent=True
            hello=result['first_hello_monotonic_ns'];now=hello+105*SECOND;events=[]
            def xcall(arguments,release=False):events.append((arguments,release))
            with patch.object(control,'continuity',return_value={'verification_monotonic_ns':now,'model_inspected':False}) as continuity,patch.object(control,'geometry',return_value={}),patch.object(control,'owner',return_value=({},{})),patch.object(control,'capture_lock',side_effect=contextlib.nullcontext),patch.object(control,'move') as move,patch.object(control,'xcall',side_effect=xcall),patch.object(time,'monotonic_ns',return_value=now),patch.object(time,'sleep'):
                timed.tick(root,hello,now)
                timed.tick(root,hello,now)
            self.assertFalse(continuity.call_args.kwargs['inspected'])
            move.assert_called_once_with(44,326,{})
            self.assertEqual(events,[(['mousedown','1'],False),(['mouseup','1'],True)])
            record=read(root/'settings-input.json');self.assertFalse(record['model_inspected'])
            self.assertEqual(record['input_begin_ns'],now)

    def test_personal_review_requires_both_actual_scene_flags_and_network(self):
        for flags in [(False,True,'qemu-ios'),(True,False,'qemu-ios'),(True,True,'Not Connected')]:
            with runtime_fixture() as (_,root,cfg,_):
                with self.assertRaises(RuntimeError):postrun_review.qualify(cfg,root,*flags)
                self.assertFalse((root/'QUALIFIED-RESULT.json').exists())

    def test_postreview_preserves_raw_and_qualifies_separate_bound_files_once(self):
        with runtime_fixture() as (_,root,cfg,_):
            raw=(root/'RESULT.json').read_bytes();native=(root/'cache-trial.json').read_bytes()
            qualified=postrun_review.qualify(cfg,root,True,True,'qemu-ios')
            self.assertEqual((root/'RESULT.json').read_bytes(),raw)
            self.assertEqual((root/'cache-trial.json').read_bytes(),native)
            self.assertEqual(qualified['status'],'SCOPED_PASS')
            self.assertFalse(qualified['home_operator_attestation'])
            postrun_review.validate_qualification(cfg,qualified,root/'QUALIFIED-RESULT.json',root/'cache-trial-qualified.json')
            with self.assertRaisesRegex(RuntimeError,'no overwrite'):postrun_review.qualify(cfg,root,True,True,'qemu-ios')

    def test_failed_dirty_unretired_or_rewritten_runtime_never_qualifies(self):
        for changes in [dict(status='FAIL'),dict(dirty=True),dict(strict_retirement=False),dict(home_operator_attestation=True),
                        dict(signals=[{'signal':15}]),dict(error='timeout')]:
            with self.subTest(changes=changes),runtime_fixture() as (_,root,cfg,result):
                save(root/'RESULT.json',dict(result,**changes))
                with self.assertRaises(RuntimeError):postrun_review.qualify(cfg,root,True,True,'qemu-ios')
        with runtime_fixture() as (_,root,cfg,_),patch.object(postrun_review,'same_process',return_value=True):
            with self.assertRaisesRegex(RuntimeError,'process retirement'):postrun_review.qualify(cfg,root,True,True,'qemu-ios')

    def test_reuse_rejects_provisional_or_mutated_qualified_derivative(self):
        with runtime_fixture() as (_,root,cfg,result):
            with self.assertRaisesRegex(RuntimeError,'separate Root-qualified'):
                postrun_review.validate_qualification(cfg,result,root/'RESULT.json',root/'cache-trial.json')
            qualified=postrun_review.qualify(cfg,root,True,True,'qemu-ios')
            qualified['home_operator_attestation']=True
            with self.assertRaisesRegex(RuntimeError,'derivative changed'):
                postrun_review.validate_qualification(cfg,qualified,root/'QUALIFIED-RESULT.json',root/'cache-trial-qualified.json')
        with runtime_fixture() as (_,root,cfg,_):
            qualified=postrun_review.qualify(cfg,root,True,True,'qemu-ios')
            path=root/'ROOT-SCENE-REVIEW.json';review=read(path);review['observed_wifi_name']='Not Connected';save(path,review)
            with self.assertRaises(RuntimeError):postrun_review.validate_qualification(cfg,qualified,root/'QUALIFIED-RESULT.json',root/'cache-trial-qualified.json')


if __name__ == '__main__':
    unittest.main()
