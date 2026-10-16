"""Opt-in fixed schedule for shutdown diagnosis; never a speedup benchmark."""
from exploration_common import SECOND, fields, read, save, digest

MODE = 'n72-shutdown-50-105-180-v2'


def enabled(cfg):
    if 'matchedDiagnostic' not in cfg:
        return False
    value = cfg['matchedDiagnostic']
    if value != {'mode': MODE} or cfg.get('cacheTrial', {}).get('mode') not in ('off', 'cold', 'reuse'):
        raise RuntimeError('Exact matched diagnostic mode with OFF/COLD/REUSE required')
    return True


def input_claim(active, root, hello, operation, now, native_pixel=None):
    """Call immediately before input; failed attempts cannot be retried."""
    if not active:
        return
    due = {'home-unlock': 50, 'tap-settings': 105}.get(operation)
    if due is None or not hello + due * SECOND <= now <= hello + (due + 1) * SECOND:
        raise RuntimeError('Scheduled input window missed; no input')
    if operation == 'tap-settings' and native_pixel != [44, 326]:
        raise RuntimeError('Scheduled Settings pixel must be44,326')
    path = root / ('matched-' + operation + '-input.json')
    # Exclusive creation before dispatch prevents a second input after failures.
    with path.open('x') as stream:
        import json
        json.dump({'operation': operation, 'observed_monotonic_ns': now,
                   'hello_ns': hello, 'native_pixel': native_pixel}, stream)


def attest(active, scene, record, hello, now, wifi_name=None):
    if not active:
        return record
    deadline = 95 if scene == 'home' else 120
    if not hello <= now <= hello + deadline * SECOND:
        raise RuntimeError('Scheduled scene inspection deadline missed')
    if scene == 'settings':
        if wifi_name != 'qemu-ios':
            raise RuntimeError('Personally viewed Settings Wi-Fi must be qemu-ios')
        record['observed_wifi_name'] = wifi_name
        record['wifi_semantics'] = 'Explicit operator report from personally viewed Settings; no automated network inference'
    return record


class Tracker:
    def __init__(self, cfg):
        self.active = enabled(cfg)
        self.hello = None
        self.seen = {}
        self.statuses = {}
        self.ready = False

    def observe(self, root, hello, now, log_rows):
        if not self.active or hello is None:
            return None
        if self.hello is not None and hello != self.hello:
            raise RuntimeError('Matched diagnostic HELLO changed')
        self.hello = hello
        age = now - hello
        # Capture each admitted scene/input receipt once; reject later rewrites.
        for filename, deadline in [('matched-home-unlock-input.json', 51),
                                   ('home-inspected.json', 95),
                                   ('settings-input.json', 106),
                                   ('settings-inspected.json', 120)]:
            path = root / filename
            if filename in self.seen and digest(path) != self.seen[filename]:
                raise RuntimeError('Matched diagnostic evidence changed')
            if age >= deadline * SECOND and filename not in self.seen:
                value = read(path)
                if filename == 'matched-home-unlock-input.json' and (
                        value.get('hello_ns') != hello or not hello + 50 * SECOND <= value['observed_monotonic_ns'] <= hello + 51 * SECOND):
                    raise RuntimeError('Actual Home input missed schedule')
                if filename == 'settings-inspected.json' and value.get('observed_wifi_name') != 'qemu-ios':
                    raise RuntimeError('Matched Settings Wi-Fi observation missing')
                if filename == 'settings-input.json' and not hello + 105 * SECOND <= value['input_begin_ns'] <= hello + 106 * SECOND:
                    raise RuntimeError('Actual Settings input missed schedule')
                self.seen[filename] = digest(path)
        if 120 * SECOND <= age <= 136 * SECOND and not self.ready:
            for row in log_rows:
                if row.startswith('status '):
                    value = fields(row)
                    timestamp = int(value['observed_monotonic_ns'])
                    if hello + 120 * SECOND <= timestamp <= hello + 135 * SECOND:
                        if (int(value['state']) != 2 or int(value['agent']) != 1
                                or int(value['shutdown_phase']) != 0
                                or int(value['sleeping']) != 0 or int(value['backlight']) <= 0):
                            raise RuntimeError('Matched readiness lost during fixed window')
                        self.statuses[timestamp] = value
            if age >= 135 * SECOND:
                times = sorted(self.statuses)
                if (not times or times[0] > hello + 121 * SECOND
                        or times[-1] < hello + 134 * SECOND
                        or any(b - a > 1500000000 for a, b in zip(times, times[1:]))):
                    raise RuntimeError('Matched readiness status coverage missing/stale')
                self.ready = True
                save(root / 'matched-readiness.json', {
                    'mode': MODE, 'hello_ns': hello, 'statuses': [self.statuses[t] for t in times],
                    'exposed_readiness_only': True,
                    'unobserved': ['all guest service states', 'storage activity before shutdown dispatch'],
                    'performance_qualified': False})
        if age > 136 * SECOND and not self.ready:
            raise RuntimeError('Original matched readiness window was missed')
        return 'matched scheduled Stop180s' if age >= 180 * SECOND else None

    def finish(self, root, hello, stop_clock):
        if not self.active:
            return None
        if (not self.ready or hello != self.hello or stop_clock is None
                or not hello + 180 * SECOND <= stop_clock <= hello + 181 * SECOND):
            raise RuntimeError('Matched trial lacks readiness or original scheduled Stop')
        if any(digest(root / name) != expected for name, expected in self.seen.items()):
            raise RuntimeError('Matched diagnostic evidence changed after admission')
        return {'mode': MODE, 'status': 'DIAGNOSTIC_SCHEDULE_COMPLETED',
                'hello_ns': hello, 'stop_ns': stop_clock, 'evidence': self.seen,
                'readiness_sha256': digest(root / 'matched-readiness.json'),
                'performance_qualified': False, 'causality_established': False}
