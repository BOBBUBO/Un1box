#!/usr/bin/env python3
"""Stream audited corresponding sources/notices into an uncompressed private ZIP."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile


def sha(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1048576), b''):
            value.update(chunk)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('appdir', 'debian-source', 'runtime-source', 'compiler-notices', 'output', 'receipt'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--provenance', type=Path, action='append', default=[])
    args = parser.parse_args()
    if args.output.exists() or args.receipt.exists():
        raise ValueError('Refuse to overwrite source bundle/evidence')
    inputs = {}

    def add(path, name, expected=None):
        if path.is_symlink() or not path.is_file() or name in inputs or '..' in Path(name).parts:
            raise ValueError('Invalid/duplicate source input: ' + name)
        checksum = sha(path)
        if expected is not None and checksum != expected:
            raise ValueError('Changed audited source input: ' + name)
        inputs[name] = (path, checksum)

    app_manifest = args.appdir / 'runtime-manifest.json'
    app = json.loads(app_manifest.read_text())
    for name, checksum in app['files'].items():
        if name.startswith('usr/share/licenses/'):
            add(args.appdir / name, 'licenses/' + name.removeprefix('usr/share/licenses/'), checksum)
    add(app_manifest, 'provenance/runtime-manifest.json')
    for prefix, root, manifest_name in [
            ('debian', args.debian_source, 'SOURCE-ARCHIVE-MANIFEST.json'),
            ('AppImage-static', args.runtime_source, 'RUNTIME-SOURCE-MANIFEST.json'),
            ('toolchain-notices', args.compiler_notices, 'NOTICE-MANIFEST.json')]:
        manifest_path = root / manifest_name
        value = json.loads(manifest_path.read_text())
        for name, entry in value['files'].items():
            if Path(name).is_absolute() or '..' in Path(name).parts:
                raise ValueError('Unsafe source manifest path')
            add(root / name, prefix + '/' + name, entry if isinstance(entry, str) else entry['sha256'])
        add(manifest_path, prefix + '/' + manifest_name)
    for path in args.provenance:
        add(path, 'provenance/' + path.name)
    recipe_dir = Path(__file__).resolve().parent
    app_root = recipe_dir.parents[1]
    revision = subprocess.check_output(['git', '-C', str(app_root), 'rev-parse', 'HEAD'], text=True).strip()
    tracked = subprocess.check_output(['git', '-C', str(app_root), 'ls-files', 'Linux/packaging'], text=True).splitlines()
    for name in tracked:
        add(app_root / name, 'packaging-recipes/' + name.removeprefix('Linux/packaging/'))
    index = dict(schema=1, packaging_revision=revision,
                 runtime_manifest_sha256=sha(app_manifest),
                 files={name: checksum for name, (_, checksum) in sorted(inputs.items())},
                 guest_assets_included=False, guest_acceptance=False)
    with zipfile.ZipFile(args.output, 'x', compression=zipfile.ZIP_STORED, allowZip64=True) as bundle:
        for name, (path, checksum) in sorted(inputs.items()):
            actual = hashlib.sha256()
            with path.open('rb') as source, bundle.open(name, 'w', force_zip64=True) as target:
                while True:
                    chunk = source.read(1048576)
                    if not chunk:
                        break
                    actual.update(chunk)
                    target.write(chunk)
            if actual.hexdigest() != checksum:
                raise ValueError('Source changed during bundle: ' + name)
        bundle.writestr('SOURCE-BUNDLE-MANIFEST.json', json.dumps(index, indent=2) + '\n')
        bundle.writestr('README.txt', 'LightTouch experimental Linux corresponding sources and notices.\n'
                         'Keep this companion with the matching AppImage. No iOS firmware or guest state is included.\n'
                         'Native sources/build recipes and linked-dependency archives are under licenses/LightTouch.\n'
                         'Exact Debian source descriptors and original/debian archives are under debian/.\n'
                         'Additional AppImage static library sources/patches/notices are under AppImage-static/.\n'
                         'Runtime build provenance and artifact manifest are under provenance/.\n'
                         'ZIP uses storage without compression to avoid competing with guest/build memory resources.\n'
                         'This source bundle does not establish runtime, distro, guest or IPA acceptance.\n')
    result = dict(schema=1, output=str(args.output.resolve()), sha256=sha(args.output),
                  bytes=args.output.stat().st_size, files=len(inputs) + 2,
                  packaging_revision=revision, runtime_manifest_sha256=index['runtime_manifest_sha256'],
                  guest_assets_included=False)
    args.receipt.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
