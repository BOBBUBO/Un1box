#!/usr/bin/env python3
"""Stage the actual shared Tk/SDL product from separately verified inputs.

No compiler, runtime download or fixture operation. Caller supplies a complete
relocatable Windows CPython/Tk runtime, tested headless publish, compiled SDL
worker and its audited native DLL directory. A staged tree is not a target pass.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''): digest.update(block)
    return digest.hexdigest()


def copy_tree(source, destination):
    source = Path(source)
    if source.is_symlink() or not source.is_dir(): raise ValueError('Choose a real input directory: '+str(source))
    for path in source.rglob('*'):
        if path.is_symlink() or getattr(path, 'is_junction', lambda: False)(): raise ValueError('Runtime links are unsupported: '+str(path))
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))


def stage(source, output, python_runtime, helper, worker, native_runtime, services_runtime=None):
    source, output, python_runtime, helper, worker, native_runtime = map(Path, (source, output, python_runtime, helper, worker, native_runtime))
    if output.exists() or output.is_symlink(): raise ValueError('Choose a new staging directory.')
    for name in ('python.exe', 'pythonw.exe', 'Lib/tkinter/__init__.py', 'DLLs/_tkinter.pyd'):
        if not (python_runtime/name).is_file(): raise ValueError('A complete Windows Python/Tk runtime is required: '+name)
    versions = [p.stem for p in python_runtime.glob('python3*.dll') if re.fullmatch('python3[0-9]{1,2}', p.stem)]
    if len(versions) != 1: raise ValueError('Expected one CPython version DLL.')
    tcl = [p for p in (python_runtime/'tcl').glob('tcl8.*') if (p/'init.tcl').is_file()]
    tk = [p for p in (python_runtime/'tcl').glob('tk8.*') if (p/'tk.tcl').is_file()]
    if len(tcl) != 1 or len(tk) != 1: raise ValueError('The Python runtime must include its matching Tcl/Tk scripts.')
    if not (helper/'LightTouchWin.Headless.exe').is_file() or not worker.is_file(): raise ValueError('Build the actual headless helper and maintained SDL worker first.')
    output.mkdir(parents=True)
    ui = output/'ui'; ui.mkdir()
    shutil.copy2(source/'Windows/launcher.py', ui/'launcher.py')
    for module in ('lighttouch_desktop', 'app_services'): copy_tree(source/'Desktop'/module, ui/module)
    runtime = output/'runtime'; runtime.mkdir()
    copy_tree(helper, runtime/'helper')
    copy_tree(python_runtime, runtime/'python')
    # The native worker uses its audited DLL closure, not the standalone preview
    # executable or stale manifest. Preserve every dependency's exact bytes.
    native = runtime/'native'; native.mkdir()
    for path in native_runtime.iterdir():
        if path.suffix.lower() == '.dll':
            if path.is_symlink() or not path.is_file(): raise ValueError('Native DLL must be a regular file.')
            shutil.copy2(path, native/path.name)
    shutil.copy2(worker, native/'lighttouch-windows-worker.exe')
    services_argument = ''
    if services_runtime is not None:
        services_runtime = Path(services_runtime)
        for name in ('lighttouch-usbmuxd.exe', 'libimobiledevice-1.0.dll', 'libplist-2.0.dll', 'libusbmuxd-2.0.dll'):
            if not (services_runtime/name).is_file(): raise ValueError('Missing guest app-service provider: '+name)
        copy_tree(services_runtime, runtime/'services')
        services_argument = ' --services-runtime "%~dp0runtime\\services"'
    (runtime/'python'/(versions[0]+'._pth')).write_text(versions[0]+'.zip\n.\nDLLs\nLib\n../../ui\n', encoding='utf-8')
    # _pth supplies isolated, relocatable imports; no ambient site-packages.
    batch = ('@echo off\r\nsetlocal\r\n'
             'set "TCL_LIBRARY=%~dp0runtime\\python\\tcl\\'+tcl[0].name+'"\r\n'
             'set "TK_LIBRARY=%~dp0runtime\\python\\tcl\\'+tk[0].name+'"\r\n'
             '"%~dp0runtime\\python\\pythonw.exe" -I "%~dp0ui\\launcher.py" '
             '--frontend "%~dp0runtime\\helper\\LightTouchWin.Headless.exe" '
             '--worker "%~dp0runtime\\native\\lighttouch-windows-worker.exe"'+services_argument+'\r\n')
    (output/'LightTouch.cmd').write_bytes(batch.encode('utf-8'))
    (output/'README.txt').write_text('Shared Tk library/import UI and maintained SDL guest controls. Native target acceptance is pending. Use LightTouch.cmd; configurations and writable data default to %LOCALAPPDATA%\\LightTouch\\SharedWindows. Prepared firmware/guest tools are supplied separately. Guest service transport/LegacyStore/IPA integration is not yet qualified for this Windows adapter. The older C# preview library is never automatically migrated.\n', encoding='utf-8')
    records = [{'file':str(p.relative_to(output)).replace('\\','/'), 'bytes':p.stat().st_size, 'sha256':sha(p)} for p in sorted(output.rglob('*')) if p.is_file()]
    result = {'status':'STAGED_ONLY', 'ui':'maintained shared Tk and SDL', 'files':records,
              'windows_target_tested':False, 'guest_qualified':False, 'services_qualified':False,
              'transition_debt':'C# headless archive/recipe/package algorithms remain until portable admission supports Windows.'}
    (output/'FILE-MANIFEST.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source','output','python-runtime','helper','worker','native-runtime'): parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--services-runtime', type=Path)
    args = parser.parse_args()
    stage(args.source, args.output, args.python_runtime, args.helper, args.worker, args.native_runtime, args.services_runtime)


if __name__ == '__main__': main()
