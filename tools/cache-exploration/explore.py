#!/usr/bin/env python3
"""One grant-bound actual n72 exploration; explicit operator scene inspection."""
import argparse
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import sys
import time

from exploration_common import *

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--grant', type=Path)
    args = parser.parse_args()
    cfg = read(P / 'EXPLORATION-INPUTS.json')
    if not args.execute:
        print(__import__('json').dumps({'execution': False, 'argv': cfg['argv'], 'root_grant_required': True}))
        return
    origin = time.monotonic_ns()
    root = cgroup()
    controls = bound_cgroup(root)
    if available() < 8 * 1024**3:
        raise RuntimeError('8GiB available admission failed')
    if __import__('shutil').disk_usage(R).free < 3 * 1024**3:
        raise RuntimeError('3GiB free disk admission failed')
    manifest = read(P / 'EXPLORATION-SOURCE-SHA256.json')
    for relative, expected in manifest.items():
        if digest(P / relative) != expected:
            raise RuntimeError('Packet changed: ' + relative)
    grant = read(args.grant) if args.grant else {}
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    now = time.monotonic_ns()
    if not grant_valid(grant, boot, digest(P / 'EXPLORATION-SOURCE-SHA256.json'), now):
        raise RuntimeError('Fresh exact-packet Root grant missing')
    # One consumed attempt even if preflight/engine later fails.
    with (R / 'consumed.json').open('x') as output:
        __import__('json').dump({'grant': grant, 'origin_ns': origin, 'controller': process(os.getpid())}, output)
    def preparation_guard():
        if time.monotonic_ns() - origin > 1150 * SECOND or available() < 6 * 1024**3:
            raise RuntimeError('Original outer/memory guard during inventory')
    for name, entry in cfg['pins'].items():
        if digest(entry['path']) != entry['sha256']:
            raise RuntimeError('Pinned input/dependency changed: ' + name)
    state = Path(cfg['state'])
    with free_lease(state):
        initial = inventory(state, preparation_guard)
    with free_lease(cfg['seed']):
        original_seed = inventory(cfg['seed'], preparation_guard)
    if initial['inventorySHA256'] != cfg['seedInventorySHA256'] or original_seed['inventorySHA256'] != cfg['seedInventorySHA256']:
        raise RuntimeError('Exact prepared clean seed changed')
    save(R / 'state-before.json', initial)
    base_before = read(P / 'exploration-base-before.json')
    # Native admission rehashes all base contents. Before launch verify all
    # preparation identities/modes/inode/size/times without duplicate contentIO.
    for relative, expected in cfg['baseMetadata'].items():
        info = (Path(cfg['base']) / relative).lstat()
        current = [info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
        if current != expected or info.st_mode & 0o222:
            raise RuntimeError('Immutable base metadata changed: ' + relative)
    env = dict(os.environ, LD_BIND_NOW='1', LD_LIBRARY_PATH=cfg['privateDependency'],
               SDL_VIDEODRIVER='x11', EGL_PLATFORM='surfaceless', IT_SHUTDOWN_DIAG='1',
               OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1')
    for key in cfg['removeEnvironment']:
        env.pop(key, None)
    env.pop('XAUTHORITY', None)
    xvfb = frontend = None
    ffd = wfd = None
    worker = front = None
    hello = stop_clock = None
    signals = []
    error = None
    ordinary_signal = False
    descriptors = []
    log = stderr = None
    auto_count = 0
    engine_mapping = None
    next_capture = next_print = 0
    last_progress = origin
    last_admission = ''
    # Signals from a terminal/service request ordinary Stop, never fresh clocks.
    interrupted = []
    for sig in [signal.SIGTERM, signal.SIGINT]:
        signal.signal(sig, lambda received, _frame: interrupted.append(received))
    def send(descriptor, sig, reason):
        if descriptor is not None and alive(descriptor):
            signal.pidfd_send_signal(descriptor, sig)
            signals.append({'signal': int(sig), 'reason': reason, 'observed_monotonic_ns': time.monotonic_ns()})
    def stop(reason):
        nonlocal stop_clock, ordinary_signal
        if stop_clock is None:
            stop_clock = time.monotonic_ns()
            save(R / 'stop-request.json', {'reason': reason, 'observed_monotonic_ns': stop_clock, 'never_reset': True})
            if ffd is not None:
                send(ffd, signal.SIGINT, reason)
            elif frontend is not None and frontend.poll() is None:
                frontend.send_signal(signal.SIGINT)
            ordinary_signal = True
    try:
        display_read, display_write = os.pipe()
        try:
            xvfb_log = (R / 'xvfb.log').open('wb'); descriptors.append(xvfb_log)
            xvfb = subprocess.Popen([tool('Xvfb'), '-displayfd', str(display_write), '-screen', '0',
                                     '800x1000x24', '-nolisten', 'tcp', '-ac'],
                                    pass_fds=[display_write], stdout=xvfb_log, stderr=xvfb_log)
            os.close(display_write); display_write = None
            if not select.select([display_read], [], [], 5)[0]:
                raise RuntimeError('Isolated Xvfb displayfd timeout')
            display = os.read(display_read, 32).decode().strip()
            if not display.isdigit() or xvfb.poll() is not None:
                raise RuntimeError('Isolated X display failed')
            env['DISPLAY'] = ':' + display
        finally:
            os.close(display_read)
            if display_write is not None:
                os.close(display_write)
        log = (R / 'session.log').open('wb'); stderr = (R / 'stderr.log').open('wb')
        descriptors += [log, stderr]
        save(R / 'command.json', {'argv': cfg['argv'], 'environment': {key: env.get(key) for key in cfg['recordEnvironment']},
                                  'cache': 'off', 'cgroup': str(root), 'controls': controls})
        frontend = subprocess.Popen(cfg['argv'], env=env, stdout=log, stderr=stderr)
        ffd = os.pidfd_open(frontend.pid)
        front = process(frontend.pid)
        if not front or front['argv'] != cfg['argv']:
            raise RuntimeError('Fresh frontend identity unavailable')
        save(R / 'started.json', {'frontend': front, 'boot_id': boot, 'display': env['DISPLAY'],
                                  'controller_origin_ns': origin, 'controller': process(os.getpid())})
        with (R / 'resources.jsonl').open('w') as resources:
            while frontend.poll() is None:
                now = time.monotonic_ns()
                if worker is None:
                    children = Path('/proc', str(frontend.pid), 'task', str(frontend.pid), 'children')
                    for candidate in children.read_text().split():
                        found = process(int(candidate))
                        if found and '--worker' in found['argv'] and cfg['state'] in found['argv']:
                            worker = found; wfd = os.pidfd_open(worker['pid']); save(R / 'worker.json', worker)
                complete = rows(R / 'session.log')
                admissions = [row for row in complete if row.startswith('admission ')]
                if admissions and admissions[-1] != last_admission:
                    last_admission = admissions[-1]; last_progress = now
                hellos = {int(fields(row)['hello_verified_observed_monotonic_ns']) for row in complete
                          if row.startswith('admission hello_verified_observed_monotonic_ns=')}
                if len(hellos) > 1 or hello is not None and hellos != {hello}:
                    raise RuntimeError('Original HELLO changed')
                if hellos:
                    hello = next(iter(hellos))
                    save(R / 'hello.json', {'monotonic_ns': hello})
                if worker and engine_mapping is None:
                    mapped = Path('/proc', str(worker['pid']), 'maps').read_text().splitlines()
                    engine_file = Path(cfg['pins']['engine']['path'])
                    engine_stat = engine_file.stat()
                    matches = [line for line in mapped if line.endswith(' ' + str(engine_file))]
                    if matches:
                        expected_dev = '%02x:%02x' % (os.major(engine_stat.st_dev), os.minor(engine_stat.st_dev))
                        if any(line.split()[3] != expected_dev or int(line.split()[4]) != engine_stat.st_ino for line in matches):
                            raise RuntimeError('Mapped candidate engine dev/inode changed')
                        engine_mapping = {'worker': worker, 'engine': str(engine_file),
                                          'sha256_preflight': cfg['pins']['engine']['sha256'], 'maps': matches}
                        save(R / 'loaded-engine.json', engine_mapping)
                    elif hello is not None:
                        raise RuntimeError('HELLO without exact candidate mapped engine')
                if not (R / 'window.json').exists() and worker:
                    result = subprocess.run([tool('xdotool'), 'search', '--pid', str(frontend.pid), '--name', 'LightTouch'],
                                            env=env, capture_output=True, text=True, timeout=2)
                    windows = result.stdout.split()
                    if len(windows) == 1:
                        pid_result = subprocess.run([tool('xdotool'), 'getwindowpid', windows[0]], env=env,
                                                    capture_output=True, text=True, timeout=2, check=True)
                        if pid_result.stdout.strip() != str(frontend.pid):
                            raise RuntimeError('Window owner changed')
                        subprocess.run([tool('xdotool'), 'windowfocus', '--sync', windows[0]], env=env, check=True, timeout=2)
                        save(R / 'window.json', {'window': int(windows[0]), 'frontend_pid': frontend.pid})
                mem = int((root / 'memory.current').read_text())
                bound_cgroup(root)
                avail = available()
                sample = {'observed_monotonic_ns': now, 'available_bytes': avail, 'cgroup_current_bytes': mem,
                          'memory_events': (root / 'memory.events').read_text(), 'frontend': process(frontend.pid),
                          'worker': process(worker['pid']) if worker else None, 'hello_ns': hello,
                          'loadavg': os.getloadavg()}
                resources.write(__import__('json').dumps(sample) + '\n'); resources.flush()
                save(R / 'progress.json', sample)
                if interrupted:
                    stop('controller signal')
                if avail < 6 * 1024**3 or mem >= 4 * 1024**3 or xvfb.poll() is not None:
                    error = 'memory/display guard'; stop(error)
                if sum((R / name).stat().st_size for name in ['session.log', 'stderr.log']) > 64 * 1024**2:
                    error = 'log byte bound'; stop(error)
                if hello is None and (now - origin >= 900 * SECOND or now - last_progress >= 30 * SECOND):
                    error = 'admission/no-progress deadline'; stop(error)
                if hello is not None and now - hello >= 129 * SECOND and not (R / 'home-inspected.json').exists():
                    error = 'original Home129s deadline'; stop(error)
                if hello is not None and now - hello >= 205 * SECOND:
                    stop('original Stop205s')
                if now - origin >= 1150 * SECOND:
                    error = 'original outer retirement reserve'; stop(error)
                if (R / 'settings-inspected.json').exists():
                    stop('actual operator Home/Settings geometry captured')
                if (R / 'operator-stop.json').exists():
                    stop('operator ordinary Stop')
                if stop_clock is not None and now - stop_clock >= (35 if hello is not None else 5) * SECOND:
                    error = error or 'ordinary Stop grace expired'
                    send(wfd, signal.SIGTERM, error); send(ffd, signal.SIGTERM, error)
                    time.sleep(.5)
                    send(wfd, signal.SIGKILL, 'bounded retirement'); send(ffd, signal.SIGKILL, 'bounded retirement')
                    break
                if hello is not None and worker and (R / 'window.json').exists() and stop_clock is None and now >= next_capture:
                    auto_count += 1
                    try:
                        directory, receipt = capture('auto-%03d' % auto_count)
                        print('ACTUAL_CAPTURE ' + str(directory / 'native.png'), flush=True)
                    except Exception as capture_error:
                        save(R / ('auto-%03d-error.json' % auto_count), {'error': str(capture_error)})
                    next_capture = now + 5 * SECOND
                if now >= next_print:
                    print('PROGRESS ' + __import__('json').dumps(sample), flush=True); next_print = now + 10 * SECOND
                time.sleep(.25)
    except BaseException as exception:
        error = str(exception); save(R / 'controller-error.json', {'error': error})
        stop('controller exception')
    finally:
        if frontend is not None and frontend.poll() is None:
            stop('controller final ordinary Stop')
            deadline = min(origin + 1190 * SECOND, (stop_clock or time.monotonic_ns()) + (35 if hello else 5) * SECOND)
            while frontend.poll() is None and time.monotonic_ns() < deadline:
                time.sleep(.1)
            if frontend.poll() is None:
                send(wfd, signal.SIGTERM, 'final worker disposal'); send(ffd, signal.SIGTERM, 'final frontend disposal')
                time.sleep(.5)
                send(wfd, signal.SIGKILL, 'final worker kill'); send(ffd, signal.SIGKILL, 'final frontend kill')
        rc = frontend.wait(timeout=3) if frontend is not None else None
        if xvfb is not None:
            xvfb.terminate()
            try:
                xvfb.wait(timeout=3)
            except subprocess.TimeoutExpired:
                xvfb.kill(); xvfb.wait(timeout=3)
        for stream in descriptors:
            stream.close()
        seed_after = inventory(cfg['seed'])
        state_after = inventory(state)
        base_after = inventory(cfg['base'])
        save(R / 'seed-after.json', seed_after); save(R / 'state-after.json', state_after); save(R / 'base-after.json', base_after)
        free = False
        try:
            with free_lease(state):
                free = True
        except (BlockingIOError, RuntimeError):
            pass
        log_rows = rows(R / 'session.log') if (R / 'session.log').exists() else []
        final = next((row for row in reversed(log_rows) if row.startswith('FINAL ')), '')
        clean = strict_final(rc, final)
        retired = (ffd is not None and not alive(ffd) and wfd is not None and not alive(wfd)
                   and not same_process(front) and not same_process(worker))
        result = {'scope': 'ACTUAL_CANDIDATE_EXPLORATION_NOT_BENCHMARK', 'exit_code': rc, 'error': error,
                  'confirmed_clean_receipt': clean, 'final_receipt': final, 'frontend': front, 'worker': worker,
                  'first_hello_monotonic_ns': hello, 'origin_monotonic_ns': origin, 'signals': signals,
                  'strict_retirement': retired, 'lease_available': free, 'dirty': (state / '.dirty').exists(),
                  'seed_unchanged': seed_after['inventorySHA256'] == cfg['seedInventorySHA256'],
                  'immutable_base_unchanged': base_after['inventorySHA256'] == base_before['inventorySHA256'],
                  'home_operator_attestation': (R / 'home-inspected.json').exists(),
                  'settings_operator_attestation': (R / 'settings-inspected.json').exists(),
                  'exact_candidate_mapping_observed': engine_mapping is not None,
                  'cache_enabled': False, 'guest_acceptance': False, 'performance_qualified': False,
                  'unit_cgroup_retirement_independent_root_review_required': True}
        result['status'] = 'SCOPED_PASS' if (error is None and clean and retired and free and not result['dirty']
            and result['seed_unchanged'] and result['immutable_base_unchanged']
            and result['home_operator_attestation'] and result['settings_operator_attestation'] and engine_mapping is not None
            and not any(item['signal'] in [9, 15] for item in signals)) else 'FAIL'
        save(R / 'RESULT.json', result)
        for descriptor in [ffd, wfd]:
            if descriptor is not None:
                os.close(descriptor)
        print(__import__('json').dumps(result, indent=2), flush=True)
    if result['status'] != 'SCOPED_PASS':
        raise SystemExit(1)

if __name__ == '__main__':
    main()
