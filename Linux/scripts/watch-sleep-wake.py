#!/usr/bin/env python3
"""Before-HELLO hardware wake witness; never classify or unlock a scene.

    Waiting for a live guest agent BEFORE waking can block the wake itself.
    Only current RUNNING+sleeping status admits the balanced Power handler.
    The handler waits for fresh awake/agent diagnostics AFTER that pulse.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import sys
import time


def sleep_candidate(status, now_ns):
    try:
        return (int(status['state']) == 2 and int(status['sleeping']) == 1 and
                0 <= now_ns - int(status['observed_monotonic_ns']) <= 1_000_000_000)
    except (ValueError, TypeError, KeyError):
        return False


def first_hello(rows):
    values = {int(row.split(b'=', 1)[1].split()[0]) for row in rows
              if row.startswith(b'admission hello_verified_observed_monotonic_ns=')}
    if len(values) > 1:
        raise RuntimeError('HELLO epoch changed; no observation restart')
    return next(iter(values), None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({'plan_only': True, 'arm_before_first_HELLO': True,
                          'wake_admission': 'current RUNNING+Sleep1+own live PID/window',
                          'agent_precondition_before_wake': False,
                          'one_balanced_Power_ms': 150, 'automatic_scene_or_unlock': False}))
        return
    armed = time.monotonic_ns()
    root = args.run.resolve()
    output = (root / args.output).resolve()
    if root not in output.parents or output.exists():
        raise RuntimeError('new bounded output inside run required')
    started = json.loads((root / 'started.json').read_text())
    inputs = json.loads((root / 'inputs.json').read_text())
    pid = started['frontend']['pid']
    ticks = started['frontend']['start_ticks']
    proc = Path('/proc') / str(pid)
    command = json.loads((root / 'command.json').read_text())['argv']
    handler = root / 'paired-step.py'
    if hashlib.sha256(handler.read_bytes()).hexdigest() != inputs['wake_handler_sha256']:
        raise RuntimeError('reviewed balanced wake handler changed')
    log = root / (args.output + '-watch.jsonl')

    def record(value):
        with log.open('a') as stream:
            stream.write(json.dumps(value) + '\n')

    fd = os.pidfd_open(pid)
    poll = select.poll()
    poll.register(fd, select.POLLIN)

    def verify_owner():
        if (poll.poll(0) or proc.stat().st_uid != started['uid'] or started['uid'] != os.geteuid() or
                proc.joinpath('stat').read_text().rpartition(')')[2].split()[19] != ticks or
                proc.joinpath('cmdline').read_bytes().decode().split('\0')[:-1] != command):
            raise RuntimeError('owner retired or changed; no wake')

    record({'armed_monotonic_ns': armed, 'frontend_pid': pid, 'start_ticks': ticks,
            'agent_before_wake_required': False, 'guest_acceptance': False})
    try:
        while True:
            verify_owner()
            rows = (root / 'session.log').read_bytes().split(b'\n')[:-1]
            hello = first_hello(rows)
            if hello is not None and armed > hello:
                raise RuntimeError('not armed before first HELLO; do not restart observation')
            now = time.monotonic_ns()
            # Native pre-HELLO900/30/5 and independent129/179 watchdogs remain
            # authoritative. This witness cannot extend either session budget.
            if now - started['started_monotonic_ns'] >= 1340_000_000_000:
                raise RuntimeError('outer budget expired')
            if hello is not None and now >= hello + 129_000_000_000:
                raise RuntimeError('original Home deadline elapsed; no wake')
            status_row = next((x for x in reversed(rows) if x.startswith(b'status ')), b'')
            status = dict(re.findall(r'(\w+)=([^ ]+)', status_row.decode()))
            if hello is not None and sleep_candidate(status, now) and (root / 'window.json').exists():
                # The reviewed handler independently rechecks owning frontend,
                # live guest worker, window PID/focus/viewport/current status.
                # It pulses once only if still sleeping; agent is checked by
                # its AFTER-wake fresh-capture observer, not this admission.
                record({'candidate_monotonic_ns': now, 'original_HELLO_ns': hello, 'status': status})
                result = subprocess.run([sys.executable, str(handler), '--step', 'wake',
                                         '--output', args.output, '--execute'],
                                        capture_output=True, text=True, timeout=25)
                record({'handler_returncode': result.returncode, 'stdout': result.stdout,
                        'stderr': result.stderr, 'finished_monotonic_ns': time.monotonic_ns()})
                if result.returncode:
                    raise RuntimeError('balanced wake/fresh capture failed; retain evidence')
                value = json.loads(result.stdout)
                if not value['paired_PNGs']:
                    raise RuntimeError('no actual fresh PNG returned')
                print(json.dumps(value), flush=True)
                return
            time.sleep(.02)
    except BaseException as error:
        # A failed/timed-out input controller must not strand a button or start
        # a second observation. Ordinary owned Stop performs full cancellation
        # and keeps the production35s disposal/dirty-state policy authoritative.
        if not poll.poll(0):
            try:
                signal.pidfd_send_signal(fd, signal.SIGINT)
            except ProcessLookupError:
                pass
        record({'error': str(error), 'finished_monotonic_ns': time.monotonic_ns(),
                'guest_acceptance': False, 'no_clock_reset': True,
                'ordinary_owned_Stop_requested': True})
        raise
    finally:
        os.close(fd)


if __name__ == '__main__':
    main()
