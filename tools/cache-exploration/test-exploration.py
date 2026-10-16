#!/usr/bin/env python3
"""Private source-policy and real filesystem/PIDFD contracts; no engine/guest."""
import copy
import struct
import tempfile
import unittest
from unittest.mock import patch
import shutil
import contextlib
import exploration_common as common
import exploration_control as control

from exploration_common import *
from exploration_control import unchanged_except_clock

class Tests(unittest.TestCase):
    def lock_policy_path(self):
        configured = os.environ.get('LT_CACHE_TEST_LOCK_POLICY')
        return Path(configured).resolve(strict=True) if configured else Path(__file__).resolve().parents[2] / 'Linux/scripts/lock-continuity.py'

    def test_pinned_reference_from_prior_worker_retains_original_awake_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); original = root / 'original'; original.mkdir()
            (original / 'native.bmp').write_bytes(b'original bytes')
            (original / 'native.png').write_bytes(b'personally viewed PNG')
            old = 3 * SECOND
            save(original / 'receipt.json', dict(sha256=digest(original / 'native.bmp'), saved_monotonic_ns=old,
                 sleeping='0', agent='1', backlight='255', frame_monotonic_ns=str(old), diagnostics_monotonic_ns=str(old)))
            policy = self.lock_policy_path()
            reference = dict(scope='PERSONALLY_INSPECTED_N72_LOCK_REFERENCE', captureDirectory=str(original))
            for name, filename in [('bmp','native.bmp'), ('png','native.png'), ('receipt','receipt.json')]:
                reference[name+'SHA256'] = digest(original / filename)
            cfg = dict(lockReference=reference, lockPolicy=str(policy),
                       pins=dict(lockPolicy=dict(path=str(policy), sha256=digest(policy))))
            save(root / 'EXPLORATION-INPUTS.json', cfg)
            with patch.object(control, 'P', root):
                self.assertEqual(control.lock_reference()[0], original)
                (original / 'native.bmp').write_bytes(b'changed bytes')
                with self.assertRaisesRegex(RuntimeError, 'Pinned Lock reference changed'):
                    control.lock_reference()

    @contextlib.contextmanager
    def home_input_fixture(self, change=None, receipt_change=None, move_failure=False):
        # Synthetic actual-format BMP exercises the real exact-anchor comparer;
        # all UI calls are intercepted. These are controller contracts, no guest.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); original = root / 'reference'; original.mkdir()
            bitmap = self.bitmap()
            struct.pack_into('<I', bitmap, 10, 54)
            struct.pack_into('<IiiHHI', bitmap, 14, 40, 320, -480, 1, 32, 0)
            (original / 'native.bmp').write_bytes(bitmap)
            current_bitmap = bytearray(bitmap)
            if change: change(current_bitmap)
            worker = dict(pid=42, start_ticks='123')
            now = time.monotonic_ns()
            save(root / 'hello.json', dict(monotonic_ns=now - SECOND))
            (root / 'session.log').write_text('status state=2 sleeping=1 observed_monotonic_ns=%d\n' % now)
            policy = control.module(self.lock_policy_path())
            events=[]
            def xcall(args, release=False):
                if args==['keyup','h']:
                    instant=time.monotonic_ns()
                    (root/'session.log').write_text('status state=2 sleeping=0 backlight=255 agent=1 observed_monotonic_ns=%d frame_monotonic_ns=%d\n' % (instant,instant))
                events.append((args, release)); return '', 0, 0
            count=0
            def capture(label, convert=True):
                output=root/label;output.mkdir()
                (output/'native.bmp').write_bytes(current_bitmap)
                instant=time.monotonic_ns()
                receipt=dict(worker_pid=42,worker_start='123',sleeping='0',agent='1',backlight='255',
                    frame_monotonic_ns=str(instant),diagnostics_monotonic_ns=str(instant))
                receipt.update(receipt_change or {})
                save(output/'receipt.json',receipt)
                return output,receipt
            def move(x,y,g):
                nonlocal count
                count += 1
                if move_failure and count==3: raise RuntimeError('intercepted input failure')
            with contextlib.ExitStack() as stack:
                patches=[patch.object(control,'R',root), patch.object(control,'lock_reference',return_value=(original,{},policy)),
                    patch.object(control,'owner',return_value=({},worker)),patch.object(control,'geometry',return_value={}),
                    patch.object(control,'capture',side_effect=capture),patch.object(control,'capture_lock',side_effect=contextlib.nullcontext),
                    patch.object(control,'xcall',side_effect=xcall),patch.object(control,'move',side_effect=move),
                    patch.object(control,'convert_bitmap'),patch.object(control.time,'sleep')]
                for item in patches:stack.enter_context(item)
                yield root,events

    def test_atomic_home_lock_slider_retains_both_captures_without_home_attestation(self):
        with self.home_input_fixture() as (root,events):
            control.home_unlock('synthetic')
            self.assertEqual(events[:2], [(['keydown','h'],False),(['keyup','h'],True)])
            self.assertEqual(events[2:], [(['mousedown','1'],False),(['mouseup','1'],True)])
            result=read(root/'synthetic-home-unlock.json')
            self.assertTrue(result['comparison']['matched'])
            self.assertTrue(result['ordinary_balanced_slider'])
            self.assertFalse(result['home_scene_asserted'])
            self.assertFalse(result['guest_acceptance'])
            self.assertFalse((root/'home-inspected.json').exists())
            self.assertTrue((root/'synthetic-lock/native.bmp').exists())
            self.assertTrue((root/'synthetic-post/native.bmp').exists())

    def test_atomic_home_refuses_changed_anchor_sleep_and_worker_identity(self):
        def changed_earth(bitmap):bitmap[54+(150*320+150)*4]=1
        for change,receipt in [(changed_earth,None),(None,dict(sleeping='1')),(None,dict(worker_start='different'))]:
            with self.subTest(change=change,receipt=receipt),self.home_input_fixture(change,receipt) as (root,events):
                with self.assertRaises(RuntimeError):control.home_unlock('rejected')
                self.assertEqual(events,[(['keydown','h'],False),(['keyup','h'],True)])
                result=read(root/'rejected-home-unlock.json')
                self.assertFalse(result['slider_started'])
                self.assertIn('error',result)
                self.assertTrue((root/'rejected-lock/native.bmp').exists())

    def test_atomic_home_releases_slider_after_midgesture_failure(self):
        with self.home_input_fixture(move_failure=True) as (root,events):
            with self.assertRaisesRegex(RuntimeError,'intercepted input failure'):control.home_unlock('interrupted')
            self.assertEqual(events[-1],(['mouseup','1'],True))
            result=read(root/'interrupted-home-unlock.json')
            self.assertTrue(result['slider_started'])
            self.assertIn('slider_released_ns',result)
            self.assertFalse(result['guest_acceptance'])

    def test_awake_wait_allows_delayed_wake_without_additional_home_or_image_guess(self):
        instant=[10*SECOND]
        home_begin=instant[0]
        worker=dict(pid=42,start_ticks='123')
        def status(_):
            awake=instant[0]-home_begin >= 300000000
            frame=home_begin+200000000 if awake else home_begin-SECOND
            return ['status state=2 sleeping=%d backlight=%d agent=1 observed_monotonic_ns=%d frame_monotonic_ns=%d' %
                    (0 if awake else 1,255 if awake else 0,instant[0],frame)]
        def pause(seconds):instant[0]+=round(seconds*SECOND)
        record={}
        with patch.object(control.time,'monotonic_ns',side_effect=lambda:instant[0]),patch.object(control.time,'sleep',side_effect=pause), \
             patch.object(control,'owner',return_value=({},worker)),patch.object(control,'rows',side_effect=status), \
             patch.object(control,'xcall') as input_call,patch.object(control,'capture') as capture_call:
            admitted=control.wait_awake_frame(home_begin,home_begin+8*SECOND,worker,record)
            self.assertEqual(instant[0]-home_begin,300000000)
            self.assertEqual(admitted['sleeping'],'0')
            self.assertGreater(len(record['awake_wait_observations']),1)
            input_call.assert_not_called();capture_call.assert_not_called()

    def test_awake_wait_rejects_stale_frame_and_reserves_original_deadline(self):
        for remaining in [8*SECOND,5*SECOND]:
            with self.subTest(remaining=remaining):
                instant=[10*SECOND];begin=instant[0];worker=dict(pid=42,start_ticks='123');record={}
                def pause(seconds):instant[0]+=round(seconds*SECOND)
                def status(_):return ['status state=2 sleeping=0 backlight=255 agent=1 observed_monotonic_ns=%d frame_monotonic_ns=%d' % (instant[0],begin-1)]
                with patch.object(control.time,'monotonic_ns',side_effect=lambda:instant[0]),patch.object(control.time,'sleep',side_effect=pause), \
                     patch.object(control,'owner',return_value=({},worker)),patch.object(control,'rows',side_effect=status),patch.object(control,'xcall') as inputs:
                    with self.assertRaisesRegex(RuntimeError,'awake/new-frame wait expired'):
                        control.wait_awake_frame(begin,begin+remaining,worker,record)
                    self.assertEqual(instant[0],min(begin+2500000000,begin+remaining-4*SECOND))
                    inputs.assert_not_called()

    def test_awake_wait_rejects_current_worker_change_and_nonrunning_guest(self):
        now=time.monotonic_ns();worker=dict(pid=42,start_ticks='123')
        for current,state in [(dict(pid=42,start_ticks='other'),2),(worker,4)]:
            with self.subTest(current=current,state=state), \
                 patch.object(control,'owner',return_value=({},current)), \
                 patch.object(control,'rows',return_value=['status state=%d observed_monotonic_ns=%d' % (state,now)]):
                record={}
                with self.assertRaises(RuntimeError):control.wait_awake_frame(now,now+8*SECOND,worker,record)
                self.assertIn('awake_wait_end_ns',record)

    def test_actual_converter_with_restricted_path_and_shadowed_basename(self):
        executable = os.environ.get('LT_CACHE_TEST_FFMPEG') or shutil.which('ffmpeg')
        self.assertTrue(executable, 'Provide real ffmpeg with LT_CACHE_TEST_FFMPEG when not in PATH')
        executable = Path(executable).resolve(strict=True)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save(root / 'EXPLORATION-TOOLS.json', {'ffmpeg': {'path': str(executable), 'sha256': digest(executable)}})
            bitmap = bytearray(54 + 2 * 2 * 4)
            bitmap[:2] = b'BM'
            struct.pack_into('<I', bitmap, 2, len(bitmap)); struct.pack_into('<I', bitmap, 10, 54)
            struct.pack_into('<IiiHHI', bitmap, 14, 40, 2, -2, 1, 32, 0)
            bitmap[54:] = bytes([0, 0, 255, 0]) * 4
            source = root / 'synthetic.bmp'; source.write_bytes(bitmap)
            # No executable lookup succeeds through PATH, yet actual conversion
            # succeeds using the already pinned absolute executable.
            with patch.object(common, 'P', root), patch.dict(os.environ, {'PATH': '/nonexistent'}):
                convert_bitmap(source, root / 'restricted.png')
            self.assertTrue((root / 'restricted.png').read_bytes().startswith(b'\x89PNG\r\n\x1a\n'))
            shadow = root / 'ffmpeg'
            shadow.write_text('#!/bin/sh\ntouch "' + str(root / 'shadow-was-run') + '"\nexit 99\n')
            shadow.chmod(0o700)
            with patch.object(common, 'P', root), patch.dict(os.environ, {'PATH': str(root)}):
                convert_bitmap(source, root / 'shadowed.png')
            self.assertFalse((root / 'shadow-was-run').exists())
            self.assertEqual((root / 'restricted.png').read_bytes(), (root / 'shadowed.png').read_bytes())

    def test_pinned_converter_hash_mismatch_rejects_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); executable = root / 'converter'
            executable.write_text('#!/bin/sh\nexit 99\n'); executable.chmod(0o700)
            save(root / 'EXPLORATION-TOOLS.json', {'ffmpeg': {'path': str(executable), 'sha256': '0' * 64}})
            with patch.object(common, 'P', root), self.assertRaisesRegex(RuntimeError, 'Pinned helper changed'):
                tool('ffmpeg')

    def test_full_inventory_content_mode_and_special_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'nand').mkdir()
            leaf = root / 'nand/page'
            leaf.write_bytes(b'abcd')
            first = inventory(root)
            leaf.write_bytes(b'abce')
            second = inventory(root)
            self.assertNotEqual(first['inventorySHA256'], second['inventorySHA256'])
            leaf.chmod(0o400)
            self.assertNotEqual(second['inventorySHA256'], inventory(root)['inventorySHA256'])
            (root / 'alias').symlink_to(leaf)
            with self.assertRaises(RuntimeError): inventory(root)

    def test_inventory_rejects_directory_symlink_without_following(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'outside').mkdir(); (root / 'inside').mkdir()
            (root / 'inside/alias').symlink_to(root / 'outside', target_is_directory=True)
            with self.assertRaises(RuntimeError): inventory(root / 'inside')

    def test_lease_refuses_dirty_without_deleting_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / '.lease').touch(); (root / '.dirty').touch()
            with self.assertRaises(RuntimeError):
                with free_lease(root): self.fail('Dirty lease admitted')
            self.assertTrue((root / '.dirty').exists())

    def test_actual_pidfd_tracks_child_retirement_not_reused_pid(self):
        child = subprocess.Popen([__import__('sys').executable, '-c', 'pass'])
        fd = os.pidfd_open(child.pid)
        child.wait(timeout=3)
        self.assertFalse(alive(fd))
        os.close(fd)

    def test_live_identity_binds_start_argv_and_uid(self):
        current = process(os.getpid())
        self.assertTrue(same_process(current))
        for key, wrong in [('start_ticks','-1'), ('argv',['unrelated']), ('uid',-1)]:
            altered = dict(current); altered[key] = wrong
            self.assertFalse(same_process(altered))

    def test_strict_stop_requires_all_native_fields_and_zero_exit(self):
        values = dict(status_available='1',state='0',confirmed='1',dirty='0',worker_reaped='1',
                      worker_exit='0',worker_disposal_requested='0',frontend_return='0')
        line = lambda v:'FINAL '+' '.join(k+'='+x for k,x in v.items())
        self.assertTrue(strict_final(0,line(values)))
        self.assertFalse(strict_final(1,line(values)))
        for key in values:
            altered = dict(values); altered.pop(key)
            self.assertFalse(strict_final(0,line(altered)))
            altered[key] = '9'
            self.assertFalse(strict_final(0,line(altered)))

    def test_grant_uses_integer_lifetime_boot_and_exact_packet(self):
        grant = dict(scope='ONE_LINUX_N72_CANDIDATE_EXPLORATION',hostBootId='boot',packetSHA256='hash',
                     issuedMonotonicNS=100,expiresMonotonicNS=100+110*SECOND)
        self.assertTrue(grant_valid(grant,'boot','hash',101))
        for changes in [dict(issuedMonotonicNS=True),dict(expiresMonotonicNS=101+120*SECOND),
                        dict(expiresMonotonicNS=100),dict(scope='other'),dict(hostBootId='old'),dict(packetSHA256='old')]:
            self.assertFalse(grant_valid(dict(grant,**changes),'boot','hash',101))
        self.assertFalse(grant_valid(grant,'boot','hash',99))
        self.assertFalse(grant_valid(grant,'boot','hash',grant['expiresMonotonicNS']+1))

    def test_cgroup_controls_require_finite_swap_cpu_memory_pid_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = {'memory.max':str(4*1024**3),'memory.swap.max':'0','cpu.max':'200000 100000','pids.max':'192'}
            for key, value in valid.items():(root/key).write_text(value)
            self.assertEqual(bound_cgroup(root),valid)
            for key,value in [('memory.max','max'),('memory.swap.max','max'),('cpu.max','max 100000'),
                              ('cpu.max','300000 100000'),('pids.max','max')]:
                (root/key).write_text(value)
                with self.assertRaises(RuntimeError):bound_cgroup(root)
                (root/key).write_text(valid[key])

    def test_guest_mapping_reserves_actual_88_pixel_toolbar(self):
        for width,height in [(480,800),(800,1000),(800,800),(320,240)]:
            v=viewport(width,height)
            self.assertGreater(v['scale'],0)
            self.assertLessEqual(v['top']+v['height'],height-88+1e-9)
            self.assertAlmostEqual(v['width']/320,v['height']/480)
            self.assertAlmostEqual(v['left']*2+v['width'],width)
        with self.assertRaises(RuntimeError):viewport(480,88)

    def bitmap(self):
        data=bytearray(54+320*480*4);data[:2]=b'BM'
        struct.pack_into('<I',data,2,len(data));struct.pack_into('<ii',data,18,320,-480)
        return data

    def test_home_continuity_protects_every_alpha_and_nonclock_pixel(self):
        first=self.bitmap();current=copy.copy(first)
        current[54+(2*320+130)*4]=1
        self.assertTrue(unchanged_except_clock(first,current))
        current[54+(2*320+130)*4+3]=1
        self.assertFalse(unchanged_except_clock(first,current))
        current=copy.copy(first);current[54+(100*320+130)*4]=1
        self.assertFalse(unchanged_except_clock(first,current))
        self.assertFalse(unchanged_except_clock(first,first[:-1]))

    def test_fresh_capture_binds_source_diagnostic_ages_and_awake_state(self):
        now=10*SECOND
        row=dict(sleeping='0',agent='1',backlight='255',frame_monotonic_ns=str(now-SECOND),
                 diagnostics_monotonic_ns=str(now-SECOND//2))
        self.assertTrue(fresh(row,now))
        for changes in [dict(sleeping='1'),dict(agent='0'),dict(backlight='0'),
                        dict(frame_monotonic_ns=str(now-3*SECOND)),dict(diagnostics_monotonic_ns=str(now-2*SECOND))]:
            self.assertFalse(fresh(dict(row,**changes),now))

if __name__=='__main__':unittest.main()
