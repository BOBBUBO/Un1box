#!/usr/bin/env python3
"""One bounded native replay stage; pixels/traces qualify results, not script exit."""
import argparse
import ctypes
import ctypes.util
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--window', type=int, required=True)
    parser.add_argument('--log', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--stage', choices=['prepare', 'reopen-albums', 'reopen-grid', 'reopen-viewer', 'cross', 'cancel', 'focus', 'pause', 'rotation'], required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--video-size', default='800x1000')
    parser.add_argument('--viewport', type=int, nargs=2, metavar=('WIDTH', 'HEIGHT'))
    args = parser.parse_args()
    expected_scene = {'reopen-albums': 'Photos image viewer', 'reopen-grid': 'Photo Albums',
                      'reopen-viewer': 'Saved Photos grid'}.get(args.stage, 'Photos image viewer below maximum zoom')
    plan = {'stage': args.stage, 'window': args.window,
            'precondition': 'Current pixels inspected: ' + expected_scene,
            'prepare': 'Every reopen screen must be inspected before the next stage; F5 shrink remains unqualified',
            'acceptance_assertions': False, 'shutdown': 'Launch owner requests Escape after all stages and waits clean receipt'}
    if not args.execute:
        print(json.dumps(plan, indent=2))
        return
    if args.window <= 0:
        parser.error('positive private SDL window required')
    if args.stage.startswith('reopen-') and (not args.viewport or min(args.viewport) <= 0):
        parser.error('reopen stages require the measured portrait SDL viewport')
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

    def tap_guest(x, y):
        bitmap = (args.output_dir / 'before/native.bmp').read_bytes()
        width, height = struct.unpack_from('<ii', bitmap, 18)
        height = abs(height)
        if width <= 0 or height < width:
            raise RuntimeError('Reopen route requires a valid portrait guest frame')
        scale = min(args.viewport[0] / width, args.viewport[1] / height)
        left, top = (args.viewport[0] - width * scale) / 2, (args.viewport[1] - height * scale) / 2
        call(['xdotool', 'mousemove', '--window', str(args.window),
              str(round(left + x * width * scale)), str(round(top + y * height * scale))])
        call(['xdotool', 'mousedown', '1'])
        try:
            time.sleep(.15)
        finally:
            call(['xdotool', 'mouseup', '1'])
        time.sleep(1)

    paused = False
    try:
        capture('before')
        if args.stage == 'reopen-albums':
            # A hidden viewer bar can consume the first tap; viewer->grid->album
            # uses the next two. Inspect before admitting the separate grid stage.
            for _ in range(3):
                tap_guest(.1, .085)
            capture('albums-after')
            return
        if args.stage == 'reopen-grid':
            tap_guest(.5, .2)
            time.sleep(1)
            capture('grid-after')
            return
        if args.stage == 'reopen-viewer':
            tap_guest(.1, .2)
            time.sleep(1)
            capture('viewer-after')
            return
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
