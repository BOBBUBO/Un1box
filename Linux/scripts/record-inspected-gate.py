#!/usr/bin/env python3
"""Record an actual caller-inspected Home/editor with exact fresh continuity.

Call immediately after view_image. --model-inspected is the caller's explicit
observation, never a scene classifier. No engine, worker or clock is started.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import shutil
import subprocess
import time

spec = importlib.util.spec_from_file_location('gates', Path(__file__).with_name('watch-visual-gates.py'))
gates = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gates)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--capture', type=Path, required=True)
    parser.add_argument('--scene', choices=('home', 'editor'), required=True)
    parser.add_argument('--output', required=True, help='new relative evidence directory')
    transitions = parser.add_mutually_exclusive_group()
    transitions.add_argument('--transition-from', type=Path, help='actual pre-action native capture')
    transitions.add_argument('--transition-record', type=Path, help='paired unlock content-continuity.json')
    parser.add_argument('--model-inspected', action='store_true')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({'plan_only': True, 'actual_model_inspection_required': True,
                          'full_BMP_continuity_required': True, 'max_reuse_seconds': 60,
                          'deadlines_unchanged_seconds': [129, 179], 'guest_acceptance': False}))
        return
    if not (args.transition_from or args.transition_record):
        raise RuntimeError('actual pre-action capture required; counters do not classify scenes')
    if not args.model_inspected:
        raise RuntimeError('actual caller model inspection required')
    # Honest completion receipt, after the caller actually inspected the image.
    inspected = time.monotonic_ns()
    root = args.run.resolve()
    original_path = args.capture.resolve()
    out = (root / args.output).resolve()
    if root not in out.parents or root not in original_path.parents:
        raise RuntimeError('evidence outside run')
    out.mkdir(mode=0o700, exist_ok=False)

    def save(name, value):
        (out / name).write_text(json.dumps(value, indent=2) + '\n')

    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    started = json.loads((root / 'started.json').read_text())
    pid = started['frontend']['pid']
    ticks = started['frontend']['start_ticks']
    proc = Path('/proc') / str(pid)
    command = json.loads((root / 'command.json').read_text())['argv']
    worker = json.loads((root / 'worker.json').read_text())
    worker_proc = Path('/proc') / str(worker['pid'])
    window = json.loads((root / 'window.json').read_text())['window']
    env = dict(os.environ, DISPLAY=started['display'], XAUTHORITY=started['xauthority'])
    hello_rows = [x for x in (root / 'session.log').read_bytes().split(b'\n')[:-1]
                  if x.startswith(b'admission hello_verified_observed_monotonic_ns=')]
    hello_values = {int(x.split(b'=', 1)[1].split()[0]) for x in hello_rows}
    if len(hello_values) != 1:
        raise RuntimeError('missing/changed original HELLO')
    hello = hello_values.pop()
    identity = dict(frontend_pid=pid, start_ticks=ticks, hello_monotonic_ns=hello)
    deadline = hello + (129 if args.scene == 'home' else 179) * 1_000_000_000
    operation_deadline = min(deadline, inspected + 12_000_000_000)
    scene = 'Home Screen' if args.scene == 'home' else 'Notes focused new editor'
    gate = dict(identity, model_inspected=True, scene=scene, inspected_monotonic_ns=inspected,
                worker_pid=worker['pid'], worker_start_ticks=worker['start_ticks'],
                capture_directory=str(original_path), bmp_sha256=sha(original_path / 'native.bmp'),
                receipt_sha256=sha(original_path / 'receipt.json'))
    save('original-inspection.json', gate)  # Never replace/backdate this receipt.
    fd = os.pidfd_open(pid)
    poll = select.poll()
    poll.register(fd, select.POLLIN)

    def verify_owner():
        if (poll.poll(0) or proc.stat().st_uid != started['uid'] or started['uid'] != os.geteuid() or
                proc.joinpath('stat').read_text().rpartition(')')[2].split()[19] != ticks or
                proc.joinpath('cmdline').read_bytes().decode().split('\0')[:-1] != command):
            raise RuntimeError('owner retired or changed')
        worker_stat = worker_proc.joinpath('stat').read_text().rpartition(')')[2].split()
        if (worker_proc.stat().st_uid != os.geteuid() or int(worker_stat[1]) != pid or
                worker_stat[19] != worker['start_ticks'] or worker_stat[0] == 'Z' or
                '--worker' not in worker['argv'] or
                worker_proc.joinpath('cmdline').read_bytes().decode().split('\0')[:-1] != worker['argv']):
            raise RuntimeError('owning guest worker retired or changed')

    def call(*argv):
        verify_owner()
        remaining = (operation_deadline - time.monotonic_ns()) / 1e9
        if remaining <= 0:
            raise RuntimeError('fixed scene/operation deadline elapsed')
        return subprocess.run(['xdotool', *argv], env=env, capture_output=True, text=True,
                              timeout=min(2, remaining), check=True).stdout.strip()

    def viewport():
        if call('getwindowpid', str(window)) != str(pid) or call('getwindowfocus') != str(window):
            raise RuntimeError('window owner/focus changed')
        geometry = dict(x.split('=', 1) for x in call('getwindowgeometry', '--shell', str(window)).splitlines())
        result = [int(geometry['WIDTH']), int(geometry['HEIGHT'])]
        if not all(0 < value <= 16384 for value in result):
            raise RuntimeError('invalid viewport')
        return result

    try:
        original, original_bmp = gates.read_capture(root, gate)
        before_path = args.transition_from
        if args.transition_record:
            record = args.transition_record.resolve()
            if root not in record.parents:
                raise RuntimeError('transition record outside run')
            before_path = Path(json.loads(record.read_text())['saved_capture'])
        before_path = before_path.resolve()
        gate['transition_from'] = dict(capture_directory=str(before_path),
                                       receipt_sha256=sha(before_path / 'receipt.json'),
                                       bmp_sha256=sha(before_path / 'native.bmp'))
        before, before_bmp = gates.read_capture(root, gate['transition_from'])
        if (int(before['observed_monotonic_ns']) < hello or
                not gates.valid_scene_transition(before, original, before_bmp, original_bmp)):
            raise RuntimeError('no observed changed pixels/newer source after input; scene transition unproved')
        gate['window'], gate['viewport'] = window, viewport()
        # Sole native screenshot writer: no watcher is started here. Existing
        # readiness/wake capture handlers must have returned before invocation.
        offset = (root / 'session.log').stat().st_size
        trigger = time.monotonic_ns()
        call('key', 's')
        capture_deadline = min(operation_deadline, time.monotonic_ns() + 2_000_000_000)
        receipt = None
        while time.monotonic_ns() < capture_deadline and receipt is None:
            with (root / 'session.log').open('rb') as log:
                log.seek(offset)
                rows = log.read().split(b'\n')[:-1]
            for row in rows:
                if row.startswith(b'screenshot sequence='):
                    line = row.decode()
                    value = dict(re.findall(r'(\w+)=([^ ]+)', line))
                    value['path'] = line.split(' path=', 1)[1]
                    if int(value['observed_monotonic_ns']) >= trigger:
                        receipt = value
                        break
            if receipt is None:
                time.sleep(.02)
        if receipt is None or Path(receipt['path']).resolve() != (root / 'live.bmp').resolve():
            raise RuntimeError('no bounded matching native screenshot')
        if (root / 'live.bmp').stat().st_size > 64 * 1024 * 1024 + 54:
            raise RuntimeError('frame file bounds')
        shutil.copyfile(root / 'live.bmp', out / 'native.bmp')
        receipt['copy_finished_monotonic_ns'] = time.monotonic_ns()
        (out / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
        fresh, fresh_bmp = gates.read_capture(root, dict(
            capture_directory=str(out), receipt_sha256=sha(out / 'receipt.json'),
            bmp_sha256=sha(out / 'native.bmp')))
        checked_viewport = viewport()
        checked = time.monotonic_ns()
        status_row = next(x.decode() for x in reversed((root / 'session.log').read_bytes().split(b'\n')[:-1])
                          if x.startswith(b'status '))
        status = dict(re.findall(r'(\w+)=([^ ]+)', status_row))
        proof = dict(identity, capture_directory=str(out), receipt_sha256=sha(out / 'receipt.json'),
                     worker_pid=worker['pid'], worker_start_ticks=worker['start_ticks'],
                     bmp_sha256=sha(out / 'native.bmp'), window=window, viewport=checked_viewport,
                     owner_checked_monotonic_ns=checked, status=status,
                     metadata_readback=dict(sequence=int(status['frame_sequence']),
                                            frame_monotonic_ns=int(status['frame_monotonic_ns']),
                                            observed_monotonic_ns=int(status['observed_monotonic_ns'])),
                     policy='static-awake-full-image-v1',
                     static_source=int(fresh['sequence']) == int(original['sequence']),
                     verified_monotonic_ns=time.monotonic_ns())
        gate['continuity'] = proof
        save('admission-attempt.json', gate)
        if not gates.valid_continuity_gate(gate, original, fresh, original_bmp, fresh_bmp,
                                           identity, scene, deadline, time.monotonic_ns()):
            raise RuntimeError('current byte continuity/freshness/owner/deadline rejected; inspect retained image')
        verify_owner()
        if time.monotonic_ns() > operation_deadline:
            raise RuntimeError('fixed deadline elapsed before gate publication')
        name = 'home-gate.json' if args.scene == 'home' else 'editor-gate.json'
        if (root / name).exists():
            raise RuntimeError('existing admission gate is retained; refusing replacement')
        temporary = out / 'accepted-gate.json'
        temporary.write_text(json.dumps(gate, indent=2) + '\n')
        os.replace(temporary, root / name)
        print(json.dumps({'gate': str(root / name), 'actual_inspected_monotonic_ns': inspected,
                          'verified_monotonic_ns': proof['verified_monotonic_ns'],
                          'full_BMP_identical': True, 'deadline_monotonic_ns': deadline,
                          'guest_acceptance': False}), flush=True)
    except BaseException as error:
        save('rejection.json', {'error': str(error), 'guest_acceptance': False,
                               'actual_inspected_monotonic_ns': inspected, 'no_clock_reset': True})
        raise
    finally:
        os.close(fd)
        # Conversion belongs after admission/rejection, never before freshness
        # checks. A failed capture is evidence, not permission to relabel it.
        if (out / 'native.bmp').exists():
            subprocess.run(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-i', str(out / 'native.bmp'),
                            '-frames:v', '1', str(out / 'native.png')], timeout=3, check=False)


if __name__ == '__main__':
    main()
