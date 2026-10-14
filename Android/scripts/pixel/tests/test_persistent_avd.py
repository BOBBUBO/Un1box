# SPDX-License-Identifier: GPL-2.0-or-later
import hashlib,json,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import persistent_avd as persistent
import pixel_supervisor


class Persistent(unittest.TestCase):
    def test_attach_flag_is_separate_from_original_guest_memory_and_clock(self):
        owner,rest=pixel_supervisor.attach_options(['--attach-avd-owner','/private/owner.json','--avd','same'])
        self.assertEqual(owner,'/private/owner.json');self.assertEqual(rest,['--avd','same'])

    def test_owner_reused_pid_or_changed_android_boot_refuses_attachment(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'owner.json';owner={'schema':1,'avd':'same','serial':'emulator-5554','pageBytes':16384,'ramMB':8192,
                'wrapperPid':123,'wrapperStartTicks':10,'qemuPid':124,'qemuStartTicks':11,'bootId':'original'}
            p.write_text(json.dumps(owner));run=mock.Mock(return_value=subprocess.CompletedProcess([],0,b'original\n',b''))
            with mock.patch.object(persistent,'identity',side_effect=lambda pid:{123:10,124:11}[pid]):
                value,pin=persistent.load_owner(p,'same','/synthetic/adb',run=run)
                self.assertEqual(value,owner);self.assertEqual(pin,hashlib.sha256(p.read_bytes()).hexdigest())
                run.return_value=subprocess.CompletedProcess([],0,b'changed\n',b'')
                with self.assertRaises(ValueError):persistent.load_owner(p,'same','/synthetic/adb',run=run)
            with mock.patch.object(persistent,'identity',return_value=999):
                with self.assertRaises(ValueError):persistent.load_owner(p,'same','/synthetic/adb',run=run)

    def test_guest_cleanup_stop_failure_still_retires_only_guest_helpers_and_preserves_avd(self):
        for fail in (False,True):
            with self.subTest(fail=fail):
                calls=[];avd=mock.Mock(pid=123);avd.poll.return_value=None
                complete=mock.Mock();watch=mock.Mock(pid=44);watch.is_alive.return_value=False
                a=SimpleNamespace(stop_attempted=False,avd=avd,args=SimpleNamespace(adb='/synthetic/adb'),
                    output=Path('/synthetic/private'),deadline=time.monotonic()+900,complete=complete,
                    watcher=watch,logcat=None,start=time.monotonic(),write=lambda name,value:calls.append((name,value)))
                def stop():
                    calls.append(('Stop',{}))
                    if fail:raise RuntimeError('Stop unconfirmed')
                a.stop=stop
                with mock.patch.object(persistent,'force_bound_worker',return_value={'guestShutdownConfirmed':False}), \
                     mock.patch.object(persistent.subprocess,'run') as run:
                    if fail:
                        with self.assertRaises(RuntimeError):persistent.cleanup_attempt(a)
                    else:persistent.cleanup_attempt(a)
                run.assert_not_called();avd.terminate.assert_not_called();avd.kill.assert_not_called()
                complete.set.assert_called_once();self.assertEqual(calls[-1][0],'terminal.json')
                self.assertTrue(calls[-1][1]['emulatorPreserved'])

    def force_case(self,changed=False):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);clock=b'actual immutable context';(out/'clock-context.json').write_bytes(clock)
            b={'appUid':10215,'workerPid':42,'workerStartTicks':1234,'bootId':'11111111-2222-3333-4444-555555555555',
                'recordId':'a'*32,'attemptToken':'b'*32,'originToken':hashlib.sha256(clock).hexdigest(),'originNs':1}
            (out/'pixel-bound.json').write_text(json.dumps(b));calls=[]
            stat=('42 (engine) '+' '.join(['S']+['0']*18+['9999' if changed else '1234'])).encode()
            values={'/proc/sys/kernel/random/boot_id':b['bootId'].encode(),'files/library/selected':b['recordId'].encode(),
                '/proc/42/stat':stat,'/proc/42/status':b'Uid: 10215 10215 10215 10215\n','/proc/42/cmdline':b'org.lighttouch.android:engine\0'}
            def run(argv,**kw):
                calls.append(argv)
                return subprocess.CompletedProcess(argv,0,values.get(argv[-1],b''),b'')
            result=persistent.force_bound_worker('/synthetic/adb',out,10,run=run,now=lambda:1)
            return result,calls

    def test_hard_fallback_targets_original_worker_only_never_avd_or_replacement(self):
        result,calls=self.force_case()
        self.assertTrue(result['originalWorkerKillRequested'])
        self.assertEqual(calls[-1][-3:],['kill','-9','42'])
        self.assertFalse(any('emu' in argv or 'am' in argv for argv in calls))
        self.assertFalse(result['guestShutdownConfirmed'])
        result,calls=self.force_case(changed=True)
        self.assertFalse(result['originalWorkerKillRequested']);self.assertFalse(any('kill' in argv for argv in calls))

    def test_owner_rejects_existing_serial_before_any_launch(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);launcher=root/'launcher';launcher.write_text('# synthetic')
            plan={'output':str(root/'owner'),'avd':'LightTouch_pixel_10_API36_16384_24_8192','ramMB':8192,'pageBytes':16384,
                'launcher':str(launcher),'launcherSha256':hashlib.sha256(launcher.read_bytes()).hexdigest(),'adb':'/synthetic/adb'}
            path=root/'plan.json';path.write_text(json.dumps(plan));(root/'ROOT-PERSISTENT-AVD-GRANT.json').write_text(json.dumps({'owner':'/root/pixel','persistentAvdAuthorized':True}))
            with mock.patch.object(persistent.subprocess,'run',return_value=subprocess.CompletedProcess([],0,b'List of devices\nemulator-5554\tdevice\n',b'')),mock.patch.object(persistent.subprocess,'Popen') as spawn:
                with self.assertRaises(ValueError):persistent.launch_owner(path)
                spawn.assert_not_called()


if __name__=='__main__':unittest.main()
