#!/usr/bin/env python3
"""Incrementally link the shared SDL worker from an audited native cache.

Source/compilation only. Caller must provide a finite externally admitted
memory/CPU/time scope. Never reconfigure QEMU or mutate cached link inputs.
Runtime asset admission and a self-contained Tk launcher are separate stages.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess


def sha(path):
    with Path(path).open('rb') as stream:
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--response', type=Path, required=True, help='Audited latest engine response retaining NOR/FMSS fixes')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    app = Path(__file__).resolve().parents[2]
    qemu, cache, output = (path.resolve() for path in (args.qemu, args.cache, args.output))
    for source in (app, qemu):
        if subprocess.check_output(['git', 'status', '--porcelain'], cwd=source, text=True).strip():
            raise RuntimeError('Commit all input source before compilation: ' + str(source))
    if output.exists():
        raise RuntimeError('Refusing existing build output: ' + str(output))
    commands = json.loads((cache/'compile_commands.json').read_text())
    original = [c for c in commands if c['file'].endswith('/hw/arm/ipod_touch_fmss.c')]
    if len(original) != 1:
        raise RuntimeError('Expected exactly one cached portable ARM C command.')
    template = shlex.split(original[0]['command'])
    response = shlex.split(args.response.read_text())
    main_object = 'qemu-system-arm.exe.p/system_main.c.obj'
    if response.count(main_object) != 1 or response[:1] != ['-o']:
        raise RuntimeError('Cached link has an unexpected main/output configuration.')
    protected = []
    for token in response[2:]:
        path = Path(token)
        if path.suffix in ('.o', '.obj', '.a') and not token.startswith('-'):
            path = path if path.is_absolute() else cache/path
            protected.append({'path': str(path), 'sha256': sha(path)})
    output.mkdir(parents=True)
    source_records = []
    sources = [(qemu/'contrib/ios-app/qemu-ios-ui.c', False),
               (qemu/'contrib/windows-app/engine.c', False),
               (app/'Desktop/native/Window.cpp', True),
               (app/'Windows/native/WindowsMain.cpp', True)]
    def run(argv, log):
        with (output/log).open('wb') as stream:
            subprocess.run(argv, cwd=cache, stdout=stream, stderr=subprocess.STDOUT, check=True)
    objects = []
    for source, cpp in sources:
        argv = list(template)
        original_source = original[0]['file']
        if argv.count(original_source) != 1:
            raise RuntimeError('Cached C command has unexpected source spelling.')
        argv[argv.index(original_source)] = str(source)
        target = output/(source.name+'.obj')
        for flag in ('-o', '-MF', '-MQ'):
            position = argv.index(flag) + 1
            argv[position] = str(target) + ('.d' if flag == '-MF' else '')
        if cpp:
            argv[0] = 'x86_64-w64-mingw32-g++-posix'
            argv = [value for value in argv if value not in ('-std=gnu11', '-Wmissing-prototypes', '-Wnested-externs', '-Wold-style-declaration', '-Wold-style-definition', '-Wstrict-prototypes')]
            argv += ['-std=gnu++17', '-I'+str(qemu/'contrib/ios-app'), '-I'+str(qemu/'contrib/windows-app')]
        source_records.append({'path': str(source), 'sha256': sha(source), 'argv': argv})
        run(argv, source.name+'.compile.log')
        objects.append(str(target))
    # Remove original main; no embedding entry/RCU constructor override.
    response.remove(main_object)
    response[1] = str(output/'lighttouch-windows-worker.exe')
    response[2:2] = objects
    rsp = output/'worker.rsp'; rsp.write_text(shlex.join(response))
    run(['x86_64-w64-mingw32-g++-posix', '-m64', '@'+str(rsp)], 'link.log')
    run(['x86_64-w64-mingw32-objdump', '-p', str(output/'lighttouch-windows-worker.exe')], 'PE-IMPORTS.txt')
    for record in protected:
        if sha(record['path']) != record['sha256']:
            raise RuntimeError('Protected cached link input changed: '+record['path'])
    record = {'status': 'COMPILED_ONLY', 'source_records': source_records, 'protected_inputs': protected,
              'response_sha256': sha(args.response), 'worker_sha256': sha(output/'lighttouch-windows-worker.exe'),
              'windows_execution_tested': False, 'guest_qualified': False,
              'limitations': 'Internal shared native worker only; complete Windows host asset/ACL admission, Tk runtime package and target execution remain pending.'}
    (output/'BUILD-PROVENANCE.json').write_text(json.dumps(record, indent=2)+'\n')
    print('PASS native shared worker compilation/link only; Windows runtime unqualified.', flush=True)


if __name__ == '__main__':
    main()
