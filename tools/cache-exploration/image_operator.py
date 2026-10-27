"""Prospective image-bound input admission; never personal scene inspection."""
import time
import struct

from exploration_common import *
import exploration_control as control

MODE = 'n72-image-bound-v2'
HOME_POLICY = 'n72-home-clock-calendar-rgb-v1'
SETTINGS_POLICY = 'n72-settings-clock-rgb-v1'
# Half-open native320x480 rectangles. Preserve Calendar rounded border,
# red header edges, white panel edges and all alpha; only glyph interiors vary.
CLOCK = (129, 1, 190, 19)
CALENDAR_WEEKDAY = (96, 36, 148, 47)
CALENDAR_DATE = (100, 52, 145, 85)


def scene_policy(scene):
    if scene not in ('home', 'settings'):
        raise RuntimeError('Only explicit Home or Settings image policy is supported')
    return HOME_POLICY if scene == 'home' else SETTINGS_POLICY


def scene_equal(first, second, scene):
    scene_policy(scene)
    if (len(first) != 54 + 320 * 480 * 4 or len(first) != len(second)
            or first[:54] != second[:54] or struct.unpack_from('<ii', first, 18) != (320, -480)):
        return False
    masks = [CLOCK] + ([CALENDAR_WEEKDAY, CALENDAR_DATE] if scene == 'home' else [])
    for offset, (a, b) in enumerate(zip(first[54:], second[54:])):
        if a != b:
            pixel, channel = divmod(offset, 4)
            y, x = divmod(pixel, 320)
            if channel == 3 or not any(left <= x < right and top <= y < bottom
                                      for left, top, right, bottom in masks):
                return False
    return True


def enabled(cfg):
    value = cfg.get('imageBoundOperator')
    if value is None:
        return False
    if (set(value) != {'mode', 'references'} or value['mode'] != MODE
            or set(value['references']) != {'home', 'settings'}
            or cfg.get('matchedDiagnostic', {}).get('mode') != 'n72-shutdown-50-105-180-v2'):
        raise RuntimeError('Exact image-bound operator and matched v2 schedule required')
    return True


def prepare_reference(directory):
    directory = Path(directory).resolve(strict=True)
    reference = {'directory': str(directory)}
    for name in ['native.bmp', 'native.png', 'receipt.json']:
        reference[name] = {'path': str(directory / name), 'sha256': digest(directory / name)}
    validate_reference(reference)
    return reference


def validate_reference(reference):
    directory = Path(reference['directory'])
    for name in ['native.bmp', 'native.png', 'receipt.json']:
        item = reference[name]
        if item['path'] != str(directory / name) or digest(item['path']) != item['sha256']:
            raise RuntimeError('Image-bound reference changed')
    receipt = read(directory / 'receipt.json')
    if digest(directory / 'native.bmp') != receipt['sha256']:
        raise RuntimeError('Reference bitmap/receipt mismatch')
    if not coherent_current(receipt, int(receipt['saved_monotonic_ns']),
                            {'pid': receipt['frontend_pid']},
                            {'pid': receipt['worker_pid'], 'start_ticks': str(receipt['worker_start'])}):
        raise RuntimeError('Reference was not an awake coherent original capture')
    return (directory / 'native.bmp').read_bytes()


def validate_admission(root, scene):
    """Recheck exact retained image/reference/receipt binding, including offline."""
    record = read(root / (scene + '-admitted.json'))
    if (record.get('scope') != 'AUTOMATIC_IMAGE_BOUND_ADMISSION_ONLY'
            or record.get('model_inspected') is not False or record.get('scene_qualified') is not False
            or record.get('scene') != scene or record.get('policy') != scene_policy(scene)):
        raise RuntimeError('Automatic admission is not a personal inspection')
    directory = root / record['capture']
    if directory.parent != root:
        raise RuntimeError('Admission capture escapes run')
    if digest(directory / 'receipt.json') != record['receipt_sha256']:
        raise RuntimeError('Admitted receipt changed')
    receipt = read(directory / 'receipt.json')
    if (digest(directory / 'native.bmp') != receipt['sha256']
            or receipt['sha256'] != record['bitmap_sha256']
            or digest(directory / 'native.png') != record['png_sha256']):
        raise RuntimeError('Admitted image or receipt changed')
    if not coherent_current(receipt, record['admitted_monotonic_ns'],
                            record['frontend'], record['worker']):
        raise RuntimeError('Admitted image was not current coherent/awake')
    if not scene_equal(validate_reference(record['reference']),
                       (directory / 'native.bmp').read_bytes(), scene):
        raise RuntimeError('Image-bound admission comparison changed')
    return record


