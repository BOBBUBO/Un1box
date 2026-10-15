#!/usr/bin/env python3
"""Capture a fresh SDL frame through S; never infer a scene or send guest input."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import select
import struct
import subprocess
import sys
import time

SECOND = 1_000_000_000
MAX_BITMAP = 54 + 4096 * 4096 * 4
active_output = None


class CompleteLines:
    def __init__(self):
        self.pending = b''
        self.discarded = 0
        self.dropping = False

    def feed(self, data):
        if self.dropping:
            end = data.find(b'\n')
            if end < 0:
                return []
            data = data[end + 1:]
            self.dropping = False
        data = self.pending + data
        parts = data.split(b'\n')
        self.pending = parts.pop()
        if len(self.pending) > 8192:
            self.pending = b''
            self.discarded += 1
            self.dropping = True
        result = []
        for part in parts:
            if len(part) <= 8192:
                result.append(part.decode(errors='replace'))
            else:
                self.discarded += 1
        return result


def values(line):
    return dict(re.findall(r'(\w+)=([^ ]+)', line))


class Readiness:
    def __init__(self):
        self.hello = False
        self.frame = None
        self.status = None
        self.last_attempt = -1
        self.last_trigger = -SECOND
        self.invalid_rows = 0

    def ingest(self, line):
        v = values(line)
        try:
            if line.startswith('admission hello_verified_'):
                int(v['hello_verified_observed_monotonic_ns'])
                self.hello = True
            elif line.startswith('trace_frame '):
                self.frame = {k: int(v[k]) for k in ('sequence', 'source_monotonic_ns', 'observed_monotonic_ns')}
            elif line.startswith('status '):
                self.status = {k: int(v[k]) for k in ('observed_monotonic_ns', 'state', 'sleeping', 'backlight', 'agent')}
        except (KeyError, ValueError):
            self.invalid_rows += 1
            if line.startswith('status '):
                self.status = None
            elif line.startswith('trace_frame '):
                self.frame = None

    def candidate(self, now):
        f, s = self.frame, self.status
        if not self.hello or not f or not s:
            return None
        if (f['sequence'] <= max(1, self.last_attempt) or s['state'] != 2 or
                s['sleeping'] != 0 or s['backlight'] <= 0 or s['agent'] != 1 or
                not 0 <= now - f['source_monotonic_ns'] <= SECOND or
                not 0 <= now - f['observed_monotonic_ns'] <= SECOND or
                not 0 <= now - s['observed_monotonic_ns'] <= 750_000_000 or
                now - self.last_trigger < SECOND // 2):
            return None
        return dict(frame=f.copy(), status=s.copy(), trigger_monotonic_ns=now)

    def attempt(self, candidate):
        self.last_attempt = candidate['frame']['sequence']
        self.last_trigger = candidate['trigger_monotonic_ns']


def native_bitmap_nonblack(bitmap):
    if len(bitmap) < 54 or len(bitmap) > MAX_BITMAP or bitmap[:2] != b'BM':
        raise ValueError('Invalid bounded native BMP')
    declared, offset = struct.unpack_from('<I', bitmap, 2)[0], struct.unpack_from('<I', bitmap, 10)[0]
    header, width, height, planes, bits, compression = struct.unpack_from('<IiiHHI', bitmap, 14)
    if (declared != len(bitmap) or offset != 54 or header != 40 or
            not 0 < width <= 4096 or not -4096 <= height < 0 or
            planes != 1 or bits != 32 or compression != 0 or
            len(bitmap) != 54 + width * -height * 4):
        raise ValueError('Native BMP layout/bounds mismatch')
    # The fourth byte is unused display alpha and is deliberately excluded.
    return any(bitmap[54::4]) or any(bitmap[55::4]) or any(bitmap[56::4])


def capture_flags(receipt, bitmap, candidate):
    captured = int(receipt['observed_monotonic_ns'])
    frame_age = captured - int(receipt['frame_monotonic_ns'])
    diagnostics_age = captured - int(receipt['diagnostics_monotonic_ns'])
    return {'rgb_nonzero': native_bitmap_nonblack(bitmap),
            'fresh_frame': 0 <= frame_age <= SECOND,
            'fresh_diagnostics': 0 <= diagnostics_age <= 750_000_000,
            'sleeping_zero': int(receipt['sleeping']) == 0,
            'agent_alive': int(receipt['agent']) == 1,
            'backlight_positive': int(receipt['backlight']) > 0,
            'sequence_at_least_trigger': int(receipt['sequence']) >= candidate['frame']['sequence'],
            'capture_after_trigger': captured >= candidate['trigger_monotonic_ns']}


def ready_preview(capture_root, handler_result, identity, now):
    """Select qualified pixels only after the sole capture writer has exited.

    The handler waits for that writer and finishes preview conversion before
    publishing this handoff. None of these checks identifies a guest scene.
    """
    if (handler_result.get('capture_writer_exited') is not True or
            handler_result.get('watcher_returncode') != 0 or
            handler_result.get('identity') != identity):
        return None
    candidates = []
    for path in sorted(capture_root.glob('capture-*')):
        try:
            if (path / 'receipt.json').stat().st_size > 16_384:
                continue
            record = json.loads((path / 'receipt.json').read_text())
            if (path / 'native.bmp').stat().st_size > MAX_BITMAP:
                continue
            bitmap = (path / 'native.bmp').read_bytes()
            receipt = record['receipt']
            if (hashlib.sha256(bitmap).hexdigest() != record['sha256'] or
                    not all(capture_flags(receipt, bitmap, record['candidate']).values()) or
                    not 0 <= now - int(receipt['frame_monotonic_ns']) <= 2 * SECOND or
                    not int(receipt['observed_monotonic_ns']) <= int(record['copied_monotonic_ns']) <= now or
                    not 0 <= now - int(record['copied_monotonic_ns']) <= 2 * SECOND or
                    int(receipt['coherent_readback']) != 1 or
                    int(receipt['shared_generation']) <= 0 or int(receipt['shared_generation']) % 2):
                continue
            png = path / 'native.png'
            if png.stat().st_size > MAX_BITMAP:
                continue
            data = png.read_bytes()
            if (len(data) > MAX_BITMAP or not data.startswith(b'\x89PNG\r\n\x1a\n') or
                    not data.endswith(b'\0\0\0\0IEND\xaeB`\x82') or
                    handler_result.get('preview_sha256', {}).get(str(png)) != hashlib.sha256(data).hexdigest()):
                continue  # A partially written preview is never published.
            candidates.append((int(receipt['observed_monotonic_ns']), png))
        except (OSError, ValueError, TypeError, KeyError, struct.error):
            continue
    return max(candidates, default=(None, None), key=lambda item: item[0])[1]


def process_identity(pid):
    root = Path(f'/proc/{pid}')
    text = (root / 'stat').read_text()
    fields = text[text.rfind(')') + 2:].split()
    uid = next(l for l in (root / 'status').read_text().splitlines() if l.startswith('Uid:')).split()[1:]
    return {'start_ticks': fields[19], 'uid': [int(v) for v in uid],
            'argv': (root / 'cmdline').read_bytes().decode().split('\0')[:-1]}


def outside_repository(path):
    path = path.resolve()
    repo = Path(__file__).resolve().parents[2]
    if path == repo or repo in path.parents:
        raise ValueError('Evidence must be outside the source repository')
    return path


def main():
    global active_output
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pid', type=int, required=True)
    p.add_argument('--start-ticks', required=True)
    p.add_argument('--window', type=int, required=True)
    p.add_argument('--log', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--timeout', type=float, default=1200)
    p.add_argument('--max-captures', type=int, default=12)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument('--stop-after-ready', action='store_true', help='explicit existing first-ready default')
    mode.add_argument('--capture-until-timeout', action='store_true', help='retain multiple frames for bounded menu bursts')
    p.add_argument('--execute', action='store_true')
    args = p.parse_args()
    if args.pid <= 0 or args.window <= 0 or not 0 < args.timeout <= 1200 or not 1 <= args.max_captures <= 24:
        p.error('Positive identities, timeout<=1200 and captures1..24 required')
    output = outside_repository(args.output_dir)
    plan = {'pid': args.pid, 'start_ticks': args.start_ticks, 'window': args.window,
            'poll_seconds': .05, 'maximum_rate_Hz': 2, 'timeout_seconds': args.timeout,
            'input': 'Only S via focused XTest, no --window/stack; no unlock/button/touch/Stop',
            'capture_only': True, 'scene_or_guest_acceptance': False,
            'stop_after_ready': not args.capture_until_timeout,
            'focus': 'Focus once when first candidate arrives; stop injecting if focus later changes'}
    if not args.execute:
        print(json.dumps(plan, indent=2))
        return 0
    identity = process_identity(args.pid)
    if identity['start_ticks'] != args.start_ticks or any(u != os.geteuid() for u in identity['uid']):
        raise RuntimeError('Frontend process identity mismatch')
    if ('--worker' in identity['argv'] or '--headless' in identity['argv'] or
            '--engine' not in identity['argv'] or '--screenshot' not in identity['argv']):
        raise RuntimeError('Expected GUI frontend with explicit private --screenshot, not worker/headless')
    screenshot_index = identity['argv'].index('--screenshot')
    expected_capture = outside_repository(Path(identity['argv'][screenshot_index + 1]))
    log = args.log.resolve()
    own_stdout = os.stat(f'/proc/{args.pid}/fd/1')
    log_stat = log.stat()
    if (own_stdout.st_dev, own_stdout.st_ino) != (log_stat.st_dev, log_stat.st_ino):
        raise RuntimeError('Log is not the exact frontend stdout file')
    environment = dict(x.split('=', 1) for x in Path(f'/proc/{args.pid}/environ').read_bytes().decode().split('\0') if '=' in x)
    if not os.environ.get('DISPLAY') or environment.get('DISPLAY') != os.environ['DISPLAY']:
        raise RuntimeError('Private X display must match the frontend')
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    active_output = output
    (output / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
    pidfd = os.pidfd_open(args.pid)
    poll = select.poll()
    poll.register(pidfd, select.POLLIN)
    gate, lines = Readiness(), CompleteLines()
    deadline = time.monotonic() + args.timeout
    attempts, focused, success = 0, False, False

    def command(argv):
        began = time.monotonic_ns()
        result = subprocess.run(argv, capture_output=True, text=True, timeout=2)
        with (output / 'controls.jsonl').open('a') as f:
            f.write(json.dumps({'argv': argv, 'started_monotonic_ns': began,
                               'finished_monotonic_ns': time.monotonic_ns(), 'exit_code': result.returncode,
                               'stdout': result.stdout, 'stderr': result.stderr}) + '\n')
        if result.returncode:
            raise RuntimeError(result.stderr)
        return result.stdout.strip()

    def verify_window():
        if poll.poll(0) or process_identity(args.pid)['start_ticks'] != args.start_ticks:
            raise RuntimeError('Owning frontend exited/changed')
        if int(command(['xdotool', 'getwindowpid', str(args.window)])) != args.pid:
            raise RuntimeError('_NET_WM_PID does not match owning frontend')

    with log.open('rb') as source:
        while time.monotonic() < deadline and not poll.poll(0) and attempts < args.max_captures:
            for line in lines.feed(source.read(65536)):
                gate.ingest(line)
            candidate = gate.candidate(time.monotonic_ns())
            if not candidate:
                time.sleep(.05)
                continue
            verify_window()
            if not focused:
                command(['xdotool', 'windowfocus', '--sync', str(args.window)])
                focused = True
            if int(command(['xdotool', 'getwindowfocus'])) != args.window:
                raise RuntimeError('Capture focus changed; no automatic reacquisition')
            # Recheck freshness after identity/focus syscalls before any key event.
            candidate = gate.candidate(time.monotonic_ns())
            if not candidate:
                continue
            gate.attempt(candidate)
            attempts += 1
            target = output / f'capture-{attempts:02d}'
            target.mkdir(mode=0o700)
            offset = log.stat().st_size
            # New xdotool invocation, no window stack and no --window: XTest.
            command(['xdotool', 'key', 's'])
            receipt = None
            tail = CompleteLines()
            with log.open('rb') as capture_log:
                capture_log.seek(offset)
                until = time.monotonic() + 2
                while time.monotonic() < until and not poll.poll(0):
                    for line in tail.feed(capture_log.read(65536)):
                        if line.startswith('screenshot sequence='):
                            v = values(line)
                            if int(v.get('observed_monotonic_ns', 0)) >= candidate['trigger_monotonic_ns']:
                                v['path'] = line.split(' path=', 1)[1]
                                receipt = dict(v, exact_log_line=line)
                                break
                    if receipt:
                        break
                    time.sleep(.02)
            record = {'candidate': candidate, 'receipt': receipt, 'capture_only': True,
                      'scene_or_guest_acceptance': False}
            if receipt:
                native = outside_repository(Path(receipt['path']))
                if native != expected_capture:
                    raise RuntimeError('Capture path differs from owning frontend --screenshot')
                if native.stat().st_size > MAX_BITMAP:
                    raise RuntimeError('Native capture exceeds bitmap bounds')
                bitmap = native.read_bytes()
                (target / 'native.bmp').write_bytes(bitmap)
                record['copied_monotonic_ns'] = time.monotonic_ns()
                record['sha256'] = hashlib.sha256(bitmap).hexdigest()
                try:
                    record['flags'] = capture_flags(receipt, bitmap, candidate)
                    success = success or all(record['flags'].values())
                    gate.last_attempt = max(gate.last_attempt, int(receipt['sequence']))
                except (KeyError, ValueError) as error:
                    record['failure'] = 'Rejected copied capture: ' + str(error)
            else:
                record['failure'] = 'No matching complete native capture receipt within2s'
            (target / 'receipt.json').write_text(json.dumps(record, indent=2) + '\n')
            print(json.dumps({'capture': str(target), 'flags': record.get('flags'), 'capture_only': True}), flush=True)
            if success and not args.capture_until_timeout:
                break
    os.close(pidfd)
    result = {'fresh_nonblack_capture_retained': success, 'attempts': attempts,
              'capture_only': True, 'scene_or_guest_acceptance': False,
              'invalid_log_rows': gate.invalid_rows, 'oversized_log_lines': lines.discarded,
              'preview_deferred': True, 'no_guest_stop_requested': True}
    (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)
    return 0 if success else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        failure = {'failure': str(error), 'capture_only': True,
                   'scene_or_guest_acceptance': False, 'guest_stop_requested': False}
        if active_output is not None:
            (active_output / 'failure.json').write_text(json.dumps(failure, indent=2) + '\n')
        print(json.dumps(failure), file=sys.stderr)
        raise SystemExit(1)
