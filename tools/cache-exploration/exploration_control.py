#!/usr/bin/env python3
"""Root-only owner-checked exploration controls; scenes require actual inspection."""
import argparse
import importlib.util
import struct
import time

from exploration_common import *

def module(path):
    spec = importlib.util.spec_from_file_location('lock_continuity', path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value

def unchanged_except_clock(first, second):
    # Existing Home policy: only clock RGB129,1..190,19; all alpha protected.
    if len(first) != 54 + 320 * 480 * 4 or len(first) != len(second) or first[:54] != second[:54]:
        return False
    width, height = struct.unpack_from('<ii', first, 18)
    if (width, height) != (320, -480):
        return False
    for offset, (a, b) in enumerate(zip(first[54:], second[54:])):
        pixel, channel = divmod(offset, 4)
        y, x = divmod(pixel, 320)
        if a != b and not (channel < 3 and 129 <= x < 190 and 1 <= y < 19):
            return False
    return True

def continuity(source, scene, label):
    original = R / source
    if original.parent != R or not (original / 'receipt.json').is_file():
        raise RuntimeError('Known private capture directory required')
    old = read(original / 'receipt.json')
    if digest(original / 'native.bmp') != old['sha256']:
        raise RuntimeError('Inspected capture changed')
    if not fresh(old, int(old['saved_monotonic_ns'])):
        raise RuntimeError('Original inspected capture was not awake/fresh')
    inspected = time.monotonic_ns()
    if inspected - int(old['saved_monotonic_ns']) > 60 * SECOND:
        raise RuntimeError('Original image too old for current scene admission')
    current, receipt = capture(label, convert=False)
    if not fresh(receipt, time.monotonic_ns()):
        raise RuntimeError('Current source/diagnostics not fresh/awake')
    same = old['worker_pid'] == receipt['worker_pid'] and old['worker_start'] == receipt['worker_start']
    if scene == 'lock':
        helper = read(P / 'EXPLORATION-INPUTS.json')['lockPolicy']
        comparison = module(helper).compare((original / 'native.bmp').read_bytes(), (current / 'native.bmp').read_bytes())
        matched = comparison['matched']
    else:
        matched = unchanged_except_clock((original / 'native.bmp').read_bytes(), (current / 'native.bmp').read_bytes())
        comparison = {'policy': 'exact-except-status-clock-RGB-all-alpha-protected', 'matched': matched}
    record = {'scene': scene, 'model_inspected': True, 'inspected_monotonic_ns': inspected,
              'inspection_semantics': 'Caller explicitly states actual view_image inspection completed before this command; recorded now, no backdating',
              'original_capture': source, 'current_capture': current.name, 'comparison': comparison,
              'same_worker': same, 'original_sha256': old['sha256'], 'current_sha256': receipt['sha256'],
              'verification_monotonic_ns': time.monotonic_ns()}
    save(current / 'continuity.json', record)
    if not same or not matched:
        raise RuntimeError('Inspected scene changed; retained captures require fresh actual inspection')
    return record

def geometry():
    window = str(read(R / 'window.json')['window'])
    result, _, _ = xcall(['getwindowgeometry', '--shell', window])
    values = dict(row.split('=', 1) for row in result.splitlines())
    width, height = int(values['WIDTH']), int(values['HEIGHT'])
    return {'window': int(window), 'width': width, 'height': height,
            'viewport': viewport(width, height), 'rotation': 0}

def move(x, y, g):
    v = g['viewport']
    return xcall(['mousemove', '--window', str(g['window']),
                  str(round(v['left'] + x / 320 * v['width'])),
                  str(round(v['top'] + y / 480 * v['height']))])

def press_key(key):
    try:
        xcall(['keydown', key]); time.sleep(.15)
    finally:
        xcall(['keyup', key], release=True)

def lock_reference():
    """Validate a pinned, previously personally viewed Lock; never label a scene."""
    cfg = read(P / 'EXPLORATION-INPUTS.json')
    reference = cfg.get('lockReference')
    if not reference or reference.get('scope') != 'PERSONALLY_INSPECTED_N72_LOCK_REFERENCE':
        raise RuntimeError('Prepared personally inspected Lock reference required')
    original = Path(reference['captureDirectory'])
    for name, filename in [('bmp', 'native.bmp'), ('png', 'native.png'), ('receipt', 'receipt.json')]:
        if digest(original / filename) != reference[name + 'SHA256']:
            raise RuntimeError('Pinned Lock reference changed: ' + name)
    receipt = read(original / 'receipt.json')
    if receipt['sha256'] != reference['bmpSHA256'] or not fresh(receipt, int(receipt['saved_monotonic_ns'])):
        raise RuntimeError('Reference was not a fresh awake original capture')
    policy = cfg['pins']['lockPolicy']
    if digest(policy['path']) != policy['sha256'] or policy['path'] != cfg['lockPolicy']:
        raise RuntimeError('Pinned Lock policy changed')
    return original, reference, module(policy['path'])

def home_unlock(label):
    """One Home, immediate exact-anchor admission and slider; no model roundtrip."""
    if re.fullmatch('[a-z0-9-]{1,54}', label) is None:
        raise RuntimeError('Bounded private home-unlock label required')
    original, reference, policy = lock_reference()
    _, worker = owner()
    hello = int(read(R / 'hello.json')['monotonic_ns'])
    started = time.monotonic_ns()
    deadline = min(started + 8 * SECOND, hello + 129 * SECOND)
    if deadline - started < 4 * SECOND:
        raise RuntimeError('Original Home129s deadline leaves insufficient bounded input time')
    record = {'scope': 'PINNED_LOCK_CONTINUITY_INPUT_ONLY', 'reference': reference,
              'original_capture': str(original), 'worker': worker, 'begin_ns': started,
              'input_deadline_ns': deadline, 'home_deadline_ns': hello + 129 * SECOND,
              'guest_acceptance': False, 'home_scene_asserted': False, 'slider_started': False}
    current = post = None
    try:
        g = geometry()
        latest = fields(next(row for row in reversed(rows(R / 'session.log')) if row.startswith('status ')))
        now = time.monotonic_ns()
        if int(latest['state']) != 2 or not 0 <= now - int(latest['observed_monotonic_ns']) <= SECOND:
            raise RuntimeError('Fresh RUNNING status required before Home')
        if now >= deadline:
            raise RuntimeError('Bounded Home input deadline expired before input')
        record['pre_home_status'] = latest
        press_key('h')
        record['home_released_ns'] = time.monotonic_ns()
        current, receipt = capture(label + '-lock', convert=False)
        record['current_capture'] = current.name
        record['current_receipt'] = receipt
        if not fresh(receipt, time.monotonic_ns()):
            raise RuntimeError('Immediate post-Home capture not fresh/awake; no slider')
        comparison = policy.compare((original / 'native.bmp').read_bytes(), (current / 'native.bmp').read_bytes())
        record['comparison'] = comparison
        save(current / 'continuity.json', record)
        if not comparison['matched']:
            raise RuntimeError('Exact inspected Earth/knob/lock-symbol anchors changed; no slider')
        if (receipt['worker_pid'], receipt['worker_start']) != (worker['pid'], worker['start_ticks']):
            raise RuntimeError('Current capture worker identity changed')
        admission = time.monotonic_ns()
        with capture_lock():
            if time.monotonic_ns() >= deadline or not fresh(receipt, time.monotonic_ns()):
                raise RuntimeError('Current Lock admission expired before slider')
            move(.18 * 320, .90 * 480, g)
            record['slider_started'] = True
            try:
                xcall(['mousedown', '1'])
                for step in range(1, 11):
                    if time.monotonic_ns() >= deadline or time.monotonic_ns() - admission > 3 * SECOND:
                        raise RuntimeError('Bounded slider deadline expired')
                    move((.18 + .074 * step) * 320, .90 * 480, g)
                    time.sleep(.08)
            finally:
                xcall(['mouseup', '1'], release=True)
                record['slider_released_ns'] = time.monotonic_ns()
        record['ordinary_balanced_slider'] = True
        record['geometry'] = g
        post, _ = capture(label + '-post', convert=False)
        record['post_capture'] = post.name
        record['requires_personal_post_input_scene_inspection'] = True
    except Exception as error:
        record['error'] = str(error)
        raise
    finally:
        record['ended_ns'] = time.monotonic_ns()
        save(R / (label + '-home-unlock.json'), record)
        # Conversion is deliberately after input. Full BMPs/receipts survive any
        # conversion error; no converter/model latency consumes awake admission.
        for path in (current, post):
            if path is not None:
                convert_bitmap(path / 'native.bmp', path / 'native.png')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['capture', 'wake', 'unlock', 'home-unlock', 'inspect-home', 'tap-settings', 'inspect-settings', 'stop'])
    parser.add_argument('--capture')
    parser.add_argument('--output')
    parser.add_argument('--x', type=int)
    parser.add_argument('--y', type=int)
    parser.add_argument('--model-inspected', action='store_true')
    args = parser.parse_args()
    owner()
    if args.operation == 'stop':
        save(R / 'operator-stop.json', {'requested_monotonic_ns': time.monotonic_ns()}); return
    label = args.output or ('operator-%d' % time.monotonic_ns())
    if args.operation == 'capture':
        path, receipt = capture(label)
        print(str(path / 'native.png')); return
    hello = read(R / 'hello.json')['monotonic_ns']
    if time.monotonic_ns() - hello >= 203 * SECOND:
        raise RuntimeError('Original205s Stop imminent; no input')
    if args.operation == 'wake':
        # Hardware wake cannot require the agent that sleeping may suspend.
        latest = next(row for row in reversed(rows(R / 'session.log')) if row.startswith('status '))
        status = fields(latest)
        if int(status['state']) != 2 or time.monotonic_ns() - int(status['observed_monotonic_ns']) > SECOND:
            raise RuntimeError('Fresh RUNNING status required for wake')
        if int(status['sleeping']) != 1:
            raise RuntimeError('No wake input: display is already awake')
        press_key('p'); return
    if args.operation == 'home-unlock':
        if not args.model_inspected:
            raise RuntimeError('Actual personal inspection of prepared Lock reference required')
        home_unlock(label)
        print('Input completed; personally inspect retained post-input PNG. No Home pass asserted.')
        return
    if not args.model_inspected or not args.capture:
        raise RuntimeError('Explicit actual image inspection and capture required')
    if args.operation == 'unlock':
        gate = continuity(args.capture, 'lock', label)
        g = geometry()
        if time.monotonic_ns() - gate['verification_monotonic_ns'] > SECOND:
            raise RuntimeError('Inspected Lock admission expired before input')
        with capture_lock():
            move(.18 * 320, .90 * 480, g)
            try:
                xcall(['mousedown', '1'])
                for step in range(1, 11):
                    move((.18 + .074 * step) * 320, .90 * 480, g); time.sleep(.1)
            finally:
                xcall(['mouseup', '1'], release=True)
        save(R / (label + '-unlock.json'), {'geometry': g, 'ordinary_balanced_slider': True,
                                          'ended_monotonic_ns': time.monotonic_ns()})
    elif args.operation == 'inspect-home':
        record = continuity(args.capture, 'home', label)
        if time.monotonic_ns() - hello >= 129 * SECOND:
            raise RuntimeError('Original Home129s deadline missed')
        record['geometry'] = geometry()
        save(R / 'home-inspected.json', record)
        print('Actual Home inspection retained; use inspected native Settings icon pixel coordinates.')
    elif args.operation == 'tap-settings':
        if not (R / 'home-inspected.json').exists():
            raise RuntimeError('Original129s Home admission required')
        if args.x is None or args.y is None or not 0 <= args.x < 320 or not 0 <= args.y < 480:
            raise RuntimeError('Personally inspected native Settings coordinates required')
        record = continuity(args.capture, 'home', label)
        g = geometry()
        if time.monotonic_ns() - record['verification_monotonic_ns'] > SECOND:
            raise RuntimeError('Inspected Home admission expired before input')
        record.update(native_pixel=[args.x, args.y], geometry=g, before_worker=owner()[1])
        with capture_lock():
            move(args.x, args.y, g)
            record['input_begin_ns'] = time.monotonic_ns()
            try:
                xcall(['mousedown', '1']); time.sleep(.15)
            finally:
                xcall(['mouseup', '1'], release=True)
            record['input_end_ns'] = time.monotonic_ns()
        save(R / 'settings-input.json', record)
    elif args.operation == 'inspect-settings':
        if not (R / 'settings-input.json').exists():
            raise RuntimeError('Settings input evidence absent')
        record = continuity(args.capture, 'settings', label)
        record['geometry'] = geometry()
        record['explicit_operator_scene'] = 'Full Settings list including complete rows'
        save(R / 'settings-inspected.json', record)
        print('Actual Settings inspection retained; controller will request ordinary Stop.')

if __name__ == '__main__':
    main()
