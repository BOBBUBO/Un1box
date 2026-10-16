"""Synthetic shared Windows staging; these fixtures are not runnable binaries."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('windows_stage', SOURCE/'Windows/tools/stage-shared.py')
stage = importlib.util.module_from_spec(spec); spec.loader.exec_module(stage)


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root/'python'
        for name in ['python.exe','pythonw.exe','python313.dll','Lib/tkinter/__init__.py','DLLs/_tkinter.pyd',
                     'tcl/tcl8.6/init.tcl','tcl/tk8.6/tk.tcl']:
            path = self.runtime/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'synthetic non-executable fixture')
        self.helper = self.root/'helper'; self.helper.mkdir(); (self.helper/'LightTouchWin.Headless.exe').write_bytes(b'fake helper')
        self.worker = self.root/'worker.exe'; self.worker.write_bytes(b'fake SDL worker')
        self.native = self.root/'native'; self.native.mkdir(); (self.native/'SDL2.dll').write_bytes(b'fake DLL')
        (self.native/'qemu-system-arm.exe').write_bytes(b'old preview excluded')
        self.output = self.root/'staged'

    def run_stage(self):
        return stage.stage(SOURCE, self.output, self.runtime, self.helper, self.worker, self.native)

    def test_relocated_imports_use_actual_shared_widgets_host_and_services(self):
        self.run_stage()
        import os
        code = '''import launcher
from lighttouch_desktop.launcher import Launcher
from lighttouch_desktop.windows import WindowsHost
import app_services
assert launcher.Launcher is Launcher
assert launcher.WindowsHost is WindowsHost
assert 'Desktop/app_services' not in app_services.__file__
'''
        subprocess.run([sys.executable,'-s','-c',code], cwd=self.output/'ui',
                       env=dict(os.environ,PYTHONPATH=str(self.output/'ui'),PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1'),
                       check=True,capture_output=True,text=True,timeout=5)
        self.assertFalse((self.output/'runtime/native/qemu-system-arm.exe').exists())
        self.assertFalse(list(self.output.rglob('*.itpack')))
        self.assertFalse(list(self.output.rglob('device.lock.json')))

    def test_native_bytes_and_relocatable_python_tk_paths_retained(self):
        result = self.run_stage()
        self.assertFalse(result['windows_target_tested']); self.assertFalse(result['services_qualified'])
        self.assertEqual((self.output/'runtime/native/lighttouch-windows-worker.exe').read_bytes(), self.worker.read_bytes())
        paths = (self.output/'runtime/python/python313._pth').read_text()
        self.assertIn('../../ui', paths); self.assertNotIn('import site', paths)
        command = (self.output/'LightTouch.cmd').read_text()
        self.assertIn('TCL_LIBRARY=%~dp0', command); self.assertIn('TK_LIBRARY=%~dp0', command)
        manifest = json.loads((self.output/'FILE-MANIFEST.json').read_text())
        for record in manifest['files']:
            self.assertEqual(stage.sha(self.output/record['file']), record['sha256'])

    def test_incomplete_python_or_existing_destination_rejected(self):
        (self.runtime/'DLLs/_tkinter.pyd').unlink()
        with self.assertRaisesRegex(ValueError,'complete Windows Python/Tk'): self.run_stage()
        self.assertFalse(self.output.exists())


if __name__ == '__main__': unittest.main()
