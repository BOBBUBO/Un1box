#!/usr/bin/env python3
"""Production template policy against the actual Root-vetted A02 images; no guest."""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('witness', Path(__file__).with_name('template-home-witness.py'))
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)
REFS = None


def pixel(data, x, y, channel=0):
    data = bytearray(data)
    height = w.LOCK.bitmap(data)
    data[54 + ((y if height < 0 else 479 - y) * 320 + x) * 4 + channel] ^= 1
    return bytes(data)


class ActualReferences(unittest.TestCase):
    def setUp(self):
        self.refs = REFS
        self.identity = dict(frontend_pid=12, start_ticks='34', hello_monotonic_ns=1_000_000_000)
        self.worker = dict(pid=13, start_ticks='35')
        def receipt(t, seq):
            return dict(sequence=seq, coherent_readback=1, shared_generation=seq * 2,
                        observed_monotonic_ns=t, saved_monotonic_ns=t + 1,
                        frame_monotonic_ns=t - 20, diagnostics_monotonic_ns=t - 10,
                        copy_started_monotonic_ns=t - 2, copy_finished_monotonic_ns=t - 1,
                        sleeping=0, backlight=245, agent=1)
        self.lock = receipt(2_000_000_000, 2)
        self.home = receipt(3_000_000_000, 3)
        self.gate = dict(self.identity, proof_kind=w.POLICY, model_inspected=False,
                         template_matched=True, scene='Home Screen', current_images_require_Root_review=True,
                         host_boot_id='boot', worker_pid=13, worker_start_ticks='35', window=99,
                         viewport=[480, 800], lock_viewport=[480, 800],
                         lock_verified_monotonic_ns=2_000_000_020,
                         slider_down_begin_monotonic_ns=2_100_000_000,
                         slider_down_end_monotonic_ns=2_100_000_010,
                         slider_up_begin_monotonic_ns=2_499_999_980,
                         slider_up_end_monotonic_ns=2_499_999_990,
                         slider_released_monotonic_ns=2_500_000_000, slider_balanced_contact=True,
                         verified_monotonic_ns=3_000_000_020,
                         lock_status=dict(state=2, sleeping=0, backlight=245, agent=1,
                                          observed_monotonic_ns=2_000_000_010),
                         status=dict(state=2, sleeping=0, backlight=245, agent=1,
                                     observed_monotonic_ns=3_000_000_010))

    def check(self, lock=None, home=None):
        return w.validate_home(self.gate, self.lock, self.home,
                               self.refs['lock'] if lock is None else lock,
                               self.refs['home'] if home is None else home,
                               self.refs, self.identity, self.worker, 'boot', 130_000_000_000, 140_000_000_000)

    def test_actual_pair_and_honest_template_proof(self):
        self.assertTrue(self.check())
        self.assertFalse(self.gate['model_inspected'])

    def test_home_clock_only_changes_allowed(self):
        self.assertTrue(self.check(home=pixel(self.refs['home'], 145, 4)))

    def test_nonclock_icon_dock_wifi_battery_alpha_and_black_rejected(self):
        for x, y in ((48, 8), (306, 9), (45, 60), (150, 240), (40, 420), (128, 4), (190, 4)):
            self.assertFalse(self.check(home=pixel(self.refs['home'], x, y)), (x, y))
        self.assertFalse(self.check(home=pixel(self.refs['home'], 145, 4, 3)))
        self.assertFalse(self.check(home=self.refs['home'][:54] + bytes(320 * 480 * 4)))

    def test_wrong_scene_and_missing_lock_anchors_reject(self):
        self.assertFalse(self.check(home=self.refs['lock']))
        self.assertFalse(self.check(lock=self.refs['home']))
        for x, y in ((150, 200), (40, 430), (158, 9)):
            self.assertFalse(self.check(lock=pixel(self.refs['lock'], x, y)))

    def test_wrong_worker_start_boot_epoch_window_viewport_reject(self):
        for key, value in (('worker_pid', 14), ('worker_start_ticks', '36'), ('host_boot_id', 'old'),
                           ('frontend_pid', 22), ('start_ticks', 'old'), ('hello_monotonic_ns', 0),
                           ('window', 0), ('viewport', [800, 480])):
            saved = self.gate[key];self.gate[key] = value
            self.assertFalse(self.check(), key);self.gate[key] = saved

    def test_no_inspection_fabrication_or_wrong_scene_gate(self):
        for key, value in (('model_inspected', True), ('template_matched', False),
                           ('scene', 'Notes focused new editor'), ('current_images_require_Root_review', False)):
            saved = self.gate[key];self.gate[key] = value
            self.assertFalse(self.check(), key);self.gate[key] = saved

    def test_stale_future_asleep_unready_odd_torn_copy_reject(self):
        for key, value in (('observed_monotonic_ns', 1), ('frame_monotonic_ns', 4_000_000_000),
                           ('diagnostics_monotonic_ns', 1), ('sleeping', 1), ('agent', 2),
                           ('backlight', 0), ('shared_generation', 5), ('coherent_readback', 0),
                           ('copy_started_monotonic_ns', 1)):
            saved = self.home[key];self.home[key] = value
            self.assertFalse(self.check(), key);self.home[key] = saved
        self.gate['status']['state'] = 3
        self.assertFalse(self.check())

    def test_old_frame_wrong_order_or_late_verification_reject(self):
        self.home['sequence'] = self.lock['sequence'];self.assertFalse(self.check())
        self.home['sequence'] = 3
        self.home['frame_monotonic_ns'] = self.gate['slider_down_end_monotonic_ns'] - 1
        self.assertFalse(self.check())
        self.home['frame_monotonic_ns'] = 2_999_999_980
        self.gate['verified_monotonic_ns'] = 131_000_000_000
        self.assertFalse(self.check())

    def test_static_current_home_valid_but_old_lock_rejected(self):
        self.home['frame_monotonic_ns'] = 2_200_000_000
        self.assertTrue(self.check())
        self.lock['frame_monotonic_ns'] = 1
        self.gate['lock_verified_monotonic_ns'] = 2_100_000_001
        self.assertFalse(self.check())

    def test_render_during_drag_accepted_only_with_capture_after_balanced_up(self):
        self.home['frame_monotonic_ns'] = 2_200_000_000
        self.assertLess(self.home['frame_monotonic_ns'], self.gate['slider_up_begin_monotonic_ns'])
        self.assertTrue(self.check())
        for key, value in (('copy_started_monotonic_ns', 2_499_999_999),
                           ('observed_monotonic_ns', 2_499_999_999)):
            saved=self.home[key];self.home[key]=value
            self.assertFalse(self.check(),key);self.home[key]=saved
        self.gate['slider_balanced_contact']=False
        self.assertFalse(self.check())

    def test_pre_down_source_or_lock_checked_after_down_reject(self):
        self.home['frame_monotonic_ns']=self.gate['slider_down_begin_monotonic_ns']-1
        self.assertFalse(self.check())
        self.home['frame_monotonic_ns']=2_200_000_000
        self.gate['lock_verified_monotonic_ns']=self.gate['slider_down_begin_monotonic_ns']+1
        self.assertFalse(self.check())

    def test_header_shape_and_truncation_reject(self):
        for data in (self.refs['home'][:-1], b'not a bitmap', pixel(self.refs['home'], 145, 4) + b'x'):
            self.assertFalse(self.check(home=data))

    def test_load_actual_watchdog_distinct_proof_schema_and_pin_refusals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entries = {}
            for scene, data, receipt in (('lock', self.refs['lock'], self.lock),
                                          ('home', self.refs['home'], self.home)):
                folder = root / scene;folder.mkdir()
                (folder / 'native.bmp').write_bytes(data)
                (folder / 'receipt.json').write_text(json.dumps(receipt))
                entries[scene] = w.description(folder)
            manifest = dict(schema=1, vetted_by='Root', actual_reference_images_personally_viewed=True,
                            attestation='Host fixture, not guest proof',
                            **{k:dict(v, source_run=str(root)) for k,v in entries.items()})
            path = root / 'refs.json';path.write_text(json.dumps(manifest))
            cfg = dict(template_references_path=str(path), template_references_sha256=w.sha(path))
            (root / 'inputs.json').write_text(json.dumps(cfg))
            (root / 'worker.json').write_text(json.dumps(self.worker))
            (root / 'started.json').write_text(json.dumps(dict(boot_id='boot')))
            (root / 'window.json').write_text(json.dumps(dict(window=99)))
            gate = dict(self.gate, **entries['home'], lock_capture=entries['lock'],
                        reference_manifest_path=str(path), reference_manifest_sha256=w.sha(path))
            (root / 'home-gate.json').write_text(json.dumps(gate))
            def check():
                return w.GATES.load_gate(root, 'home-gate.json', self.identity, 'Home Screen',
                                         130_000_000_000, 140_000_000_000)
            self.assertTrue(check())
            (root / 'window.json').write_text(json.dumps(dict(window=100)))
            self.assertFalse(check())
            (root / 'window.json').write_text(json.dumps(dict(window=99)))
            self.assertFalse(w.GATES.load_gate(root, 'home-gate.json', self.identity,
                                               'Notes focused new editor', 130_000_000_000, 140_000_000_000))
            cfg['template_references_sha256'] = '0' * 64
            (root / 'inputs.json').write_text(json.dumps(cfg));self.assertFalse(check())
            cfg['template_references_sha256'] = w.sha(path)
            (root / 'inputs.json').write_text(json.dumps(cfg))
            (root / 'worker.json').write_text(json.dumps(dict(pid=13, start_ticks='old')))
            self.assertFalse(check())

    def test_real_wait_match_retains_black_reject_then_exact_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = object.__new__(w.Live)
            live.identity = self.identity
            live.deadline = 130_000_000_000
            clock = [2_000_000_020]
            live.viewport = lambda: [480, 800]
            live.status = lambda: self.gate['lock_status']
            calls = []
            def capture():
                folder = Path(tmp) / str(len(calls));folder.mkdir()
                calls.append(clock[0])
                data = self.refs['lock'] if len(calls) == 2 else self.refs['lock'][:54] + bytes(320*480*4)
                return dict(capture_directory=str(folder)), self.lock, data
            live.capture = capture
            def wait(seconds): clock[0] += int(seconds * 1e9)
            with patch.object(w.time, 'monotonic_ns', side_effect=lambda:clock[0]), patch.object(w.time, 'sleep', side_effect=wait):
                result = live.wait_match('lock', self.refs['lock'], 12_000_000_000)
            self.assertEqual(len(calls), 2)
            self.assertGreaterEqual(calls[1]-calls[0], 500_000_000)
            self.assertEqual(result[2], self.refs['lock'])
            self.assertFalse(json.loads((Path(tmp)/'0/template-attempt.json').read_text())['template_matched'])
            self.assertTrue(json.loads((Path(tmp)/'1/template-attempt.json').read_text())['template_matched'])

    def test_real_wait_match_never_extends_original_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = object.__new__(w.Live)
            live.identity = self.identity
            clock = [2_000_000_020]
            live.deadline = clock[0] + 1_000_000_000
            original = live.deadline
            live.viewport = lambda:[480, 800]
            live.status = lambda:self.gate['lock_status']
            count = [0]
            def capture():
                p = Path(tmp)/str(count[0]);p.mkdir();count[0]+=1
                return dict(capture_directory=str(p)), self.lock, self.refs['home']
            live.capture = capture
            def wait(seconds):clock[0]+=int(seconds*1e9)
            with patch.object(w.time, 'monotonic_ns', side_effect=lambda:clock[0]), patch.object(w.time, 'sleep', side_effect=wait):
                with self.assertRaises(ValueError):live.wait_match('lock', self.refs['lock'],12_000_000_000)
            self.assertEqual(live.deadline,original)
            self.assertEqual(count[0],2)
            self.assertEqual(clock[0],original)

    def test_production_slider_releases_after_partial_down_or_move_failure(self):
        class Fake:
            window = 99
            def __init__(self, fail): self.calls=[];self.fail=fail
            def viewport(self): return [480, 800]
            def x(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                self.last_event=dict(start_monotonic_ns=1, end_monotonic_ns=2)
                if args[0] == self.fail:
                    raise ValueError('partial host operation failure')
        for fail in ('mousedown', 'mousemove'):
            live = Fake(fail)
            if fail == 'mousemove':
                # Initial positioning succeeds; fail a held-contact move.
                first = live.x
                def x(*args, **kwargs):
                    if args[0] == 'mousemove' and not live.calls:
                        live.calls.append((args,kwargs));return
                    return first(*args,**kwargs)
                live.x = x
            with patch.object(w.time,'sleep',return_value=None):
                with self.assertRaises(ValueError):w.slider(live,[480,800])
            self.assertEqual(live.calls[-1], (('mouseup','1'),dict(release=True)))

    def test_production_down_admission_runs_after_focus_check_before_xevent(self):
        live = object.__new__(w.Live)
        order=[]
        live.owner=lambda:order.append('owner')
        live.viewport=lambda:order.append('focus')
        def reject():
            order.append('admit');raise ValueError('stale capture')
        live.before_down=reject
        with patch.object(w.subprocess,'run') as child:
            with self.assertRaises(ValueError):live.x('mousedown','1')
            child.assert_not_called()
        self.assertEqual(order,['owner','focus','admit'])

    def test_file_manifest_hash_and_vetting_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entries = {}
            for scene, data in self.refs.items():
                folder = root / scene;folder.mkdir()
                (folder / 'native.bmp').write_bytes(data)
                (folder / 'receipt.json').write_text(json.dumps(self.home))
                entries[scene] = dict(w.description(folder), source_run=str(root))
            manifest = dict(schema=1, vetted_by='Root', actual_reference_images_personally_viewed=True,
                            attestation='Host fixture for policy testing, not actual vetting', **entries)
            path = root / 'refs.json'
            def save():
                path.write_text(json.dumps(manifest));return w.sha(path)
            digest = save()
            self.assertEqual(w.references(path, digest), self.refs)
            for bad in ('0' * 64, digest.upper()):
                with self.assertRaises(ValueError):w.references(path, bad)
            manifest['actual_reference_images_personally_viewed'] = False
            with self.assertRaises(ValueError):w.references(path, save())
            manifest['actual_reference_images_personally_viewed'] = True
            digest = save();(root / 'home/native.bmp').write_bytes(self.refs['lock'])
            with self.assertRaises(ValueError):w.references(path, digest)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--references', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    args = parser.parse_args()
    REFS = w.references(args.references.resolve(), args.sha256)
    unittest.main(argv=['test-template-home'])