class Operator:
    def __init__(self, cfg):
        self.active = enabled(cfg)
        self.cfg = cfg
        self.home_sent = False
        self.settings_sent = False
        self.next_poll = 0
        self.count = 0

    def admit(self, root, scene, hello):
        started, worker = control.owner()
        self.count += 1
        directory, receipt = control.capture('image-%s-%03d' % (scene, self.count), convert=False)
        now = time.monotonic_ns()
        if not coherent_current(receipt, now, started['frontend'], worker):
            raise RuntimeError('Automatic capture not coherent/awake/current-owner')
        reference = self.cfg['imageBoundOperator']['references'][scene]
        matched = scene_equal(validate_reference(reference), (directory / 'native.bmp').read_bytes(), scene)
        save(directory / 'image-comparison.json', {'matched': matched, 'reference': reference,
             'policy': scene_policy(scene), 'observed_monotonic_ns': now,
             'model_inspected': False, 'scene_qualified': False})
        # Retain PNGs even for mismatches. Conversion does not renew source age.
        control.convert_bitmap(directory / 'native.bmp', directory / 'native.png')
        if not matched:
            return False
        now = time.monotonic_ns()
        deadline = hello + (95 if scene == 'home' else 120) * SECOND
        if now > deadline or not coherent_current(receipt, now, started['frontend'], worker):
            raise RuntimeError('Automatic image admission expired after conversion')
        after, after_worker = control.owner()
        if after['frontend'] != started['frontend'] or after_worker != worker:
            raise RuntimeError('Automatic image owner changed')
        record = {'scope': 'AUTOMATIC_IMAGE_BOUND_ADMISSION_ONLY', 'scene': scene,
                  'model_inspected': False, 'scene_qualified': False,
                  'admitted_monotonic_ns': now, 'hello_ns': hello,
                  'capture': directory.name, 'bitmap_sha256': receipt['sha256'],
                  'png_sha256': digest(directory / 'native.png'),
                  'receipt_sha256': digest(directory / 'receipt.json'),
                  'reference': reference, 'frontend': started['frontend'], 'worker': worker,
                  'policy': scene_policy(scene)}
        save(root / (scene + '-admitted.json'), record)
        return True

    def tick(self, root, hello, now):
        if not self.active or hello is None:
            return
        age = now - hello
        if not self.home_sent and age >= 50 * SECOND:
            self.home_sent = True  # Every failed input remains a consumed attempt.
            control.home_unlock('timed-lock-unlock', matched=True)
        if not self.home_sent:
            return
        if age >= 95 * SECOND and not (root / 'home-admitted.json').is_file():
            raise control.matched_trial.SceneDeadlineFailure('home')
        if age >= 120 * SECOND and not (root / 'settings-admitted.json').is_file():
            raise control.matched_trial.SceneDeadlineFailure('settings')
        if 55 * SECOND <= age < 95 * SECOND and not (root / 'home-admitted.json').exists() and now >= self.next_poll:
            self.admit(root, 'home', hello)
            self.next_poll = time.monotonic_ns() + SECOND
        if not self.settings_sent and age >= 105 * SECOND:
            self.settings_sent = True
            home = validate_admission(root, 'home')
            # Fresh exact continuity protects the actual Settings input. This
            # is machine image admission, not an assertion of personal viewing.
            gate = control.continuity(home['capture'], 'home', 'timed-settings-gate', inspected=False)
            geometry = control.geometry()
            if time.monotonic_ns() - gate['verification_monotonic_ns'] > SECOND:
                raise RuntimeError('Automatic Settings input admission expired')
            gate.update(native_pixel=[44, 326], geometry=geometry, before_worker=control.owner()[1])
            with control.capture_lock():
                control.move(44, 326, geometry)
                gate['input_begin_ns'] = time.monotonic_ns()
                control.matched_trial.input_claim(True, root, hello, 'tap-settings', gate['input_begin_ns'], [44, 326])
                try:
                    control.xcall(['mousedown', '1']); time.sleep(.15)
                finally:
                    control.xcall(['mouseup', '1'], release=True)
                gate['input_end_ns'] = time.monotonic_ns()
            save(root / 'settings-input.json', gate)
            self.next_poll = 0
        if 108 * SECOND <= age < 120 * SECOND and self.settings_sent and not (root / 'settings-admitted.json').exists() and now >= self.next_poll:
            self.admit(root, 'settings', hello)
            self.next_poll = time.monotonic_ns() + SECOND
