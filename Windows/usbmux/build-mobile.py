#!/usr/bin/env python3
"""Build the Windows app-services DLL from local transport/TLS providers.

No downloads or target execution. Run under the integrator's build allocation.
Builds common/src only: TSS image-mounter tools and wireless pairing are outside
this wired guest-service adapter. Their libtatsu/curl dependency is unnecessary.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

ARCHIVE_SHA256 = '23cc0077e221c7d991bd0eb02150a0d49199bcca1ddf059edccee9ffd914939d'


def sha(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            value.update(block)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('archive', 'transport', 'mobile-deps', 'tls-prefix', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    if sha(args.archive) != ARCHIVE_SHA256:
        raise ValueError('Use the pinned libimobiledevice1.4.0 release archive.')
    transport, deps, tls, output = [p.resolve() for p in
                                   (args.transport, args.mobile_deps, args.tls_prefix, args.output)]
    # The transport build already checks these provider hashes and its closure.
    for name, record in json.loads((transport / 'OUTPUTS.json').read_text())['files'].items():
        if sha(transport / name) != record['sha256']:
            raise ValueError('Transport provider changed: ' + name)
    output.mkdir(parents=True, exist_ok=False)
    prefix = output / 'prefix'
    shutil.copytree(deps / 'prefix/include', prefix / 'include')
    (prefix / 'lib/pkgconfig').mkdir(parents=True)
    for path in (deps / 'prefix/lib').glob('*.dll.a'):
        shutil.copyfile(path, prefix / 'lib' / path.name)
    for path in (deps / 'prefix/lib/pkgconfig').glob('*.pc'):
        text = path.read_text().splitlines()
        (prefix / 'lib/pkgconfig' / path.name).write_text('\n'.join(
            'prefix=' + str(prefix) if line.startswith('prefix=') else line for line in text) + '\n')
    mux = transport / 'libusbmuxd-2.1.1'
    shutil.copyfile(mux / 'include/usbmuxd.h', prefix / 'include/usbmuxd.h')
    shutil.copyfile(mux / 'src/.libs/libusbmuxd-2.0.dll.a', prefix / 'lib/libusbmuxd-2.0.dll.a')
    text = (mux / 'src/libusbmuxd-2.0.pc').read_text().splitlines()
    (prefix / 'lib/pkgconfig/libusbmuxd-2.0.pc').write_text('\n'.join(
        'prefix=' + str(prefix) if line.startswith('prefix=') else line for line in text) + '\n')
    # Use the already audited MSYS2 OpenSSL provider, without its executables.
    shutil.copytree(tls / 'include/openssl', prefix / 'include/openssl')
    for name in ('libssl.dll.a', 'libcrypto.dll.a'):
        shutil.copyfile(tls / 'lib' / name, prefix / 'lib' / name)
    for name in ('openssl.pc', 'libssl.pc', 'libcrypto.pc'):
        lines = (tls / 'lib/pkgconfig' / name).read_text().splitlines()
        (prefix / 'lib/pkgconfig' / name).write_text('\n'.join(
            'prefix=' + str(prefix) if line.startswith('prefix=') else line for line in lines) + '\n')
    with tarfile.open(args.archive) as archive:
        members = archive.getmembers()
        if len(members) > 5000 or sum(p.size for p in members) > 64 * 1024 * 1024:
            raise ValueError('Unexpected source archive size.')
        for member in members:
            path = Path(member.name)
            if (path.is_absolute() or '..' in path.parts or not path.parts or
                    path.parts[0] != 'libimobiledevice-1.4.0' or
                    not (member.isfile() or member.isdir())):
                raise ValueError('Unexpected source archive member: ' + member.name)
        archive.extractall(output, filter='data')
    source = output / 'libimobiledevice-1.4.0'
    # Upstream checks this tool-only dependency unconditionally. Make the
    # library-only scope explicit in both source and generated configure; never
    # invent a libtatsu .pc or claim its library was built.
    path = source / 'configure.ac'
    original_times = path.stat()
    text = path.read_text()
    needle = 'PKG_CHECK_MODULES(libtatsu, libtatsu-1.0 >= $LIBTATSU_VERSION)'
    if text.count(needle) != 1:
        raise ValueError('Unexpected configure.ac libtatsu check.')
    path.write_text(text.replace(needle, '# Windows service-library build omits tools/ideviceimagemounter.\n' +
                                'if test "x$LIGHTTOUCH_LIBRARY_ONLY" != xyes; then\n' + needle + '\nfi'))
    # Source and generated configure are changed together. Keep distribution
    # timestamps so make does not invoke unpinned local autotools regeneration.
    os.utime(path, ns=(original_times.st_atime_ns, original_times.st_mtime_ns))
    path = source / 'configure'
    original_times = path.stat()
    text = path.read_text()
    start = text.index('pkg_failed=no\n{ printf "%s\\n" "$as_me:${as_lineno-$LINENO}: checking for libtatsu-1.0')
    end = text.index('# Check whether --with-readline was given.', start)
    path.write_text(text[:start] + 'if test "x$LIGHTTOUCH_LIBRARY_ONLY" != xyes; then\n' +
                    text[start:end] + '\nfi\n' + text[end:])
    os.utime(path, ns=(original_times.st_atime_ns, original_times.st_mtime_ns))
    env = dict(os.environ)
    for key in ('CPATH', 'C_INCLUDE_PATH', 'CPLUS_INCLUDE_PATH', 'LIBRARY_PATH',
                'PKG_CONFIG_SYSROOT_DIR', 'MAKEFLAGS', 'MFLAGS', 'CONFIG_SITE',
                'CPPFLAGS', 'CFLAGS', 'LDFLAGS', 'LIBS'):
        env.pop(key, None)
    env.update(PATH='/usr/bin:/bin', LC_ALL='C', LIGHTTOUCH_LIBRARY_ONLY='yes',
               CC='/usr/bin/x86_64-w64-mingw32-gcc-posix', AR='/usr/bin/x86_64-w64-mingw32-ar',
               RANLIB='/usr/bin/x86_64-w64-mingw32-ranlib', STRIP='/usr/bin/x86_64-w64-mingw32-strip',
               PKG_CONFIG='/usr/bin/pkg-config', PKG_CONFIG_PATH='',
               PKG_CONFIG_LIBDIR=str(prefix / 'lib/pkgconfig'), CFLAGS='-O2 -g0 -D_WIN32_WINNT=0x0601')

    def run(argv):
        argv = list(map(str, argv))
        print(json.dumps({'argv': argv, 'cwd': str(source)}), flush=True)
        subprocess.run(argv, cwd=source, env=env, check=True, timeout=150)

    run([source / 'configure', '--host=x86_64-w64-mingw32', '--build=x86_64-pc-linux-gnu',
         '--prefix=' + str(prefix), '--enable-shared', '--disable-static', '--without-cython',
         '--without-readline', '--disable-wireless-pairing', '--with-openssl'])
    run(['/usr/bin/make', '-C', 'common', '-j1'])
    run(['/usr/bin/make', '-C', 'src', '-j1'])
    runtime = output / 'bin'
    shutil.copytree(transport / 'bin', runtime)
    for name in ('libssl-3-x64.dll', 'libcrypto-3-x64.dll'):
        shutil.copyfile(tls / 'bin' / name, runtime / name)
    shutil.copyfile(source / 'src/.libs/libimobiledevice-1.0.dll', runtime / 'libimobiledevice-1.0.dll')
    notices = output / 'licenses'
    shutil.copytree(transport / 'licenses', notices)
    for package, paths in [('openssl', [tls / 'share/licenses/openssl/LICENSE']),
                           ('libimobiledevice', list(source.glob('COPYING*')))]:
        (notices / package).mkdir()
        for path in paths:
            shutil.copyfile(path, notices / package / path.name)
    print('BUILT Windows service library; inspect PE closure/exports before packaging. Windows execution remains unqualified.')


if __name__ == '__main__':
    main()
