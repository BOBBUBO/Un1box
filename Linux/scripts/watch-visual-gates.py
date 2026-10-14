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


def load_gate(root, name, identity, scene, deadline_ns, now_ns):
    try:
        gate = json.loads((root / name).read_text())
        path = Path(gate['capture_directory']).resolve()
        if root not in path.parents:
            return False
        if (path / 'receipt.json').stat().st_size > 16_384 or (path / 'native.bmp').stat().st_size > 64 * 1024 * 1024 + 54:
            return False
        receipt_bytes = (path / 'receipt.json').read_bytes()
        bitmap = (path / 'native.bmp').read_bytes()
        if (
                hashlib.sha256(receipt_bytes).hexdigest() != gate['receipt_sha256'] or
                hashlib.sha256(bitmap).hexdigest() != gate['bmp_sha256']):
            return False
        receipt = json.loads(receipt_bytes)
        receipt = receipt.get('receipt', receipt)
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
