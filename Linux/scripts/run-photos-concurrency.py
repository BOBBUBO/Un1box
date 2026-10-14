#!/usr/bin/env python3
"""Replay one bounded Photos/shutter sequence in an already running private SDL guest.

Default prints the plan. Pixels and native status must be reviewed by the caller;
this controller never asserts guest acceptance or audible output.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--window', type=int, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--video-size', default='800x1000')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    plan = {
        'window': args.window,
        'precondition': 'Visually inspected Photos image viewer, below maximum zoom; fresh private worker/state',
        'sequence': ['before capture', 'Power down, Home down, F6', 'during capture, balanced button release',
                     'second F6 at pulse+1.2s', 'during delayed shutter capture', 'after capture',
                     'rotation cancel, restore portrait, inspect', 'Escape requests confirmed shutdown'],
        'f6_duration_seconds': .96,
        'second_replay_delay_seconds': 1.2,
        'acceptance_assertions': False,
        'physical_audibility_verified': False,
    }
    if not args.execute:
        print(json.dumps(plan, indent=2))
        return
    if args.window <= 0 or not os.environ.get('DISPLAY'):
        parser.error('a positive SDL window ID and private DISPLAY are required')
    xdotool, ffmpeg = shutil.which('xdotool'), shutil.which('ffmpeg')
    if not xdotool or not ffmpeg:
        parser.error('xdotool and ffmpeg must be available')
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    (args.output_dir / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')

    def call(command):
        started = time.monotonic_ns()
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        record = {'started_monotonic_ns': started, 'finished_monotonic_ns': time.monotonic_ns(),
                  'argv': command, 'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
        with (args.output_dir / 'controls.jsonl').open('a') as log:
            log.write(json.dumps(record) + '\n')
        if result.returncode:
            raise RuntimeError(f'Control failed: {command}: {result.stderr}')

    def key(action, value):
        call([xdotool, action, '--window', str(args.window), value])

    def capture(name):
        call([ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-f', 'x11grab', '-video_size',
              args.video_size, '-i', os.environ['DISPLAY'], '-frames:v', '1', '-threads', '1',
              str(args.output_dir / (name + '.png'))])

    def wait_until(origin, seconds):
        time.sleep(max(0, origin + seconds - time.monotonic()))

    try:
        capture('before')
        pulse = time.monotonic()
        key('keydown', 'p')
        key('keydown', 'h')
        key('key', 'F6')
        capture('during-buttons')
        wait_until(pulse, .35)
        key('keyup', 'h')
        key('keyup', 'p')
        # Actual run04 delivered nonzero shutter PCM roughly0.9-1.6s after
        # the chord; the second balanced replay spans1.2-2.16s. Timing is
        # a workload proposal, not proof of overlap on another host.
        wait_until(pulse, 1.2)
        key('key', 'F6')
        capture('during-delayed-shutter')
        wait_until(pulse, 2.4)
        capture('after')
        key('key', 'r')
        capture('rotation-cancel')
        for _ in range(3):
            key('key', 'r')
            time.sleep(.1)
        capture('portrait-after-cancel')
    finally:
        # Balance hardware buttons even if a capture fails. No kill or dirty
        # state repair: the launch owner waits for the native clean receipt.
        cleanup_errors = []
        for action, value in [('keyup', 'h'), ('keyup', 'p'), ('key', 'Escape')]:
            try:
                key(action, value)
            except Exception as error:
                cleanup_errors.append(str(error))
        if cleanup_errors:
            (args.output_dir / 'cleanup-errors.json').write_text(json.dumps(cleanup_errors) + '\n')
            if sys.exc_info()[0] is None:
                raise RuntimeError('; '.join(cleanup_errors))


if __name__ == '__main__':
    main()
