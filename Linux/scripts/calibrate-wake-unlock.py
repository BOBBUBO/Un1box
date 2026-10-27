#!/usr/bin/env python3
"""One bounded wake/slider observation cycle; caller inspects pixels before Photos admission."""
import argparse
import json
from pathlib import Path
import struct
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--window', type=int, required=True)
    parser.add_argument('--viewport', type=int, nargs=2, required=True, metavar=('WIDTH', 'HEIGHT'))
    parser.add_argument('--log', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if args.window <= 0 or min(args.viewport) <= 0:
        parser.error('positive private SDL window and viewport dimensions required')
    plan = {'precondition': 'Actual private SDL window in portrait; launch owner supplies200s fallback',
            'sequence': ['Home150ms', 'wait2s', 'paired wake native capture',
                         'slider(.18,.90) to(.92,.90), ten steps', 'wait2s', 'paired slider native capture'],
            'automatic_unlock_or_readiness_assertions': False,
            'photos_admission': 'Caller inspects fresh native pixels and receipts before any Photos/controller input'}
    if not args.execute:
        print(json.dumps(plan, indent=2))
        return
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    (args.output_dir / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')

    def call(command):
        started = time.monotonic_ns()
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
        with (args.output_dir / 'controls.jsonl').open('a') as log:
            log.write(json.dumps({'started_monotonic_ns': started, 'finished_monotonic_ns': time.monotonic_ns(),
                                 'argv': command, 'exit_code': result.returncode,
                                 'stdout': result.stdout, 'stderr': result.stderr}) + '\n')
        if result.returncode:
            raise RuntimeError(result.stderr)

    def key(action, value):
        call(['xdotool', action, '--window', str(args.window), value])

    def capture(name):
        call([sys.executable, str(Path(__file__).with_name('capture-native-checkpoint.py')),
              '--window', str(args.window), '--log', str(args.log),
              '--output-dir', str(args.output_dir / name)])

    key('keydown', 'h')
    try:
        time.sleep(.15)
    finally:
        key('keyup', 'h')
    time.sleep(2)
    capture('wake')
    # Derive the portrait letterbox from the captured guest dimensions and the
    # caller's measured viewport, rather than copying board dimensions.
    bitmap = (args.output_dir / 'wake/native.bmp').read_bytes()
    guest_width, guest_height = struct.unpack_from('<ii', bitmap, 18)
    guest_height = abs(guest_height)
    scale = min(args.viewport[0] / guest_width, args.viewport[1] / guest_height)
    left = (args.viewport[0] - guest_width * scale) / 2
    top = (args.viewport[1] - guest_height * scale) / 2

    def move(fraction):
        call(['xdotool', 'mousemove', '--window', str(args.window),
              str(round(left + fraction * guest_width * scale)),
              str(round(top + .9 * guest_height * scale))])

    move(.18)
    call(['xdotool', 'mousedown', '1'])
    try:
        for step in range(1, 11):
            move(.18 + (.92 - .18) * step / 10)
            time.sleep(.1)
    finally:
        call(['xdotool', 'mouseup', '1'])
    time.sleep(2)
    capture('slider')


if __name__ == '__main__':
    main()
