#!/usr/bin/env python3
"""Verify downloaded Debian descriptors and every referenced source file."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def sha(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1048576), b''):
            result.update(chunk)
    return result.hexdigest()


def fields(path):
    text = path.read_text()
    if text.startswith('-----BEGIN PGP SIGNED MESSAGE-----'):
        text = text.split('\n\n', 1)[1].split('-----BEGIN PGP SIGNATURE-----', 1)[0]
        text = '\n'.join(line[2:] if line.startswith('- ') else line for line in text.splitlines())
    result = {}
    current = None
    for line in text.splitlines():
        if not line:
            continue
        if line[0].isspace():
            if current is None:
                raise ValueError('Descriptor continuation without a field')
            result[current] += '\n' + line.strip()
        else:
            current, value = line.split(':', 1)
            if current in result:
                raise ValueError('Duplicate descriptor field')
            result[current] = value.strip()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refuse to overwrite a source audit')
    manifest = json.loads(args.manifest.read_text())
    root = args.manifest.parent
    for name, digest in manifest['files'].items():
        path = root / name
        if Path(name).name != name or path.is_symlink() or sha(path) != digest:
            raise ValueError('Source download manifest mismatch: ' + name)
    coordinates = set()
    descriptors = {}
    for path in sorted(root.glob('*.dsc')):
        value = fields(path)
        coordinate = (value['Source'], value['Version'])
        if coordinate in coordinates:
            raise ValueError('Duplicate source coordinate')
        coordinates.add(coordinate)
        referenced = []
        for line in value['Checksums-Sha256'].splitlines():
            if not line:
                continue
            digest, size, name = line.split()
            source = root / name
            if not re.fullmatch('[a-f0-9]{64}', digest) or Path(name).name != name or name not in manifest['files']:
                raise ValueError('Invalid/unmanifested descriptor source: ' + name)
            if source.is_symlink() or source.stat().st_size != int(size) or sha(source) != digest:
                raise ValueError('Descriptor source bytes mismatch: ' + name)
            referenced.append(name)
        if not referenced:
            raise ValueError('Empty source descriptor')
        descriptors[path.name] = dict(source=coordinate, references=referenced)
    if coordinates != {tuple(row) for row in manifest['requested_sources']}:
        raise ValueError('Downloaded source descriptors do not match requested exact versions')
    result = dict(schema=1, passed=True, download_manifest_sha256=sha(args.manifest),
                  source_count=len(coordinates), archive_count=len(manifest['files']), descriptors=descriptors,
                  apt_signature_provenance='Retain signed private APT indices and download log; this audit verifies their downloaded source bytes, not standalone PGP signatures')
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'passed': True, 'sources': len(coordinates), 'archives': len(manifest['files'])}))


if __name__ == '__main__':
    main()
