#!/usr/bin/env python3
"""Fetch the pinned AppImage runtime's static-library sources and Alpine patches.

Versions come from upstream runtime CI job109068477114/run36463736478;
the release runtime is commit8f39b89e2ac31e1640b3d3f7e9a5108e6ce805fa.
No downloaded recipe or source code is executed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tarfile
import time
import urllib.request

PACKAGES = (
    ('main/musl', '8999cf56154b9bd09c995f293d8dfd3aa8001e3a', '1.2.5', '11',
     'musl-1.2.5.tar.gz', 'https://musl.libc.org/releases/musl-1.2.5.tar.gz', 'musl-1.2.5/COPYRIGHT'),
    ('main/zstd', '5c2ddf18f193dafdd25ae7391d96c7e30766eecd', '1.5.6', '2',
     'zstd-1.5.6.tar.gz', 'https://github.com/facebook/zstd/archive/v1.5.6.tar.gz', 'zstd-1.5.6/LICENSE'),
    ('main/zlib', '2b38f55109add14f4f99a974c2fdf421b6b9e9e9', '1.3.2', '0',
     'zlib-1.3.2.tar.gz', 'https://zlib.net/fossils/zlib-1.3.2.tar.gz', 'zlib-1.3.2/LICENSE'),
    ('community/mimalloc2', '5a1a5dc3216a69e3cd66a57ee07cb5ef01b8be39', '2.1.7', '0',
     'mimalloc-2.1.7.tar.gz', 'https://github.com/microsoft/mimalloc/archive/v2.1.7/mimalloc-2.1.7.tar.gz', 'mimalloc-2.1.7/LICENSE'),
)


def digest(path, algorithm='sha256'):
    value = hashlib.new(algorithm)
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1048576), b''):
            value.update(chunk)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    files = {}
    packages = []

    def fetch(url, path):
        remaining = 600 - (time.monotonic() - start)
        if remaining <= 0:
            raise RuntimeError('Original10minute source-download deadline reached')
        total = 0
        with urllib.request.urlopen(url, timeout=min(60, remaining)) as response, path.open('xb') as stream:
            while True:
                chunk = response.read(1048576)
                if not chunk:
                    break
                total += len(chunk)
                if total > 64 * 1024**2 or time.monotonic() - start > 600:
                    raise RuntimeError('Source download exceeds bounded size/time')
                stream.write(chunk)
        files[str(path.relative_to(args.output))] = dict(url=url, sha256=digest(path), bytes=total)

    for package_path, revision, version, release, archive_name, archive_url, license_name in PACKAGES:
        name = package_path.split('/')[-1]
        root = args.output / name
        root.mkdir()
        base = 'https://raw.githubusercontent.com/alpinelinux/aports/' + revision + '/' + package_path + '/'
        recipe = root / 'APKBUILD'
        fetch(base + 'APKBUILD', recipe)
        text = recipe.read_text()
        for key, expected in [('pkgver', version), ('pkgrel', release)]:
            if not re.search('^' + key + '=' + re.escape(expected) + '$', text, re.MULTILINE):
                raise ValueError('Recipe differs from actual runtime build version: ' + name)
        checksum_text = re.search(r'^sha512sums="\n(.*?)\n"', text, re.MULTILINE | re.DOTALL).group(1)
        checksums = dict((filename, checksum) for checksum, filename in (line.split() for line in checksum_text.splitlines()))
        if archive_name not in checksums:
            raise ValueError('Expected source archive absent from recipe')
        for filename, checksum in checksums.items():
            if Path(filename).name != filename or not re.fullmatch('[0-9a-f]{128}', checksum):
                raise ValueError('Unsafe source filename/checksum')
            path = root / filename
            fetch(archive_url if filename == archive_name else base + filename, path)
            if digest(path, 'sha512') != checksum:
                raise ValueError('Alpine recipe source checksum mismatch: ' + str(path))
        with tarfile.open(root / archive_name) as archive:
            member = archive.getmember(license_name)
            if not member.isfile() or member.size > 1024**2:
                raise ValueError('Invalid source license member')
            notice = root / 'LICENSE.txt'
            notice.write_bytes(archive.extractfile(member).read())
            files[str(notice.relative_to(args.output))] = dict(source_archive=archive_name, source_member=license_name, sha256=digest(notice), bytes=notice.stat().st_size)
        packages.append(dict(package=name, version=version + '-r' + release,
                             aports_path=package_path, aports_revision=revision, sha512sums=checksums))
    report = dict(schema=1, runtime_revision='8f39b89e2ac31e1640b3d3f7e9a5108e6ce805fa',
                  upstream_ci_job=109068477114, elapsed_seconds=time.monotonic() - start,
                  packages=packages, files=files, downloaded_code_executed=False)
    (args.output / 'RUNTIME-SOURCE-MANIFEST.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'packages': len(packages), 'files': len(files), 'source_checksums_passed': True}))


if __name__ == '__main__':
    main()
