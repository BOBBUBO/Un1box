#!/usr/bin/env python3
"""Stage the fixed x86 debug provider and its checked offline notices."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
from urllib.parse import urlsplit

NAMES = {'libEGL_lighttouch_angle.so', 'libGLESv1_CM_lighttouch_angle.so', 'libGLESv2_lighttouch_angle.so'}
PLATFORM = {'libc.so', 'libdl.so', 'libm.so', 'liblog.so', 'libnativewindow.so'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_file(root, name):
    current = root
    for part in name.split('/'):
        if part in ('', '.', '..') or not re.fullmatch(r'[A-Za-z0-9._-]+', part):
            raise ValueError('Invalid provider notice path')
        current /= part
        if current.is_symlink():
            raise ValueError('Provider bundle symlinks are not accepted')
    if not current.is_file():
        raise ValueError(f'Missing provider input: {name}')
    return current


def inspect_library(path, readelf):
    data = path.read_bytes()
    if data[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<HH', data, 16) != (3, 62):
        raise ValueError('Provider must be an x86-64 little-endian ELF shared object')
    offset = struct.unpack_from('<Q', data, 32)[0]
    size, count = struct.unpack_from('<HH', data, 54)
    alignments = []
    for index in range(count):
        start = offset + index * size
        if struct.unpack_from('<I', data, start)[0] == 1:
            file_offset, virtual = struct.unpack_from('<QQ', data, start + 8)
            alignment = struct.unpack_from('<Q', data, start + 48)[0]
            if alignment < 16384 or (virtual - file_offset) % 16384:
                raise ValueError(f'Provider ELF is not 16KiB aligned: {path.name}')
            alignments.append(alignment)
    if not alignments:
        raise ValueError('Provider has no load segments')
    dynamic = subprocess.check_output([str(readelf), '--dynamic', '--wide', str(path)], text=True)
    sonames = re.findall(r'\(SONAME\).*\[([^\]]+)\]', dynamic)
    needed = re.findall(r'\(NEEDED\).*\[([^\]]+)\]', dynamic)
    if sonames != [path.name] or not set(needed) <= NAMES | PLATFORM:
        raise ValueError(f'Unexpected provider SONAME/dependency closure: {path.name}')
    return {'sha256': sha(path), 'soname': path.name, 'needed': needed, 'load_alignments': alignments}


def stage(destination, libraries, manifest_path, notices, readelf, manifest_sha, notices_sha):
    if not re.fullmatch(r'[0-9a-f]{64}', manifest_sha) or sha(manifest_path) != manifest_sha:
        raise ValueError('Provider closure manifest pin changed')
    closure = json.loads(manifest_path.read_text(encoding='utf-8'))
    if set(closure['objects']) != NAMES:
        raise ValueError('Provider closure must contain exactly the three fixed libraries')
    observed = {}
    for name in sorted(NAMES):
        path = checked_file(libraries, name)
        result = inspect_library(path, readelf)
        expected = closure['objects'][name]
        if result != {key: expected[key] for key in result}:
            raise ValueError(f'Provider closure identity changed: {name}')
        observed[name] = result
    notice_path = checked_file(notices, 'manifest.json')
    if not re.fullmatch(r'[0-9a-f]{64}', notices_sha) or sha(notice_path) != notices_sha:
        raise ValueError('Provider notice manifest pin changed')
    notice_manifest = json.loads(notice_path.read_text(encoding='utf-8'))
    if notice_manifest['schema'] != 1 or notice_manifest['libraries'] != {name: value['sha256'] for name, value in observed.items()}:
        raise ValueError('Provider notices do not match the staged runtime')
    reference = urlsplit(notice_manifest['source_reference'])
    if reference.scheme != 'https' or not reference.hostname or reference.username or reference.password or not re.fullmatch(r'[0-9a-f]{40}', notice_manifest['source_revision']):
        raise ValueError('Invalid provider source reference/revision')
    files = [('manifest.json', notice_path)]
    names = {'manifest.json'}
    for row in notice_manifest['files']:
        path = checked_file(notices, row['path'])
        if row['path'] in names or not re.fullmatch(r'[0-9a-f]{64}', row['sha256']) or sha(path) != row['sha256']:
            raise ValueError('Provider notice integrity failure')
        names.add(row['path']); files.append((row['path'], path))
    # All native and notice verification precedes replacing generated outputs.
    clear(destination)
    for name in sorted(NAMES):
        output = destination / 'jniLibs/x86_64' / name
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(libraries / name, output)
    for name, path in files:
        output = destination / 'assets/licenses/angle' / name
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, output)
    receipt = {'schema': 1, 'abi': 'x86_64', 'libraries': observed,
               'source_reference': notice_manifest['source_reference'], 'source_revision': notice_manifest['source_revision'],
               'license_compatibility': notice_manifest.get('license_compatibility', 'unresolved')}
    output = destination / 'assets/providers/angle-runtime.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print('Staged fixed x86 ANGLE runtime/notice identities; actual app-UID loading remains unqualified')


def clear(destination):
    if destination.is_symlink():
        raise ValueError('Unexpected generated provider destination')
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('destination', type=Path)
    parser.add_argument('--libraries', type=Path)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--notices', type=Path)
    parser.add_argument('--readelf', type=Path)
    parser.add_argument('--manifest-sha256')
    parser.add_argument('--notices-sha256')
    args = parser.parse_args()
    selected = [args.libraries, args.manifest, args.notices, args.readelf, args.manifest_sha256, args.notices_sha256]
    if not any(selected):
        clear(args.destination)
    elif not all(selected):
        parser.error('Provide all fixed runtime, closure, notice and readelf inputs')
    else:
        stage(args.destination, args.libraries.resolve(strict=True), args.manifest,
              args.notices.resolve(strict=True), args.readelf, args.manifest_sha256, args.notices_sha256)
