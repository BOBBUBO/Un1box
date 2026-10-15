#!/usr/bin/env python3
"""Fetch exact Debian source archives for an audited runtime inventory, never install."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--apt-workdir', type=Path, required=True, help='Private APT indices/cache; never use system state')
    parser.add_argument('--keyring', type=Path, required=True, help='Verified Debian archive keyring from build environment')
    args = parser.parse_args()
    packages = json.loads(args.inventory.read_text())
    sources = sorted({(p['source_package'], p['source_version']) for p in packages})
    args.output.mkdir(parents=True, exist_ok=False)
    work = args.apt_workdir.resolve()
    work.mkdir(parents=True, exist_ok=False)
    (work / 'lists/partial').mkdir(parents=True)
    (work / 'archives/partial').mkdir(parents=True)
    keyring = args.keyring.resolve(strict=True)
    if any(c.isspace() for c in str(keyring)):
        raise ValueError('APT keyring path must not contain whitespace')
    source_list = work / 'sources.list'
    source_list.write_text(''.join('deb-src [check-valid-until=no signed-by=%s] https://archive.debian.org/%s %s main\n' % (keyring, repository, suite) for repository, suite in (('debian', 'bullseye'), ('debian-security', 'bullseye-security'))))
    apt = ['apt-get', '-o', 'Dir::Etc::sourcelist=' + str(source_list), '-o', 'Dir::Etc::sourceparts=-',
           '-o', 'Dir::State::lists=' + str(work / 'lists'), '-o', 'Dir::Cache::archives=' + str(work / 'archives'),
           '-o', 'APT::Get::List-Cleanup=0', '-o', 'APT::Sandbox::User=' + str(os.getuid())]
    started = time.monotonic()
    subprocess.run(apt + ['update'], check=True, timeout=180)
    for name, version in sources:
        if not name or not version or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+.:~_-' for c in name + version):
            raise ValueError('Invalid Debian source coordinate')
        remaining = 1200 - (time.monotonic() - started)
        if remaining <= 0:
            raise RuntimeError('Original20minute source-download deadline reached')
        if os.statvfs(args.output).f_bavail * os.statvfs(args.output).f_frsize < 2 * 1024**3:
            raise RuntimeError('Source download requires2GiB remaining disk space')
        subprocess.run(apt + ['source', '--download-only', '--only-source', name + '=' + version], cwd=args.output, check=True, timeout=min(180, remaining))
    files = {}
    for path in sorted(args.output.iterdir()):
        if path.is_file():
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1048576), b''):
                    digest.update(chunk)
            files[path.name] = digest.hexdigest()
    report = dict(schema=1, requested_sources=sources, apt_command=apt, keyring_sha256=hashlib.sha256(keyring.read_bytes()).hexdigest(), files=files)
    (args.output / 'SOURCE-ARCHIVE-MANIFEST.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'source_count': len(sources), 'archives': len(report['files'])}))


if __name__ == '__main__':
    main()
