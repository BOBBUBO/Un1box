#!/usr/bin/env python3
"""Collect only explicit Debian11 runtime roots and their actual ELF closure."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('appimage_stage', HERE / 'stage-appimage.py')
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)


def run(argv, **kwargs):
    return subprocess.check_output(list(map(str, argv)), text=True, **kwargs)


def owner(path):
    result = run(['dpkg-query', '-S', path])
    # Debian package names may contain the architecture colon.
    return result.split(': ', 1)[0].split(', ', 1)[0]


def collect(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    files = {}
    packages = set()
    pending = []
    custom_prefixes = [directory.resolve() / 'prefix' for directory in args.dependency]
    library_env = dict(os.environ, LD_LIBRARY_PATH=':'.join(str(p / 'lib') for p in custom_prefixes))
    payload = output / 'payload'
    payload.mkdir()

    def add(source, destination, license_text, package=None):
        source = Path(source).resolve(strict=True)
        if destination in files:
            if files[destination]['original_sha256'] != stage.sha(source):
                raise RuntimeError('Conflicting runtime files: ' + destination)
            return
        stage.target(destination)
        copied = payload / destination
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, copied)
        if copied.read_bytes()[:4] == b'\x7fELF':
            copied.chmod(0o755)
            # Source build directories must not escape into the runnable package.
            dynamic = run(['readelf', '-d', copied])
            if re.search(r'\((?:RPATH|RUNPATH)\)', dynamic):
                subprocess.run(['patchelf', '--set-rpath', '$ORIGIN', str(copied)], check=True)
            pending.append((copied, source))
        files[destination] = dict(source=str(copied), destination=destination,
                                  sha256=stage.sha(copied), original_sha256=stage.sha(source), license=license_text)
        if package:
            packages.add(package)

    def system(source, destination):
        # Resolve soname links only after asking dpkg about the lexical filename.
        for prefix in custom_prefixes:
            if source.resolve().is_relative_to(prefix):
                add(source, destination, 'Pinned private dependency; bundled source and notices')
                return
        package = owner(str(source))
        add(source, destination, 'Debian ' + package + '; bundled copyright/source inventory', package)

    system(Path('/usr/bin/python3.9'), 'usr/bin/python3')
    for root, destination in [(Path('/usr/lib/python3.9'), 'usr/lib/python3.9'),
                              (Path('/usr/share/tcltk/tcl8.6'), 'usr/share/tcltk/tcl8.6'),
                              (Path('/usr/share/tcltk/tk8.6'), 'usr/share/tcltk/tk8.6')]:
        for source in sorted(root.rglob('*')):
            if source.is_file() and '__pycache__' not in source.parts and source.suffix != '.pyc' and 'test' not in source.relative_to(root).parts:
                system(source, destination + '/' + str(source.relative_to(root)))
    add(args.frontend, 'usr/bin/lighttouch-linux', 'LightTouch GPL-2.0-or-later; bundled source')
    add(args.engine, 'usr/lib/lighttouch/libqemu-arm.so', 'QEMU GPL-2.0-or-later and bundled linked-dependency notices/source')
    # SDL loads audio backends by name, so ELF NEEDED alone is insufficient.
    for soname in ('libasound.so.2', 'libpulse.so.0', 'libpulse-simple.so.0'):
        library = Path('/usr/lib/x86_64-linux-gnu') / soname
        if library.exists():
            system(library, 'usr/lib/' + soname)
    for prefix in custom_prefixes:
        if prefix.parent.name == 'mobile':
            system(prefix / 'bin/usbmuxd-qemu', 'usr/bin/usbmuxd-qemu')
            for library in sorted((prefix / 'lib').glob('*.so*')):
                if library.is_file():
                    system(library, 'usr/lib/lighttouch/' + library.name)
    observed = set()
    while pending:
        elf, original = pending.pop()
        if elf in observed:
            continue
        observed.add(elf)
        result = run(['ldd', original], env=library_env)
        if 'not found' in result:
            raise RuntimeError('Unresolved build-time dependency: ' + result)
        for line in result.splitlines():
            match = re.match(r'\s*(\S+) => (/\S+)', line)
            if match:
                soname, source = match.groups()
                if soname not in stage.HOST_LIBRARIES:
                    system(Path(source), 'usr/lib/' + soname)
    notices = output / 'notices'
    notices.mkdir()
    provenance = []
    for package in sorted(packages):
        row = run(['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\t${source:Package}\t${source:Version}', package]).strip().split('\t')
        provenance.append(dict(zip(('package', 'version', 'source_package', 'source_version'), row)))
        name = package.split(':', 1)[0]
        copyright_file = Path('/usr/share/doc') / name / 'copyright'
        if not copyright_file.exists():
            raise RuntimeError('Missing copyright for ' + package)
        add(copyright_file, 'usr/share/licenses/debian/' + name + '/copyright', 'Debian package copyright notice: ' + package)
    for name in ('GPL-2', 'GPL-3', 'LGPL-2', 'LGPL-2.1', 'LGPL-3', 'BSD', 'Apache-2.0', 'Artistic'):
        path = Path('/usr/share/common-licenses') / name
        if path.exists():
            add(path, 'usr/share/licenses/common/' + name, 'Common license text')
    (notices / 'debian-source-inventory.json').write_text(json.dumps(provenance, indent=2) + '\n')
    add(notices / 'debian-source-inventory.json', 'usr/share/licenses/LightTouch/debian-source-inventory.json', 'Debian source provenance')
    source_notice = notices / 'SOURCE.txt'
    source_notice.write_text('LightTouch Linux experimental build. No guest firmware is included.\n'
        'Exact build manifests and corresponding-source inventory are bundled here.\n'
        'Debian source archives are available from archive.debian.org/debian and debian-security,\n'
        'at the exact source package versions in debian-source-inventory.json.\n'
        'Native sources and linked-library archives accompany this private build; distribution\n'
        'requires preserving the complete matching source bundle and license notices.\n')
    add(source_notice, 'usr/share/licenses/LightTouch/SOURCE.txt', 'Corresponding source notice')
    # Retain exact clean source revisions, not the entire private workspace.
    app = HERE.parents[1]
    qemu = args.engine.parent.parent / 'qemu-source'
    source_index = {}
    for name, source in (('LightTouch', app), ('qemu-ios', qemu)):
        revision = run(['git', '-C', source, 'rev-parse', 'HEAD']).strip()
        archive = notices / (name + '-' + revision + '.tar.gz')
        with archive.open('wb') as stream:
            subprocess.run(['git', '-C', str(source), 'archive', '--format=tar.gz', 'HEAD'], stdout=stream, check=True)
        add(archive, 'usr/share/licenses/LightTouch/source/' + archive.name, name + ' corresponding source')
        source_index[name] = revision
    for source in sorted((qemu / 'subprojects').iterdir()):
        if source.is_dir() and (source / '.git').exists():
            revision = run(['git', '-C', source, 'rev-parse', 'HEAD']).strip()
            wrap = source.with_suffix('.wrap')
            if not wrap.exists() or not re.search(r'^revision\s*=\s*' + re.escape(revision) + r'\s*$', wrap.read_text(), re.MULTILINE):
                raise RuntimeError('Subproject source revision differs from pinned wrap: ' + str(source))
            archive = notices / (source.name + '-' + revision + '.tar.gz')
            with archive.open('wb') as stream:
                subprocess.run(['git', '-C', str(source), 'archive', '--format=tar.gz', revision], stdout=stream, check=True)
            add(archive, 'usr/share/licenses/LightTouch/source/' + archive.name, 'QEMU pinned subproject corresponding source')
            source_index['subproject/' + source.name] = revision
    for directory in args.dependency:
        manifest = directory / 'dependency-manifest.json'
        if manifest.exists():
            value = json.loads(manifest.read_text())
            add(manifest, 'usr/share/licenses/LightTouch/source/' + directory.name + '-manifest.json', 'Pinned dependency build manifest')
            built = value.get('built_packages')
            for package in value['packages']:
                if built is not None and package['name'] not in built:
                    continue
                source_root = directory / 'sources' if (directory / 'sources').is_dir() else directory
                archive = source_root / package['archive']
                if stage.sha(archive) != package['sha256']:
                    raise RuntimeError('Dependency source hash mismatch: ' + str(archive))
                add(archive, 'usr/share/licenses/LightTouch/source/' + archive.name, package['name'] + ' corresponding source; notices inside archive')
                tree_name = package.get('directory') or re.sub(r'\.tar\.(?:xz|gz|bz2)$', '', package['archive'])
                tree = source_root / tree_name
                for filename in ('COPYING', 'COPYING.LIB', 'COPYING.LESSER', 'LICENSE', 'LICENSE.txt', 'COPYINGv2', 'COPYINGv3'):
                    notice = tree / filename
                    if notice.is_file():
                        add(notice, 'usr/share/licenses/LightTouch/' + package['name'] + '/' + filename, package['name'] + ' license text')
            daemon = directory / 'usbmuxd-source'
            if daemon.exists():
                archive = notices / ('usbmuxd-' + value['daemon_revision'] + '.tar.gz')
                with archive.open('wb') as stream:
                    subprocess.run(['git', '-C', str(daemon), 'archive', '--format=tar.gz', value['daemon_revision']], stdout=stream, check=True)
                add(archive, 'usr/share/licenses/LightTouch/source/' + archive.name, 'QEMU usbmuxd corresponding source')
                add(daemon / 'COPYING', 'usr/share/licenses/LightTouch/usbmuxd/COPYING', 'QEMU usbmuxd license')
    source_index['packaging_source_sha256'] = {p.name: stage.sha(p) for p in sorted(HERE.glob('*')) if p.is_file()}
    (notices / 'source-revisions.json').write_text(json.dumps(source_index, indent=2) + '\n')
    add(notices / 'source-revisions.json', 'usr/share/licenses/LightTouch/source-revisions.json', 'Build source provenance')
    manifest = dict(schema=1, architecture='x86_64', glibc_max='2.31', python_stdlib='python3.9', files=list(files.values()))
    manifest_path = output / 'runtime-inputs.json'
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    result = stage.stage(manifest_path, output / 'LightTouch.AppDir')
    print(json.dumps({'appdir': str(output / 'LightTouch.AppDir'), 'files': len(result['files']), 'guest_acceptance': False}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--frontend', type=Path, required=True)
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--dependency', type=Path, action='append', default=[])
    collect(parser.parse_args())


if __name__ == '__main__':
    main()
