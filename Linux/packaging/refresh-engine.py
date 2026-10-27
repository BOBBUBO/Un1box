#!/usr/bin/env python3
"""Refresh only a recorded engine revision inside the saved bounded container."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1048576), b''):
            digest.update(chunk)
    return digest.hexdigest()


def git(source, *args):
    return subprocess.check_output(['git', '-C', str(source), *args], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('next_command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.next_command or args.next_command[0] != '--' or len(args.next_command) < 2:
        parser.error('Expected -- followed by frontend/daemon/collection command')
    supplied = Path(os.environ['LIGHTTOUCH_QEMU_SOURCE'])
    source, build = Path('/build/qemu-source'), Path('/build/engine-cxx17-full')
    if git(supplied, 'status', '--porcelain=v1', '--untracked-files=all'):
        raise RuntimeError('Commit the supplied QEMU source before engine refresh')
    if git(source, 'status', '--porcelain=v1', '--untracked-files=all'):
        raise RuntimeError('Existing private QEMU source is dirty; refuse update')
    revision = git(supplied, 'rev-parse', 'HEAD')
    previous = json.loads((build / 'portable-manifest.json').read_text())
    if previous['source_dirty'] or sha(build / 'libqemu-arm.so') != previous['library_sha256']:
        raise RuntimeError('Existing engine artifact does not match its clean build manifest')
    args.evidence.mkdir(parents=True, exist_ok=False)
    for name in ('engine-inputs.json', 'portable-manifest.json', 'libqemu-arm.so'):
        shutil.copyfile(build / name, args.evidence / name)
    request = dict(previous_private_source_revision=git(source, 'rev-parse', 'HEAD'),
                   previous_engine_revision=previous['source_revision'], next_revision=revision,
                   previous_library_sha256=previous['library_sha256'])
    (args.evidence / 'UPDATE-REQUEST.json').write_text(json.dumps(request, indent=2) + '\n')
    subprocess.run(['git', '-C', str(source), 'fetch', '--no-tags', str(supplied), revision], check=True)
    subprocess.run(['git', '-C', str(source), 'checkout', '--detach', revision], check=True)
    if git(source, 'rev-parse', 'HEAD') != revision:
        raise RuntimeError('Private source checkout differs from supplied revision')
    # The existing source-update guard retains its full configuration, tools,
    # dependency and completed-library checks. Never edit its recorded inputs.
    subprocess.run(['python3', str(source / 'contrib/portable/build-engine.py'), str(build),
                    '--cpp-standard=gnu++17', '--source-update',
                    '--prefix', '/build/deps-full/prefix',
                    '--crypto-prefix', '/build/crypto/prefix'], check=True)
    actual = json.loads((build / 'portable-manifest.json').read_text())
    if actual['source_dirty'] or actual['source_revision'] != revision or sha(build / 'libqemu-arm.so') != actual['library_sha256']:
        raise RuntimeError('Updated engine failed clean revision/artifact binding')
    (args.evidence / 'UPDATE-RESULT.json').write_text(json.dumps(
        dict(request, passed=True, library_sha256=actual['library_sha256']), indent=2) + '\n')
    subprocess.run(args.next_command[1:], check=True)


if __name__ == '__main__':
    main()
