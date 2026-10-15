#!/usr/bin/env python3
"""Bounded n72 Lock→Home witness against explicitly vetted retained references.

This records template matches, NEVER a model inspection of unseen live pixels.
Only Lock anchors and the existing Home clock exception are admitted. Notes
remains an operator-inspected scene. Root must view actual retained live images
before final acceptance. No engine, clock, session, or recovery is started here.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import struct
import subprocess
import time


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


GATES = module('template_visual_gates', Path(__file__).with_name('watch-visual-gates.py'))
LOCK = module('template_lock', Path(__file__).with_name('lock-continuity.py'))
POLICY = 'root-vetted-n72-lock-home-v1'


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def references(path, expected_sha):
    require(path.stat().st_size <= 16384 and sha(path) == expected_sha, 'reference manifest pin')
    spec = json.loads(path.read_text())
    require(spec['schema'] == 1 and spec['vetted_by'] == 'Root' and
            spec['actual_reference_images_personally_viewed'] is True and
            bool(spec['attestation']), 'actual reference vetting required')
    values = {}
    for scene in ('lock', 'home'):
        desc = spec[scene]
        root = Path(desc['source_run']).resolve()
        receipt, data = GATES.read_capture(root, desc)
        LOCK.bitmap(data)
        require(int(receipt['sequence']) > 1 and int(receipt['sleeping']) == 0 and
                int(receipt['agent']) == 1 and int(receipt['backlight']) > 0,
                'reference was not an awake capture')
        values[scene] = data
    return values


def valid_current(receipt, status, identity, checked, *, lock=False):
    """Fresh coherent COPY; static source permitted for Home, not wake Lock."""
    try:
        captured = int(receipt['observed_monotonic_ns'])
        source = int(receipt['frame_monotonic_ns'])
        return (GATES.coherent_copy(receipt, identity['hello_monotonic_ns'], checked) and
                0 < source <= captured <= int(receipt['saved_monotonic_ns']) <= checked and
                int(receipt['sequence']) > 1 and
                all(int(x['sleeping']) == 0 and int(x['agent']) == 1 and
                    int(x['backlight']) > 0 and
                    0 <= checked - int(x['diagnostics_monotonic_ns'] if x is receipt else
                                       x['observed_monotonic_ns']) <= 1_000_000_000
                    for x in (receipt, status)) and int(status['state']) == 2 and
                (not lock or checked - source <= 2_000_000_000))
    except (KeyError, ValueError, TypeError):
        return False


def validate_home(gate, lock_receipt, home_receipt, lock_bmp, home_bmp,
                  refs, identity, worker, boot, deadline, now):
    """Production gate check; no caller boolean substitutes for actual pixels."""
    try:
        checked = int(gate['verified_monotonic_ns'])
        released = int(gate['slider_released_monotonic_ns'])
        down_begin = int(gate['slider_down_begin_monotonic_ns'])
        down_end = int(gate['slider_down_end_monotonic_ns'])
        up_begin = int(gate['slider_up_begin_monotonic_ns'])
        up_end = int(gate['slider_up_end_monotonic_ns'])
        lock_checked = int(gate['lock_verified_monotonic_ns'])
        return (gate['proof_kind'] == POLICY and gate['model_inspected'] is False and
                gate['template_matched'] is True and gate['scene'] == 'Home Screen' and
                gate['current_images_require_Root_review'] is True and
                all(gate[k] == identity[k] for k in identity) and
                gate['host_boot_id'] == boot and
                gate['worker_pid'] == worker['pid'] and
                gate['worker_start_ticks'] == worker['start_ticks'] and
                gate['worker_pid'] != identity['frontend_pid'] and
                isinstance(gate['window'], int) and gate['window'] > 0 and
                gate['viewport'] == gate['lock_viewport'] and len(gate['viewport']) == 2 and
                all(type(v) is int and 0 < v <= 16384 for v in gate['viewport']) and
                gate['slider_balanced_contact'] is True and
                identity['hello_monotonic_ns'] <= lock_checked <= down_begin <= down_end <
                up_begin <= up_end <= released <= int(home_receipt['copy_started_monotonic_ns']) and
                down_end <= int(home_receipt['frame_monotonic_ns']) <= checked <= deadline and checked <= now and
                valid_current(lock_receipt, gate['lock_status'], identity, lock_checked, lock=True) and
                valid_current(home_receipt, gate['status'], identity, checked) and
                GATES.valid_scene_transition(lock_receipt, home_receipt, lock_bmp, home_bmp) and
                LOCK.compare(refs['lock'], lock_bmp)['matched'] and
                GATES.image_continuity(refs['home'], home_bmp, 'Home Screen')['matched'] and
                refs['home'][57::4] == home_bmp[57::4])
    except (KeyError, TypeError, ValueError):
        return False


def load_home_gate(root, gate, identity, deadline, now):
    """Only an input-pinned reference manifest can authorize this proof type."""
    try:
        cfg = json.loads((root / 'inputs.json').read_text())
        path = Path(cfg['template_references_path']).resolve()
        require(str(path) == gate['reference_manifest_path'] and
                cfg['template_references_sha256'] == gate['reference_manifest_sha256'], 'manifest changed')
        refs = references(path, cfg['template_references_sha256'])
        lock_receipt, lock_bmp = GATES.read_capture(root, gate['lock_capture'])
        home_receipt, home_bmp = GATES.read_capture(root, gate)
        worker = json.loads((root / 'worker.json').read_text())
        started = json.loads((root / 'started.json').read_text())
        require(gate['window'] == json.loads((root / 'window.json').read_text())['window'],
                'recorded window changed')
        return validate_home(gate, lock_receipt, home_receipt, lock_bmp, home_bmp,
                             refs, identity, worker, started['boot_id'], deadline, now)
    except (OSError, KeyError, TypeError, ValueError, struct.error):
        return False


class Live:
    """One screenshot writer; every action rechecks owned process+window."""
    def __init__(self, root, out, identity, deadline):
        self.root, self.out, self.identity, self.deadline = root, out, identity, deadline
        self.started = json.loads((root / 'started.json').read_text())
        self.worker = json.loads((root / 'worker.json').read_text())
        self.window = int(json.loads((root / 'window.json').read_text())['window'])
        self.env = dict(os.environ, DISPLAY=self.started['display'], XAUTHORITY=self.started['xauthority'])
        self.fd = os.pidfd_open(identity['frontend_pid'])
        self.poll = select.poll()
        self.poll.register(self.fd, select.POLLIN)
        self.count = 0

    def owner(self):
        require(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == self.started['boot_id'], 'boot changed')
        require(not self.poll.poll(0), 'frontend retired')
        command = json.loads((self.root / 'command.json').read_text())['argv']
        for pid, ticks, argv, parent in ((self.identity['frontend_pid'], self.identity['start_ticks'], command, None),
                                        (self.worker['pid'], self.worker['start_ticks'], self.worker['argv'],
                                         self.identity['frontend_pid'])):
            proc = Path('/proc') / str(pid)
            stat = proc.joinpath('stat').read_text().rpartition(')')[2].split()
            require(proc.stat().st_uid == self.started['uid'] == os.geteuid() and stat[0] != 'Z' and
                    stat[19] == ticks and (parent is None or int(stat[1]) == parent) and
                    proc.joinpath('cmdline').read_bytes().decode().split('\0')[:-1] == argv, 'owner changed')
        require('--worker' in self.worker['argv'], 'worker argv')

    def x(self, *args, release=False, query=False):
        self.owner()
        if not release and not query:
            self.viewport()
        if args[0] == 'mousedown' and getattr(self, 'before_down', None):
            self.before_down()  # After final owner/focus queries, immediately before the real down.
        remaining = (self.deadline - time.monotonic_ns()) / 1e9
        require(release or remaining > 0, 'original deadline elapsed')
        start = time.monotonic_ns()
        value = subprocess.run(['xdotool', *args], env=self.env, text=True, capture_output=True,
                               timeout=2 if release else min(2, remaining), check=True)
        self.last_event = dict(start_monotonic_ns=start, end_monotonic_ns=time.monotonic_ns(),
                               argv=['xdotool', *args], stdout=value.stdout)
        with (self.out / 'controls.jsonl').open('a') as log:
            log.write(json.dumps(self.last_event) + '\n')
        return value.stdout.strip()

    def viewport(self):
        require(self.x('getwindowpid', str(self.window), query=True) == str(self.identity['frontend_pid']) and
                self.x('getwindowfocus', query=True) == str(self.window), 'window/focus changed')
        geo = dict(x.split('=', 1) for x in self.x('getwindowgeometry', '--shell', str(self.window), query=True).splitlines())
        value = [int(geo['WIDTH']), int(geo['HEIGHT'])]
        require(all(0 < x <= 16384 for x in value), 'viewport bounds')
        return value

    def status(self):
        rows = (self.root / 'session.log').read_bytes().split(b'\n')[:-1]
        row = next(x for x in reversed(rows) if x.startswith(b'status '))
        return dict(re.findall(r'(\w+)=([^ ]+)', row.decode()))

    def capture(self):
        self.count += 1
        target = self.out / ('capture-%02d' % self.count)
        target.mkdir()
        offset = (self.root / 'session.log').stat().st_size
        trigger = time.monotonic_ns()
        self.x('key', 's')
        end = min(self.deadline, trigger + 2_000_000_000)
        while time.monotonic_ns() < end:
            with (self.root / 'session.log').open('rb') as log:
                log.seek(offset)
                rows = log.read().split(b'\n')[:-1]
            for row in rows:
                if row.startswith(b'screenshot_retry '):
                    (target / 'retry.txt').write_bytes(row)
                    return None
                if not row.startswith(b'screenshot sequence='):
                    continue
                line = row.decode()
                receipt = dict(re.findall(r'(\w+)=([^ ]+)', line))
                receipt['path'] = line.split(' path=', 1)[1]
                if (int(receipt['observed_monotonic_ns']) < trigger or
                        Path(receipt['path']).resolve() != (self.root / 'live.bmp').resolve()):
                    continue
                data = (self.root / 'live.bmp').read_bytes()
                LOCK.bitmap(data)
                (target / 'native.bmp').write_bytes(data)
                receipt['saved_monotonic_ns'] = time.monotonic_ns()
                (target / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
                return description(target), receipt, data
            time.sleep(.02)
        raise ValueError('no bounded coherent screenshot')

    def wait_match(self, scene, reference, limit):
        self.deadline = min(self.deadline, time.monotonic_ns() + limit)
        for _ in GATES.capture_attempts(time.monotonic_ns, time.sleep, self.deadline):
            captured = self.capture()
            if captured is None:
                continue
            desc, receipt, data = captured
            viewport, status = self.viewport(), self.status()
            checked = time.monotonic_ns()
            match = (LOCK.compare(reference, data) if scene == 'lock' else
                     GATES.image_continuity(reference, data, 'Home Screen'))
            accepted = valid_current(receipt, status, self.identity, checked, lock=scene == 'lock')
            (Path(desc['capture_directory']) / 'template-attempt.json').write_text(json.dumps(dict(
                template_matched=match['matched'], comparison=match, current_capture_valid=accepted,
                checked_monotonic_ns=checked, status=status, viewport=viewport,
                model_inspected=False, guest_acceptance=False), indent=2) + '\n')
            if match['matched'] and accepted:
                return desc, receipt, data, viewport, status, checked
        raise ValueError('template stage exhausted original/12s budget')


def slider(live, viewport):
    """Actual aspect-fit slider with release even after partial down failure."""
    ww, wh = viewport
    scale = min(ww / 320, wh / 480)
    left, top = (ww - 320 * scale) / 2, (wh - 480 * scale) / 2
    def move(x):
        require(live.viewport() == viewport, 'viewport changed during gesture')
        live.x('mousemove', '--window', str(live.window), str(round(left + x * 320 * scale)),
               str(round(top + .9 * 480 * scale)))
    move(.18)
    try:
        live.x('mousedown', '1')
        down = dict(live.last_event)
        for step in range(1, 11):
            move(.18 + .074 * step)
            time.sleep(.1)
    finally:
        live.x('mouseup', '1', release=True)
    up = dict(live.last_event)
    return dict(slider_down_begin_monotonic_ns=down['start_monotonic_ns'],
                slider_down_end_monotonic_ns=down['end_monotonic_ns'],
                slider_up_begin_monotonic_ns=up['start_monotonic_ns'],
                slider_up_end_monotonic_ns=up['end_monotonic_ns'],
                slider_released_monotonic_ns=time.monotonic_ns(), slider_balanced_contact=True)


def description(path):
    return dict(capture_directory=str(path), bmp_sha256=sha(path / 'native.bmp'),
                receipt_sha256=sha(path / 'receipt.json'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', default='template-home-01')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps(dict(plan_only=True, no_new_session=True, Home_seconds=129,
                              stage_seconds=12, capture_rate_max_hz=2, model_inspected=False,
                              Root_review_actual_images_required=True, Notes_still_operator_inspected=True)))
        return
    root = args.run.resolve()
    cfg = json.loads((root / 'inputs.json').read_text())
    require(cfg['execution_authorized'] is True, 'run authorization required')
    ref_path = Path(cfg['template_references_path']).resolve()
    refs = references(ref_path, cfg['template_references_sha256'])
    out = (root / args.output).resolve()
    require(root in out.parents, 'output outside run')
    out.mkdir(mode=0o700, exist_ok=False)
    live = None
    try:
        # The manager's wake witness owns live.bmp until its CLOSED handoff.
        # Do not start a second screenshot writer or infer a scene from flags.
        startup_wait = time.monotonic_ns() + 5_000_000_000
        while not (root / 'started.json').exists():
            require(time.monotonic_ns() < startup_wait, 'no manager started receipt within5s')
            time.sleep(.02)
        started = json.loads((root / 'started.json').read_text())
        while True:
            require(time.monotonic_ns() - started['started_monotonic_ns'] < 900_000_000_000,
                    'bounded admission wait elapsed')
            rows = (root / 'session.log').read_bytes().split(b'\n')[:-1]
            epochs = {int(x.split(b'=', 1)[1].split()[0]) for x in rows
                      if x.startswith(b'admission hello_verified_observed_monotonic_ns=')}
            require(len(epochs) <= 1, 'HELLO changed')
            if epochs:
                hello = next(iter(epochs))
                require(time.monotonic_ns() < hello + 129_000_000_000, 'original Home129 elapsed')
                handoff = root / 'wake-auto-01/result.json'
                if handoff.exists():
                    closed = json.loads(handoff.read_text())
                    require(closed['capture_writer_exited'] is True and closed['watcher_returncode'] == 0,
                            'no closed wake writer handoff')
                    worker = json.loads((root / 'worker.json').read_text())
                    require(closed['identity'] == dict(frontend_pid=started['frontend']['pid'],
                            start_ticks=started['frontend']['start_ticks'], worker_pid=worker['pid'],
                            worker_start_ticks=worker['start_ticks'],
                            window=json.loads((root / 'window.json').read_text())['window']), 'handoff owner changed')
                    break
            require(not (root / 'result.json').exists(), 'run already terminal')
            time.sleep(.02)
        identity = dict(frontend_pid=started['frontend']['pid'], start_ticks=started['frontend']['start_ticks'],
                        hello_monotonic_ns=hello)
        deadline = hello + 129_000_000_000
        live = Live(root, out, identity, deadline)
        live.viewport()
        status = live.status()
        require(int(status['state']) == 2 and int(status['sleeping']) in (0, 1) and
                0 <= time.monotonic_ns() - int(status['observed_monotonic_ns']) <= 1_000_000_000,
                'not current RUNNING status')
        if int(status['sleeping']) == 1:
            try:
                live.x('keydown', 'h')
                time.sleep(.15)
            finally:
                live.x('keyup', 'h', release=True)
        lock = live.wait_match('lock', refs['lock'], 12_000_000_000)
        lock_desc, lock_receipt, lock_bmp, viewport, lock_status, lock_checked = lock
        # No PNG conversion, external model hop, or watcher wait before gesture.
        live.deadline = min(deadline, lock_checked + 2_000_000_000)
        def final_down_admission():
            checked = time.monotonic_ns()
            current_status = live.status()
            require(valid_current(lock_receipt, current_status, identity, checked, lock=True),
                    'Lock capture/status aged before actual contact down')
            (out / 'before-down-admission.json').write_text(json.dumps(dict(
                checked_monotonic_ns=checked, status=current_status, model_inspected=False)) + '\n')
        live.before_down = final_down_admission
        edges = slider(live, viewport)
        live.before_down = None
        live.deadline = deadline
        home_desc, home_receipt, home_bmp, home_viewport, status, checked = live.wait_match(
            'home', refs['home'], 12_000_000_000)
        gate = dict(identity, **home_desc, proof_kind=POLICY, model_inspected=False,
                    template_matched=True, scene='Home Screen', current_images_require_Root_review=True,
                    reference_manifest_path=str(ref_path), reference_manifest_sha256=cfg['template_references_sha256'],
                    host_boot_id=started['boot_id'], worker_pid=live.worker['pid'],
                    worker_start_ticks=live.worker['start_ticks'], window=live.window,
                    viewport=home_viewport, lock_viewport=viewport, lock_capture=lock_desc,
                    lock_verified_monotonic_ns=lock_checked, lock_status=lock_status,
                    **edges, status=status, verified_monotonic_ns=checked,
                    guest_acceptance=False)
        require(validate_home(gate, lock_receipt, home_receipt, lock_bmp, home_bmp, refs, identity,
                              live.worker, started['boot_id'], deadline, time.monotonic_ns()), 'final proof rejected')
        live.owner()
        require(time.monotonic_ns() <= deadline and not (root / 'home-gate.json').exists(),
                'original deadline or existing gate')
        temporary = out / 'accepted-template-gate.json'
        temporary.write_text(json.dumps(gate, indent=2) + '\n')
        os.replace(temporary, root / 'home-gate.json')
        print(json.dumps(dict(template_matched=True, model_inspected=False,
                              home_capture=home_desc['capture_directory'], gate=str(root / 'home-gate.json'),
                              original_Home_deadline_ns=deadline, guest_acceptance=False)), flush=True)
    except BaseException as error:
        (out / 'failure.json').write_text(json.dumps(dict(error=str(error), model_inspected=False,
                                                       guest_acceptance=False, no_clock_reset=True)) + '\n')
        raise
    finally:
        if live is not None:
            os.close(live.fd)
        # One bounded preview after all navigation/gate work. All raw rejects stay.
        bitmaps = sorted(out.glob('capture-*/native.bmp'))
        if bitmaps:
            bmp = bitmaps[-1]
            try:
                subprocess.run(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-i', str(bmp),
                                '-threads', '1', '-frames:v', '1', str(bmp.with_suffix('.png'))], timeout=3, check=True)
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
                (out / 'preview-failure.json').write_text(json.dumps(dict(raw_retained=str(bmp))) + '\n')


if __name__ == '__main__':
    main()
