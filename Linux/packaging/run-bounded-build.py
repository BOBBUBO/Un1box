#!/usr/bin/env python3
"""Execute one finite Docker build, recording the real cgroup and host memory floor."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time

IMAGE = 'debian@sha256:e5b6442dd2e9684cf5e87d8338b5968f3b348636fc0be6d7850a381e3731a2bd'


def available():
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith('MemAvailable:'):
            return int(line.split()[1]) * 1024
    raise RuntimeError('MemAvailable missing')


def inspect(name):
    return json.loads(subprocess.check_output(['docker', 'inspect', name], text=True, timeout=5))[0]


def alternate_objects(common):
    """Retain readonly object closure of shared clones, including nested alternates."""
    found = set()
    pending = [common / 'objects']
    while pending:
        objects = pending.pop().resolve(strict=True)
        if objects in found:
            continue
        found.add(objects)
        alternates = objects / 'info/alternates'
        if alternates.is_file():
            for line in alternates.read_text().splitlines():
                if line:
                    path = Path(line)
                    pending.append(path if path.is_absolute() else objects / path)
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--qemu', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--evidence', type=Path, required=True, help='Fresh evidence directory; build products may be resumed')
    parser.add_argument('--collect-only-image', help='Explicit saved successful build environment; run no compilers')
    parser.add_argument('--runtime-name', default='runtime', help='Fresh runtime output directory name')
    args = parser.parse_args()
    assert available() >= 8 * 1024**3, 'Build admission needs8GiB available host RAM'
    app, qemu, output = args.app.resolve(), args.qemu.resolve(), args.output.resolve()
    assert args.runtime_name and Path(args.runtime_name).name == args.runtime_name
    output.mkdir(parents=True, exist_ok=True)
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    assert os.statvfs(output).f_bavail * os.statvfs(output).f_frsize >= 4 * 1024**3
    common = Path(subprocess.check_output(['git', '-C', str(qemu), 'rev-parse', '--path-format=absolute', '--git-common-dir'], text=True).strip())
    app_common = Path(subprocess.check_output(['git', '-C', str(app), 'rev-parse', '--path-format=absolute', '--git-common-dir'], text=True).strip())
    extra_mounts = []
    for path in sorted(alternate_objects(common) | alternate_objects(app_common)):
        if not path.is_relative_to(common) and not path.is_relative_to(app_common):
            extra_mounts += ['--mount', 'type=bind,src=%s,dst=%s,readonly' % (path, path)]
    inner = ['bash', '/src/app/Linux/packaging/build-bullseye-inner.sh']
    image = IMAGE
    if args.collect_only_image:
        image = args.collect_only_image
        inner = ['python3', '/src/app/Linux/packaging/collect-bullseye-runtime.py',
                 '--output', '/build/' + args.runtime_name,
                 '--frontend', '/build/frontend/lighttouch-linux',
                 '--engine', '/build/engine-cxx17-full/libqemu-arm.so',
                 '--dependency', '/build/deps-full', '--dependency', '/build/crypto',
                 '--dependency', '/build/mobile-v3']
    command = ['docker', 'create', '--name', args.name, '--memory=4g', '--memory-swap=4g', '--cpus=2', '--pids-limit=256',
               '--mount', 'type=bind,src=%s,dst=/src/app,readonly' % app,
               '--mount', 'type=bind,src=%s,dst=%s,readonly' % (qemu, qemu),
               '--mount', 'type=bind,src=%s,dst=%s,readonly' % (common, common),
               '--mount', 'type=bind,src=%s,dst=%s,readonly' % (app_common, app_common),
               '--mount', 'type=bind,src=%s,dst=/build' % output,
               '-e', 'LIGHTTOUCH_QEMU_SOURCE=' + str(qemu), '-e', 'LIGHTTOUCH_APP_SOURCE=/src/app'] + extra_mounts + [image] + inner
    subprocess.run(command, check=True, stdout=subprocess.PIPE)
    info = inspect(args.name)
    assert info['HostConfig']['Memory'] == 4 * 1024**3
    assert info['HostConfig']['MemorySwap'] == 4 * 1024**3
    assert info['HostConfig']['NanoCpus'] == 2_000_000_000
    assert info['HostConfig']['PidsLimit'] == 256
    (evidence / 'container-config.json').write_text(json.dumps(info, indent=2) + '\n')
    def interrupted(signum, frame):
        raise InterruptedError('Build observer received signal ' + str(signum))
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    start = time.monotonic()
    samples = 0
    error = None
    group = None
    with (evidence / 'build.log').open('w') as log, (evidence / 'resources.jsonl').open('w') as resources:
        child = subprocess.Popen(['docker', 'start', '--attach', args.name], stdout=log, stderr=subprocess.STDOUT)
        try:
            while child.poll() is None:
                host = available()
                row = {'observed_monotonic_ns': time.monotonic_ns(), 'elapsed_seconds': time.monotonic() - start, 'MemAvailable_bytes': host}
                if group is None:
                    state = inspect(args.name)['State']
                    if state['Pid']:
                        lines = Path('/proc', str(state['Pid']), 'cgroup').read_text().splitlines()
                        for line in lines:
                            if line.startswith('0::'):
                                group = Path('/sys/fs/cgroup') / line[3:].lstrip('/')
                if group:
                    for name in ('memory.current', 'memory.peak', 'pids.current', 'cpu.stat', 'memory.events'):
                        try:
                            row[name] = (group / name).read_text().strip()
                        except FileNotFoundError:
                            pass
                resources.write(json.dumps(row) + '\n')
                resources.flush()
                samples += 1
                if host < 6 * 1024**3:
                    raise RuntimeError('Host available RAM fell below6GiB')
                if time.monotonic() - start >= 1500:
                    raise RuntimeError('Original25minute build limit reached')
                time.sleep(1)
            child.wait()
        except BaseException as failure:
            error = repr(failure)
            subprocess.run(['docker', 'stop', '--time', '15', args.name], stdout=subprocess.PIPE, timeout=25)
            child.wait(timeout=10)
        finally:
            state = inspect(args.name)
            report = {'elapsed_seconds': time.monotonic() - start, 'samples': samples, 'error': error,
                      'docker_attach_exit': child.returncode, 'state': state['State'],
                      'limits': {key: state['HostConfig'][key] for key in ('Memory', 'MemorySwap', 'NanoCpus', 'PidsLimit')},
                      'no_guest_operations': True}
            (evidence / 'BUILD-TERMINAL.json').write_text(json.dumps(report, indent=2) + '\n')
            print(json.dumps(report, indent=2))
    if error or child.returncode != 0 or state['State']['Running'] or state['State']['OOMKilled']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
