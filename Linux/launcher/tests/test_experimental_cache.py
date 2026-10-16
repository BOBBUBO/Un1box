"""Real local directory/env/exec contracts; never boots an iOS guest."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from experimental_cache import KEYS, worker_environment
from model import SetupError


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'library'
        self.data.mkdir(mode=0o700)
        self.profile = dict(id='a'*32, base=str(self.root/'base'),
                            state=str(self.root/'state'), guest_package=str(self.root/'guest.itpack'))
        self.inherited = dict(os.environ, LT_TCG_IR_CACHE='untrusted', LT_TCG_IR_CACHE_ENABLE='experimental-v1', IT_USB_TCP='private-service')

    def child_environment(self, env):
        source = 'import os,json; print(json.dumps({k:os.environ.get(k) for k in '+repr(KEYS)+'}))'
        return json.loads(subprocess.check_output([sys.executable, '-c', source], env=env, text=True))

    def test_off_scrubs_inherited_values_without_writing_or_mutating_parent(self):
        env = worker_environment(self.data, {}, inherited=self.inherited)
        self.assertEqual(self.child_environment(env), dict.fromkeys(KEYS))
        self.assertFalse((self.data/'experimental-tcg-ir-cache').exists())
        self.assertEqual(self.inherited[KEYS[1]], 'untrusted')
        self.assertEqual(env['IT_USB_TCP'], 'private-service')

    def test_explicit_selection_reaches_fresh_process_and_is_private_per_profile(self):
        env = worker_environment(self.data, self.profile, True, self.inherited)
        cache = Path(env[KEYS[1]])
        self.assertEqual(self.child_environment(env), {key:env[key] for key in KEYS})
        self.assertEqual(cache, self.data/'experimental-tcg-ir-cache'/self.profile['id'])
        for folder in [cache.parent, cache]:
            self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
        second = worker_environment(self.data, dict(self.profile,id='b'*32), True, self.inherited)
        self.assertNotEqual(env[KEYS[1]], second[KEYS[1]])
        self.assertEqual(self.inherited[KEYS[1]], 'untrusted')

    def test_existing_records_are_preserved_and_reused(self):
        env = worker_environment(self.data, self.profile, True, self.inherited)
        marker = Path(env[KEYS[1]])/'retained-private-record'
        marker.write_bytes(b'not imported or removed')
        self.assertEqual(worker_environment(self.data,self.profile,True,self.inherited), env)
        self.assertEqual(marker.read_bytes(), b'not imported or removed')

    def test_unsafe_existing_modes_fail_without_repair(self):
        root = self.data/'experimental-tcg-ir-cache'
        root.mkdir(mode=0o755)
        with self.assertRaises(SetupError):worker_environment(self.data,self.profile,True,self.inherited)
        self.assertEqual(stat.S_IMODE(root.stat().st_mode),0o755)
        self.assertFalse((root/self.profile['id']).exists())

    def test_root_and_profile_symlinks_are_never_followed(self):
        root = self.data/'experimental-tcg-ir-cache'
        outside = self.root/'outside';outside.mkdir(mode=0o700)
        for symlink in [root,root/self.profile['id']]:
            if symlink != root:root.mkdir(mode=0o700)
            symlink.symlink_to(outside,target_is_directory=True)
            with self.assertRaises(OSError):worker_environment(self.data,self.profile,True,self.inherited)
            self.assertEqual(list(outside.iterdir()),[])
            symlink.unlink()

    def test_invalid_identity_or_nonboolean_opt_in_never_creates_cache(self):
        for identity in ['../escape','',123,'a'*64]:
            with self.assertRaises(SetupError):
                worker_environment(self.data,dict(self.profile,id=identity),True,self.inherited)
        with self.assertRaises(SetupError):worker_environment(self.data,self.profile,'true',self.inherited)
        self.assertFalse((self.data/'experimental-tcg-ir-cache').exists())

    def test_asset_overlap_rejected_before_directory_creation(self):
        root=self.data/'experimental-tcg-ir-cache'
        for field in ['base','state','guest_package','bootrom']:
            for asset in [self.data,root,root/'asset']:
                with self.assertRaises(SetupError):
                    worker_environment(self.data,dict(self.profile,**{field:str(asset)}),True,self.inherited)
                self.assertFalse(root.exists())


if __name__=='__main__':unittest.main()
