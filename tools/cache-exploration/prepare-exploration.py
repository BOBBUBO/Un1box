#!/usr/bin/env python3
"""Prepare isolated writable state and actual input receipts; no guest/build."""
import argparse
import os
import re
import shutil
import stat
import subprocess

from exploration_common import *
import cache_trial
import matched_trial

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock-policy', type=Path, required=True)
    parser.add_argument('--lock-reference', type=Path, help='Prior private, personally viewed native Lock capture directory')
    parser.add_argument('--model-inspected-reference', action='store_true')
    parser.add_argument('--cache-mode', choices=['off', 'cold', 'reuse'], default='off')
    parser.add_argument('--cache-directory', type=Path, help='Separate existing canonical private0700 directory')
    parser.add_argument('--reuse-cache-receipt', type=Path, help='Prior actual clean cache-trial.json')
    parser.add_argument('--reuse-cache-result', type=Path, help='Matching prior actual RESULT.json')
    parser.add_argument('--matched-shutdown-diagnostic', action='store_true')
    args = parser.parse_args()
    if args.cache_mode == 'off' and any((args.cache_directory, args.reuse_cache_receipt, args.reuse_cache_result)):
        raise RuntimeError('OFF preparation must not select experimental cache inputs')
    if args.cache_mode != 'off' and args.cache_directory is None:
        raise RuntimeError('Explicit separate cache directory required')
    if (args.cache_mode == 'reuse') != bool(args.reuse_cache_receipt and args.reuse_cache_result):
        raise RuntimeError('Reuse requires both prior clean cache receipt and matching RESULT')
    if args.cache_mode != 'reuse' and (args.reuse_cache_receipt or args.reuse_cache_result):
        raise RuntimeError('Only reuse may select prior cache evidence')
    plan = read(P / 'PLAN.json')
    if R.exists() or (P / 'EXPLORATION-INPUTS.json').exists():
        raise RuntimeError('Preserve existing prepared exploration; no overwrite')
    if available() < 8 * 1024**3 or shutil.disk_usage(P).free < 3 * 1024**3:
        raise RuntimeError('Preparation memory/disk admission refused')
    inputs = plan['assets']
    base, seed = Path(inputs['base']), Path(inputs['state'])
    if base == seed or base in seed.parents or seed in base.parents:
        raise RuntimeError('Base/seed overlap')
    if read(Path(plan['referenceReceipt']['path']))['result'] != 'SCOPED_PASS':
        raise RuntimeError('Prior strict-clean prepared-seed receipt missing')
    pins = {}
    for name, entry in {**plan['artifacts'], 'rom': plan['rom'], 'package': plan['guestPackage'],
                        'baseLock': plan['baseLock'], 'baseIdentity': plan['baseIdentityFile'],
                        'priorCleanReceipt': plan['referenceReceipt']}.items():
        if digest(entry['path']) != entry['sha256']:
            raise RuntimeError('Prepared input changed: ' + name)
        pins[name] = entry
    if args.cache_mode == 'reuse':
        for name, value in [('cacheReuseReceipt', args.reuse_cache_receipt), ('cacheReuseResult', args.reuse_cache_result)]:
            path = value.resolve(strict=True)
            pins[name] = {'path': str(path), 'sha256': digest(path), 'bytes': path.stat().st_size}
    # Dependency resolution only; loader trace does not run frontend/QEMU main.
    env = dict(os.environ, LD_LIBRARY_PATH=str(Path(pins['privateLibfdt']['path']).parent))
    env.pop('LD_PRELOAD', None)
    closure = {}
    traces = {}
    for name in ['engine', 'frontend']:
        result = subprocess.run(['ldd', pins[name]['path']], env=env, text=True, capture_output=True, timeout=20)
        traces[name] = {'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
        if result.returncode or 'not found' in result.stdout:
            raise RuntimeError('Unresolved existing artifact dependency: ' + name)
        for path in re.findall(r'(?:=>\s+|^\s*)(/\S+)\s+\(0x', result.stdout, re.M):
            actual = Path(path).resolve(strict=True)
            closure[str(actual)] = {'path': str(actual), 'sha256': digest(actual), 'bytes': actual.stat().st_size}
    for path, entry in closure.items():
        pins['dependency:' + path] = entry
    for name in ['Xvfb', 'xdotool', 'ffmpeg']:
        tool = Path(shutil.which(name) or '/missing/' + name).resolve(strict=True)
        pins['tool:' + name] = {'path': str(tool), 'sha256': digest(tool), 'bytes': tool.stat().st_size}
    lock_policy = args.lock_policy.resolve(strict=True)
    pins['lockPolicy'] = {'path': str(lock_policy), 'sha256': digest(lock_policy), 'bytes': lock_policy.stat().st_size}
    reference = None
    if args.lock_reference is not None:
        if not args.model_inspected_reference:
            raise RuntimeError('Personally view actual reference PNG before preparing it')
        directory = args.lock_reference.resolve(strict=True)
        reference = {'scope': 'PERSONALLY_INSPECTED_N72_LOCK_REFERENCE', 'captureDirectory': str(directory),
                     'inspection_semantics': 'Operator personally viewed this actual PNG; matching anchors later admits input only, never a new scene pass'}
        for name, filename in [('bmp', 'native.bmp'), ('png', 'native.png'), ('receipt', 'receipt.json')]:
            path = directory / filename
            entry = {'path': str(path), 'sha256': digest(path), 'bytes': path.stat().st_size}
            pins['lockReference:' + name] = entry
            reference[name + 'SHA256'] = entry['sha256']
        receipt = read(directory / 'receipt.json')
        if receipt['sha256'] != reference['bmpSHA256'] or not fresh(receipt, int(receipt['saved_monotonic_ns'])):
            raise RuntimeError('Reference must retain its original fresh awake receipt')
    elif args.model_inspected_reference:
        raise RuntimeError('--model-inspected-reference requires --lock-reference')
    started = time.monotonic_ns()
    def guard():
        if available() < 6 * 1024**3 or time.monotonic_ns() - started > 120 * SECOND:
            raise RuntimeError('Source preparation memory/time bound')
    base_inventory = inventory(base, guard)
    metadata = {}
    for relative in base_inventory['entries']:
        path = base / relative
        info = path.lstat()
        if info.st_mode & 0o222:
            raise RuntimeError('Base must stay read-only; no chmod: ' + str(path))
        metadata[relative] = [info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
    with free_lease(seed):
        before = inventory(seed, guard)
        R.mkdir(mode=0o700)
        state = R / 'state'
        state.mkdir(mode=0o700)
        for relative, entry in sorted(before['entries'].items(), key=lambda item: len(Path(item[0]).parts)):
            if relative == '.':
                continue
            src, dst = seed / relative, state / relative
            if entry['type'] == 'directory':
                dst.mkdir(mode=0o700)
            else:
                shutil.copy2(src, dst, follow_symlinks=False)
                if src.stat().st_ino == dst.stat().st_ino and src.stat().st_dev == dst.stat().st_dev:
                    raise RuntimeError('Writable state hardlink forbidden')
            guard()
        for relative, entry in sorted(before['entries'].items(), key=lambda item: -len(Path(item[0]).parts)):
            if entry['type'] == 'directory':
                (state / relative).chmod(entry['mode'])
        copied = inventory(state, guard)
        after = inventory(seed, guard)
        if not before['inventorySHA256'] == after['inventorySHA256'] == copied['inventorySHA256']:
            raise RuntimeError('Independent copied writable state inventory mismatch')
    save(P / 'exploration-base-before.json', base_inventory)
    save(P / 'exploration-seed-before.json', before)
    save(P / 'exploration-state-prepared.json', copied)
    save(P / 'exploration-dependencies.json', {'scope': 'RESOLUTION_ONLY_NO_GUEST_OR_ENGINE_MAIN',
                                             'traces': traces, 'resolved_files': closure})
    save(P / 'EXPLORATION-TOOLS.json', {name: pins['tool:' + name] for name in ['Xvfb', 'xdotool', 'ffmpeg']})
    argv = [pins['frontend']['path'], '--engine', pins['engine']['path'], '--base', str(base),
            '--state', str(state), '--rom', inputs['bootrom'], '--package', inputs['guest_package'],
            '--trace-io', '--no-audio', '--seconds', '260', '--screenshot', str(R / 'live.bmp')]
    cfg = {'schema': 1, 'scope': 'ONE_CANDIDATE_EXPLORATION_DEFAULT_OFF_NOT_BENCHMARK',
           'argv': argv, 'base': str(base), 'seed': str(seed), 'state': str(state),
           'seedInventorySHA256': before['inventorySHA256'], 'pins': pins,
           'privateDependency': str(Path(pins['privateLibfdt']['path']).parent),
           'baseMetadata': metadata, 'lockPolicy': str(lock_policy), 'lockReference': reference,
           'removeEnvironment': ['PYTHONHOME', 'PYTHONPATH', 'LT_TCG_IR_CACHE_ENABLE', 'LT_TCG_IR_CACHE',
                                 'LD_PRELOAD', 'IT_VCPU_ACCT', 'IT_VCPU_ACCT_LOG', 'IT_USB_TCP', 'IT_AUDIO_HW',
                                 'IT_GLES_DEBUG', 'IT_I2S_TONE', 'IT_I2S_DUMP', 'ASAN_OPTIONS', 'UBSAN_OPTIONS',
                                 'SDL_AUDIODRIVER', 'SDL_RENDER_DRIVER', 'LIBGL_ALWAYS_SOFTWARE'],
           'recordEnvironment': ['DISPLAY', 'LD_BIND_NOW', 'LD_LIBRARY_PATH', 'SDL_VIDEODRIVER',
                                 'EGL_PLATFORM', 'IT_SHUTDOWN_DIAG', 'LT_TCG_IR_CACHE_ENABLE', 'LT_TCG_IR_CACHE'],
           'limits': {'HomeSecondsFromHello': 129, 'StopSecondsFromHello': 205, 'nativeStopGraceSeconds': 35,
                      'outerSecondsFromControllerOrigin': 1200},
           'guestExecuted': False, 'sourceSeedUnchanged': True, 'isolatedStatePrepared': True}
    cfg['cacheTrial'] = {'mode': args.cache_mode}
    if args.matched_shutdown_diagnostic:
        cfg['matchedDiagnostic'] = {'mode': matched_trial.MODE}
    if args.cache_mode != 'off':
        cfg['scope'] = 'ONE_EXPLICIT_EXPERIMENTAL_CACHE_CORRECTNESS_NOT_BENCHMARK'
        cfg['cacheTrial']['directory'] = str(args.cache_directory)
    # Read-only cache inventory at preparation; checked again after fresh Root
    # grant consumption and immediately before the only native worker starts.
    save(P / 'cache-preparation-before.json', cache_trial.before(cfg))
    save(P / 'EXPLORATION-INPUTS.json', cfg)
    print(__import__('json').dumps({'prepared': True, 'writableFiles': sum(v['type'] == 'file' for v in copied['entries'].values()),
          'writableBytes': sum(v.get('size', 0) for v in copied['entries'].values()),
          'baseEntries': len(base_inventory['entries']), 'seedInventorySHA256': before['inventorySHA256'],
          'sourceSeedUnchanged': True, 'guestExecuted': False, 'elapsedSeconds': (time.monotonic_ns() - started)/SECOND}, indent=2))

if __name__ == '__main__':
    main()
