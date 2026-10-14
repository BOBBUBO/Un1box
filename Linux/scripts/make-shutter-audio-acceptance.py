#!/usr/bin/env python3
"""Generate bounded real guest shutter/touch actions; never runs a guest."""
import argparse
import math
import pathlib

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--output-dir', type=pathlib.Path, required=True)
p.add_argument('--start', type=float, default=60, help='time after observed boot/unlock/page load')
p.add_argument('--cycles', type=int, default=4, help='1..16 stock screenshot pulses (writes guest Photos overlay)')
p.add_argument('--interval', type=float, default=4, help='seconds between pulses, at least3')
a = p.parse_args()
repo = pathlib.Path(__file__).resolve().parents[2]
out = a.output_dir.resolve()
if repo == out or repo in out.parents:
    p.error('evidence must be outside Git')
if not 1 <= a.cycles <= 16 or not math.isfinite(a.start) or a.start < 0 or not math.isfinite(a.interval) or a.interval < 3:
    p.error('bounded cycles1..16, finite nonnegative start and interval>=3 required')
out.mkdir(mode=0o700, parents=True, exist_ok=False)
actions = []
def at(t, text):
    actions.append((t, text))
def shot(t, name):
    at(t, f'screenshot {out / (name + ".bmp")}')
at(a.start, 'guest frontmost')
shot(a.start + 1, 'before-audio')
for i in range(a.cycles):
    t = a.start + 3 + i * a.interval
    # Stock OS gesture documented in hw/arm/ipod_touch_i2s.c. This asks the
    # guest to play photoShutter.caf; it never injects host-generated PCM.
    at(t, 'touches 2 17 0 .3 .5 91 0 .7 .5')
    at(t + .1, 'button 0 1')
    at(t + .1, 'button 1 1')
    at(t + .2, 'touches 2 17 1 .25 .5 91 1 .75 .5')
    at(t + .3, 'touches 2 17 1 .2 .5 91 1 .8 .5')
    at(t + .45, 'button 1 0')
    at(t + .45, 'button 0 0')
    at(t + .75, 'touches 2 17 2 .2 .5 91 2 .8 .5')
    shot(t + .9, f'after-shutter-{i + 1:02}')
end = a.start + 3 + a.cycles * a.interval
at(end, 'guest frontmost')
shot(end + 2, 'after-audio')
at(end + 5, 'cancel')
at(end + 6, 'stop')
(out / 'input.txt').write_text('\n'.join(f'{t:.2f} {text}' for t, text in sorted(actions, key=lambda row: row[0])) + '\n')
(out / 'README.txt').write_text(
    'CANDIDATE ONLY: inspect actual boot/unlock/Safari page before these actions.\n'
    'Run without --no-audio. Do not claim sound from queued PCM or source/Mac evidence.\n'
    'Compare host-monotonic status windows for nonzero/peak/loud samples, render and touch activity; hear actual output.\n'
    'Compare guest frontmost before/after and pixels; if the chord locks/exits the app, this attempt failed.\n'
    'Each stock screenshot creates a Photos file only in writable guest overlay. No firmware/package/SDK acquisition.\n')
print(out)
