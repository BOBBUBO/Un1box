#!/usr/bin/env python3
"""Fetch checksum-pinned C development packages without changing host libraries."""
import argparse
import hashlib
import json
import io
import tarfile
import pathlib
import subprocess
import urllib.request


def stage(manifest, destination):
    destination = pathlib.Path(destination).resolve()
    packages = destination / 'packages'
    sysroot = destination / 'sysroot'
    packages.mkdir(parents=True, exist_ok=True)
    sysroot.mkdir(exist_ok=True)
    results = []
    for package in json.loads(pathlib.Path(manifest).read_text())['packages']:
        archive = packages / package['archive']
        if not archive.exists():
            temporary = archive.with_suffix('.part')
            with urllib.request.urlopen(package['url'], timeout=45) as response, temporary.open('wb') as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            temporary.replace(archive)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if digest != package['sha256']:
            raise SystemExit('Checksum mismatch: ' + str(archive))
        # Pinned MSYS2 packages are not arbitrary user archives. Refuse names or
        # links capable of escaping the isolated root before extracting them.
        raw = subprocess.check_output(['zstd', '-q', '-d', '-c', str(archive)])
        with tarfile.open(fileobj=io.BytesIO(raw)) as package_tar:
            package_tar.extractall(sysroot, filter='data')
        results.append({'name': package['name'], 'sha256': digest})
        print('STAGED', package['name'], flush=True)
    (destination / 'STAGED.json').write_text(json.dumps(results, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--destination', required=True)
    args = parser.parse_args()
    stage(args.manifest, args.destination)
