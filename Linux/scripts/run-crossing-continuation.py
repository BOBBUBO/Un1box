#!/usr/bin/env python3
"""Observe existing production F7 crossing/lift replay; never infer a guest pass."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--window', type=int, required=True)
    parser.add_argument('--log', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--video-size', default='800x1000')
    args = parser.parse_args()
    plan = {'precondition': 'Independently inspected current Photos image enlarged above fit, below maximum, '
                            'with room to pan; current presentation portrait',
            'production_input': 'Existing F7 replay:201/202 cross,201 lifts atstep13,202 alone moves at14/15, ends16',
            'native_checkpoints_seconds_after_key': {'crossed-two': .97, 'lift-boundary': 1.075,
                                                       'remaining-motion': 1.225, 'released': 1.6},
            'qualification': 'Checkpoint names are targets, not actual contact phases. Check applied/wire/source timestamps; '
                             'Visible translation with constant image scale while only202 remains is required.',
            'acceptance_assertions': False, 'shutdown': 'Launch owner requests Escape and waits confirmed clean receipt'}
    if not args.execute:
        print(json.dumps(plan, indent=2))
        return
    if args.window <= 0:
        parser.error('positive private SDL window required')
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    (args.output_dir / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')

    def call(command):
        start = time.monotonic_ns()
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        with (args.output_dir / 'controls.jsonl').open('a') as log:
            log.write(json.dumps({'started_monotonic_ns': start, 'finished_monotonic_ns': time.monotonic_ns(),
                                 'argv': command, 'exit_code': result.returncode,
                                 'stdout': result.stdout, 'stderr': result.stderr}) + '\n')
        if result.returncode:
            raise RuntimeError(result.stderr)

    def native(name, deferred=False):
        command = [sys.executable, str(Path(__file__).with_name('capture-native-checkpoint.py')),
                   '--window', str(args.window), '--log', str(args.log),
                   '--output-dir', str(args.output_dir / name)]
        if deferred:
            command.append('--defer-preview')
        call(command)

    def gui(name):
        call(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-f', 'x11grab',
              '-video_size', args.video_size, '-i', os.environ['DISPLAY'], '-frames:v', '1',
              '-threads', '1', str(args.output_dir / (name + '-gui.png'))])

    try:
        native('before')
        gui('before')
        origin = time.monotonic_ns()
        call(['xdotool', 'key', '--window', str(args.window), 'F7'])
        timeline = []
        # Native BMP/receipt copies happen now; defer PNG work until all contact
        # phases finish. Each completed copy precedes the next screenshot key,
        # avoiding a shared live.bmp overwrite racing an earlier receipt.
        for name, offset in plan['native_checkpoints_seconds_after_key'].items():
            target = origin + int(offset * 1e9)
            while True:
                remaining = (target - time.monotonic_ns()) / 1e9
                if remaining <= 0:
                    break
                time.sleep(min(.005, remaining))
            began = time.monotonic_ns()
            native(name, deferred=True)
            receipt = json.loads((args.output_dir / name / 'receipt.json').read_text())
            timeline.append({'name': name, 'target_monotonic_ns': target, 'started_monotonic_ns': began,
                             'finished_monotonic_ns': time.monotonic_ns(), 'lateness_ns': max(0, began-target),
                             'receipt': receipt})
            (args.output_dir / 'timeline.json').write_text(json.dumps(timeline, indent=2) + '\n')
        for name in plan['native_checkpoints_seconds_after_key']:
            call(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-i',
                  str(args.output_dir / name / 'native.bmp'), '-frames:v', '1', '-threads', '1',
                  str(args.output_dir / name / 'native.png')])
        gui('released')
    finally:
        # No stop/kill and no synthesized success. Balance any remaining replay
        # input; the owner retains the session and performs semantic shutdown.
        call(['xdotool', 'key', '--window', str(args.window), 'c'])


if __name__ == '__main__':
    main()
