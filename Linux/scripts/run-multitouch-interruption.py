#!/usr/bin/env python3
"""One bounded native replay stage; pixels/traces qualify results, not script exit."""
import argparse
import ctypes
import ctypes.util
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
    parser.add_argument('--stage', choices=['prepare', 'cross', 'cancel', 'focus', 'pause', 'rotation'], required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--video-size', default='800x1000')
    args = parser.parse_args()
    plan = {'stage': args.stage, 'window': args.window,
            'precondition': 'Current Photos image viewer inspected; interruption stages require inspected belowmax zoom',
            'prepare': 'Balanced F5 pinch-in then paired native capture; inspect it before admitting another stage',
            'acceptance_assertions': False, 'shutdown': 'Launch owner requests Escape after all stages and waits clean receipt'}
    if not args.execute:
        print(json.dumps(plan, indent=2))
        return
    if args.window <= 0:
        parser.error('positive private SDL window required')
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

    def key(value):
        call(['xdotool', 'key', '--window', str(args.window), value])

    def gui(name):
        call(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-f', 'x11grab',
              '-video_size', args.video_size, '-i', os.environ['DISPLAY'], '-frames:v', '1',
              '-threads', '1', str(args.output_dir / (name + '-gui.png'))])

    def capture(name):
        call([sys.executable, str(Path(__file__).with_name('capture-native-checkpoint.py')),
              '--window', str(args.window), '--log', str(args.log),
              '--output-dir', str(args.output_dir / name)])
        gui(name)

    paused = False
    try:
        capture('before')
        if args.stage == 'prepare':
            key('F5')
            time.sleep(1.2)
            capture('pinch-in-after')
            return
        if args.stage == 'cross':
            key('F7')
            time.sleep(.55)
            capture('crossing')
            time.sleep(.35)
            capture('independent-lift')
            time.sleep(.55)
            capture('remaining-contact-after')
            return
        key('F6')
        time.sleep(.22)
        if args.stage == 'focus':
            x11 = ctypes.CDLL(ctypes.util.find_library('X11'))
            x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
            x11.XOpenDisplay.restype = ctypes.c_void_p
            x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
            x11.XDefaultRootWindow.restype = ctypes.c_ulong
            display = x11.XOpenDisplay(None)
            if not display:
                raise RuntimeError('Cannot open the assigned private X display')
            root = x11.XDefaultRootWindow(display)
            call(['xdotool', 'windowfocus', '--sync', str(root)])
            time.sleep(.3)
            gui('focus-lost-held')
            call(['xdotool', 'windowfocus', '--sync', str(args.window)])
            x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
            x11.XCloseDisplay(display)
        elif args.stage == 'pause':
            key('space')
            paused = True
            time.sleep(.3)
            capture('paused-held')
            key('space')
            paused = False
            time.sleep(.3)
        else:
            key('r' if args.stage == 'rotation' else 'c')
            time.sleep(.25)
        capture('interrupted')
        if args.stage == 'rotation':
            for _ in range(3):
                key('r')
                time.sleep(.1)
            capture('portrait-restored')
        key('F6')
        time.sleep(.65)
        capture('fresh-pinch-during')
        time.sleep(.5)
        capture('fresh-pinch-after')
    finally:
        # No disposal/kill: the caller retains the worker receipt and controls
        # the bounded session. Keep cleanup failures visible independently.
        errors = []
        cleanup = [['xdotool', 'windowfocus', '--sync', str(args.window)]]
        if paused:
            cleanup.append(['xdotool', 'key', '--window', str(args.window), 'space'])
        cleanup.append(['xdotool', 'key', '--window', str(args.window), 'c'])
        for command in cleanup:
            try:
                call(command)
            except Exception as error:
                errors.append(str(error))
        if errors:
            (args.output_dir / 'cleanup-errors.json').write_text(json.dumps(errors) + '\n')
            if sys.exc_info()[0] is None:
                raise RuntimeError('; '.join(errors))


if __name__ == '__main__':
    main()
