#!/usr/bin/env python3
"""The Windows release package, run by scripts/build-release.sh after Windows/tools/build-engine.sh:
links the SDL worker against the release engine objects, collects the native DLL closure, stages the
app with its Python/Tk runtime, the app services and the C# helper, adds licences and provenance, and
zips it.

usage: package-windows.py WORKSPACE VERSION WINDOWS_INPUTS HELPER
  WORKSPACE       the release workspace (app/, engine/, win-cross/, win-deps/ from the earlier steps)
  VERSION         e.g. v0.0.8
  WINDOWS_INPUTS  python-tk01/ (CPython + Tk) and services-mobile01/ (app-service DLLs)
  HELPER          the published LightTouchWin helper (dotnet publish output)
"""
import hashlib, json, pathlib, shutil, subprocess, sys, zipfile

R, VERSION, W, HELPER = pathlib.Path(sys.argv[1]), sys.argv[2], pathlib.Path(sys.argv[3]), pathlib.Path(sys.argv[4])
app, qemu, cross = R / 'app', R / 'engine', R / 'win-cross'
out_root = R / 'wbuild/package'
NAME = 'LightTouch-' + VERSION + '-Windows-x86_64'
(R / 'evidence-win').mkdir(exist_ok=True)


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as s:
        for b in iter(lambda: s.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def rev(repo):
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()


def run(argv, log):
    with open(R / 'evidence-win' / log, 'w') as stream:
        subprocess.run(list(map(str, argv)), stdout=stream, stderr=subprocess.STDOUT, check=True)


for repo in (app, qemu):
    assert not subprocess.check_output(['git', 'status', '--porcelain'], cwd=repo, text=True).strip(), repo

worker_dir = R / 'wbuild/worker'
run([sys.executable, app / 'Windows/tools/build-shared-worker.py', '--qemu', qemu, '--cache', cross / 'engine',
     '--response', cross / 'engine/qemu-system-arm.exe.rsp', '--output', worker_dir], 'windows-worker-build.log')
worker = worker_dir / 'lighttouch-windows-worker.exe'

native = R / 'wbuild/native-runtime'
sysroot = R / 'win-deps/sysroot'
run([sys.executable, app / 'Windows/tools/package-runtime.py', '--engine', worker,
     '--dll-dir', sysroot / 'mingw64/bin', '--dll-dir', sysroot / 'codec/bin', '--destination', native],
    'windows-native-closure.log')

out_root.mkdir(parents=True)
out = out_root / NAME
python_runtime = W / 'python-tk01/extracted/python'
services = W / 'services-mobile01/bin'
run([sys.executable, app / 'Windows/tools/stage-shared.py', '--source', app, '--output', out,
     '--python-runtime', python_runtime, '--helper', HELPER, '--worker', worker,
     '--native-runtime', native, '--services-runtime', services], 'windows-stage.log')

(out / 'docs').mkdir()
shutil.copy2(app / 'docs/ASSETS.md', out / 'docs/ASSETS.md')

# Licenses and notices: LightTouch, QEMU, FFmpeg and every bundled native provider's own texts.
lic = out / 'licenses'
lic.mkdir()
shutil.copy2(app / 'LICENSE', lic / 'LightTouch-LICENSE.txt')
for name in ('LICENSE', 'COPYING', 'COPYING.LIB'):
    if (qemu / name).is_file():
        shutil.copy2(qemu / name, lic / ('qemu-ios-' + name + '.txt'))
ffmpeg = cross / 'ffmpeg-9.0.1'
for name in ('LICENSE.md', 'COPYING.LGPLv2.1'):
    shutil.copy2(ffmpeg / name, lic / ('ffmpeg-' + name))
shutil.copytree(sysroot / 'mingw64/share/licenses', lic / 'mingw-runtime')
shutil.copytree(W / 'services-mobile01/licenses', lic / 'app-services')
shutil.copy2(W / 'services-mobile01/libimobiledevice-1.4.0/COPYING.LESSER', lic / 'app-services/libimobiledevice-COPYING.LESSER')
shutil.copy2(python_runtime / 'LICENSE.txt', lic / 'Python-LICENSE.txt')
app_rev, qemu_rev = rev(app), rev(qemu)
(lic / 'SOURCE.txt').write_text(
    'LightTouch ' + VERSION + ' Windows x86_64 package. No firmware, guest assets or saves are included.\r\n'
    'LightTouch source: https://github.com/blake-lucas/LightTouch commit ' + app_rev + '\r\n'
    'qemu-ios engine source: https://github.com/blake-lucas/qemu-ios commit ' + qemu_rev + ' \r\n'
    'FFmpeg 9.0.1 (LGPL-2.1, decoders aac/mp3/alac/h264 only) built with qemu-ios contrib/ffmpeg patches.\r\n'
    'Other native DLLs are MinGW-w64/MSYS2 package builds; their license texts are under mingw-runtime/ and app-services/.\r\n'
    'Corresponding source for GPL/LGPL components is available on request from the commits above and the upstream package versions.\r\n',
    encoding='utf-8')

with (out / 'README.txt').open('a', encoding='utf-8') as f:
    f.write('\r\n' + VERSION + ' build (' + app_rev[:10] + ' app, ' + qemu_rev[:10] + ' engine). '
            'Extract the whole ZIP into a fresh folder and run LightTouch.cmd. Engine, FFmpeg and SDL worker built from those revisions; '
            'the Windows CPython/Tk runtime, app-service DLLs and helper are prebuilt inputs.\r\n')

prov = out / 'provenance'
prov.mkdir()
shutil.copy2(worker_dir / 'BUILD-PROVENANCE.json', prov / 'worker-build.json')
shutil.copy2(native / 'runtime-manifest.json', prov / 'native-runtime-manifest.json')
shutil.copy2(cross / 'PE-IMPORTS.txt', prov / 'engine-PE-IMPORTS.txt')
(prov / 'SOURCE-PROVENANCE.json').write_text(json.dumps(dict(
    ui_revision=app_rev, worker_revision=app_rev, engine_revision=qemu_rev,
    engine_objects_rebuilt=True, ffmpeg_rebuilt=True, helper_rebuilt=False, helper=str(HELPER),
    reused=dict(python_runtime=str(python_runtime), services_runtime=str(services),
                mingw_dependency_sysroot=str(W / 'dependencies01')),
    windows_target_tested=False, guest_qualified=False), indent=2) + '\n')

records = [dict(file=p.relative_to(out).as_posix(), bytes=p.stat().st_size, sha256=sha(p))
           for p in sorted(out.rglob('*')) if p.is_file() and p.name != 'FILE-MANIFEST.json']
(out / 'FILE-MANIFEST.json').write_text(json.dumps(dict(schema=1, app_revision=app_rev, engine_revision=qemu_rev, files=records), indent=2) + '\n')

archive = out_root / (NAME + '.zip')
with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for p in sorted(out.rglob('*')):
        if p.is_file():
            z.write(p, p.relative_to(out_root).as_posix())
with zipfile.ZipFile(archive) as z:
    names = z.namelist()
    assert len(names) == len(set(names)) == len(records) + 1
    assert z.testzip() is None
    for r in records:
        if r['file'].startswith(('runtime/native/', 'runtime/helper/LightTouchWin', 'ui/', 'docs/', 'licenses/')):
            assert hashlib.sha256(z.read(NAME + '/' + r['file'])).hexdigest() == r['sha256'], r['file']
print(json.dumps(dict(archive=str(archive), bytes=archive.stat().st_size, sha256=sha(archive), files=len(records))))
