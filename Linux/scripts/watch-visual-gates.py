#!/usr/bin/env python3
"""Request ordinary Stop unless inspected Home/editor receipts precede fixed RPCs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import time


def valid_gate(gate, receipt, identity, scene, deadline_ns, now_ns):
    """Human/model scene inspection is supplied; counters never create a gate."""
    try:
        capture = int(receipt['observed_monotonic_ns'])
        source = int(receipt['frame_monotonic_ns'])
        diagnostics = int(receipt['diagnostics_monotonic_ns'])
        inspected = gate['inspected_monotonic_ns']
        return (gate['model_inspected'] is True and gate['scene'] == scene and
                all(gate[k] == identity[k] for k in identity) and
                identity['hello_monotonic_ns'] <= capture <= inspected <= deadline_ns and inspected <= now_ns and
                0 <= inspected - capture <= 5_000_000_000 and
                0 <= capture - source <= 2_000_000_000 and
                0 <= capture - diagnostics <= 1_000_000_000 and
                int(receipt['sequence']) > 1 and int(receipt['sleeping']) == 0 and
                int(receipt['agent']) == 1 and int(receipt['backlight']) > 0)
    except (KeyError, TypeError, ValueError):
        return False


def valid_continuity_gate(gate, original, current, original_bmp, current_bmp,
                          identity, scene, deadline_ns, now_ns):
    """An actual prior scene inspection plus separately timed exact continuity.

    This records a past admission, not a new model inspection. Its verification
    must precede the original deadline; later watchdog polls do not age or reset
    that already-admitted gate. Static publication is permitted only with fresh
    capture/diagnostics and exact same-worker metadata readback. Publication
    timestamps use host CLOCK_MONOTONIC; they do not measure observation age.
    The loader supplies actual bounded file bytes.
    """
    try:
        proof = gate['continuity']
        inspected = int(gate['inspected_monotonic_ns'])
        verified = int(proof['verified_monotonic_ns'])
        captured = int(current['observed_monotonic_ns'])
        checked = int(proof['owner_checked_monotonic_ns'])
        status = proof['status']
        viewport = proof['viewport']
        readback = proof['metadata_readback']
        return (proof['policy'] == 'static-awake-full-image-v1' and
                gate['model_inspected'] is True and gate['scene'] == scene and
                all(gate[k] == proof[k] == identity[k] for k in identity) and
                gate['worker_pid'] == proof['worker_pid'] and
                gate['worker_start_ticks'] == proof['worker_start_ticks'] and
                isinstance(proof['worker_pid'], int) and proof['worker_pid'] > 0 and
                proof['worker_pid'] != identity['frontend_pid'] and bool(proof['worker_start_ticks']) and
                identity['hello_monotonic_ns'] <= int(original['observed_monotonic_ns']) <=
                inspected <= captured <= checked <= verified <= deadline_ns and verified <= now_ns and
                0 <= verified - inspected <= 60_000_000_000 and
                bool(original_bmp) and original_bmp == current_bmp and
                all(int(r['sequence']) > 1 and int(r['sleeping']) == 0 and
                    int(r['agent']) == 1 and int(r['backlight']) > 0 for r in (original, current)) and
                0 < int(original['frame_monotonic_ns']) <= int(original['observed_monotonic_ns']) and
                0 <= int(original['observed_monotonic_ns']) - int(original['diagnostics_monotonic_ns']) <= 1_000_000_000 and
                0 <= verified - captured <= 2_000_000_000 and
                int(current['sequence']) >= int(original['sequence']) and
                0 < int(original['frame_monotonic_ns']) <= int(current['frame_monotonic_ns']) <= captured and
                0 <= verified - int(current['copy_finished_monotonic_ns']) <= 2_000_000_000 and
                captured <= int(current['copy_finished_monotonic_ns']) <= checked and
                int(readback['sequence']) == int(current['sequence']) == int(status['frame_sequence']) and
                int(readback['frame_monotonic_ns']) == int(current['frame_monotonic_ns']) == int(status['frame_monotonic_ns']) and
                int(readback['observed_monotonic_ns']) == int(status['observed_monotonic_ns']) and
                0 <= verified - int(current['diagnostics_monotonic_ns']) <= 1_000_000_000 and
                0 <= verified - int(status['observed_monotonic_ns']) <= 1_000_000_000 and
                int(status['state']) == 2 and int(status['sleeping']) == 0 and
                int(status['agent']) == 1 and int(status['backlight']) > 0 and
                isinstance(proof['window'], int) and proof['window'] > 0 and
                gate['window'] == proof['window'] and gate['viewport'] == viewport and
                len(viewport) == 2 and all(isinstance(v, int) and 0 < v <= 16384 for v in viewport))
    except (KeyError, TypeError, ValueError):
        return False


def valid_scene_transition(before, after, before_bmp, after_bmp):
    """Actual changed pixels and a newer source after input, never a scene label."""
    try:
        return (bool(before_bmp) and bool(after_bmp) and before_bmp != after_bmp and
                int(after['sequence']) > int(before['sequence']) > 1 and
                int(after['observed_monotonic_ns']) > int(before['observed_monotonic_ns']) and
                0 < int(before['frame_monotonic_ns']) <= int(after['frame_monotonic_ns']) <=
                int(after['observed_monotonic_ns']))
    except (KeyError, TypeError, ValueError):
        return False


def read_capture(root, description):
    path = Path(description['capture_directory']).resolve()
    if root not in path.parents:
        raise ValueError('capture outside run')
    if (path / 'receipt.json').stat().st_size > 16_384 or (path / 'native.bmp').stat().st_size > 64 * 1024 * 1024 + 54:
        raise ValueError('capture bounds')
    receipt_bytes = (path / 'receipt.json').read_bytes()
    bitmap = (path / 'native.bmp').read_bytes()
    if (hashlib.sha256(receipt_bytes).hexdigest() != description['receipt_sha256'] or
            hashlib.sha256(bitmap).hexdigest() != description['bmp_sha256']):
        raise ValueError('capture hashes changed')
    receipt = json.loads(receipt_bytes)
    return receipt.get('receipt', receipt), bitmap


def load_gate(root, name, identity, scene, deadline_ns, now_ns):
    try:
        gate = json.loads((root / name).read_text())
        receipt, bitmap = read_capture(root, gate)
        if 'continuity' in gate:
            worker = json.loads((root / 'worker.json').read_text())
            if gate['worker_pid'] != worker['pid'] or gate['worker_start_ticks'] != worker['start_ticks']:
                return False
            current, current_bitmap = read_capture(root, gate['continuity'])
            before, before_bitmap = read_capture(root, gate['transition_from'])
            if (int(before['observed_monotonic_ns']) < identity['hello_monotonic_ns'] or
                    not valid_scene_transition(before, receipt, before_bitmap, bitmap)):
                return False
            return valid_continuity_gate(gate, receipt, current, bitmap, current_bitmap,
                                         identity, scene, deadline_ns, now_ns)
        return valid_gate(gate, receipt, identity, scene, deadline_ns, now_ns)
    except (OSError, ValueError, TypeError, KeyError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({'plan_only': True, 'home_deadline_seconds': 129,
                          'editor_deadline_seconds': 179, 'clock': 'first verified HELLO, never reset',
                          'signal': 'ordinary SIGINT through owned pidfd', 'automatic_scene_assertion': False}))
        return
    root = args.run.resolve()
    started = json.loads((root / 'started.json').read_text())
    pid = started['frontend']['pid']
    ticks = started['frontend']['start_ticks']
    proc = Path('/proc') / str(pid)
    if (proc.stat().st_uid != os.geteuid() or started['uid'] != os.geteuid() or
            proc.joinpath('stat').read_text().rpartition(')')[2].split()[19] != ticks):
        raise RuntimeError('frontend identity mismatch')
    command = json.loads((root / 'command.json').read_text())['argv']
    if proc.joinpath('cmdline').read_bytes().decode().split('\0')[:-1] != command or '--worker' in command:
        raise RuntimeError('frontend argv mismatch')
    fd = os.pidfd_open(pid)
    poll = select.poll()
    poll.register(fd, select.POLLIN)
    hello = None
    gates = [('home-gate.json', 'Home Screen', 129), ('editor-gate.json', 'Notes focused new editor', 179)]
    try:
        while not poll.poll(0):
            # Only newline-terminated records can establish the one session epoch.
            rows = (root / 'session.log').read_bytes().split(b'\n')[:-1]
            for row in rows:
                if row.startswith(b'admission hello_verified_observed_monotonic_ns='):
                    observed = int(row.split(b'=', 1)[1].split()[0])
                    if hello is not None and observed != hello:
                        raise RuntimeError('HELLO epoch changed; refusing deadline restart')
                    hello = observed
            if hello is None:
                time.sleep(.05)
                continue  # Production frontend already enforces pre-HELLO900/30/5.
            identity = {'frontend_pid': pid, 'start_ticks': ticks, 'hello_monotonic_ns': hello}
            now = time.monotonic_ns()
            for name, scene, seconds in gates:
                deadline = hello + seconds * 1_000_000_000
                if now >= deadline and not load_gate(root, name, identity, scene, deadline, now):
                    if not poll.poll(0):
                        signal.pidfd_send_signal(fd, signal.SIGINT)
                    result = {'ordinary_stop_requested': True, 'missing_gate': name,
                              'deadline_monotonic_ns': deadline, 'observed_monotonic_ns': now,
                              'identity': identity, 'guest_acceptance': False}
                    (root / 'visual-gate-stop.json').write_text(json.dumps(result, indent=2) + '\n')
                    print(json.dumps(result), flush=True)
                    return
            if now >= hello + 179_000_000_000:
                print(json.dumps({'both_inspection_gates_present': True, 'guest_acceptance': False}), flush=True)
                return
            time.sleep(.05)
    except BaseException as error:
        if not poll.poll(0):
            signal.pidfd_send_signal(fd, signal.SIGINT)
        (root / 'visual-gate-error.json').write_text(json.dumps({
            'ordinary_stop_requested': True, 'error': str(error), 'guest_acceptance': False,
            'frontend_pid': pid, 'start_ticks': ticks, 'observed_monotonic_ns': time.monotonic_ns()}) + '\n')
        raise
    finally:
        os.close(fd)


if __name__ == '__main__':
    main()
