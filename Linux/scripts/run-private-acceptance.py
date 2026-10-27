#!/usr/bin/env python3
"""Plan or execute one private native import/Notes session; no visual assertions."""
import argparse
import hashlib
import json
import os
import pathlib
import shlex
import signal
import subprocess
import time

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--mode', choices=('prepare', 'write', 'read'), required=True)
for name in ('binary', 'base', 'state', 'output-dir'):
    p.add_argument('--' + name, type=pathlib.Path, required=True)
for name in ('engine', 'rom', 'package', 'import-source'):
    p.add_argument('--' + name, type=pathlib.Path)
p.add_argument('--source-sha256')
p.add_argument('--marker', default='LightTouch Linux persistence 2026-10-14')
p.add_argument('--reopen', nargs=2, type=float, help='Notes row coordinates measured from saved-list pixels')
p.add_argument('--previous-write', type=pathlib.Path, help='read mode links actual prior clean write receipt')
p.add_argument('--recover', action='store_true', help='explicit dirty recovery for first write only')
p.add_argument('--no-audio', action='store_true')
p.add_argument('--execute', action='store_true', help='otherwise only generate private scripts/commands')
a = p.parse_args()
repo = pathlib.Path(__file__).resolve().parents[2]
for name in ('binary', 'base', 'state', 'output_dir', 'engine', 'rom', 'package', 'import_source'):
    value = getattr(a, name)
    if value:
        value = value.resolve()
        setattr(a, name, value)
        if name in ('base', 'state', 'output_dir') and (value == repo or repo in value.parents):
            p.error('bases, state and evidence must be outside the source repository')
if a.mode == 'prepare' and not a.import_source:
    p.error('prepare mode requires --import-source; this imports already prepared assets, not firmware preparation')
if a.mode != 'prepare' and not a.engine:
    p.error('session modes require --engine')
if a.mode == 'read' and a.recover:
    p.error('read verification deliberately refuses --recover after the claimed clean shutdown')
if '\n' in a.marker or len(a.marker.encode()) >= 1024:
    p.error('marker must be one bounded UTF-8 script argument')
if a.reopen and not all(0 <= x <= 1 for x in a.reopen):
    p.error('reopen coordinates must be normalized 0..1')
prior = None
if a.previous_write:
    if a.mode != 'read':
        p.error('--previous-write is only for a fresh-worker read')
    previous = a.previous_write.resolve()
    previous_command = json.loads((previous / 'command.json').read_text())
    previous_inputs = json.loads((previous / 'inputs.json').read_text())
    if previous_inputs['mode'] != 'write':
        p.error('previous receipt must be from a write attempt')
    if previous_inputs['marker'] != a.marker:
        p.error('fresh read must name the same expected marker as its linked write')
    prior = json.loads((previous / 'result.json').read_text())
    if not prior.get('confirmed_clean_receipt') or prior.get('exit_code') != 0 or prior['state_after']['dirty']:
        p.error('previous write has no actual confirmed clean exit')
    for flag, value in (('--base', a.base), ('--state', a.state)):
        if flag not in previous_command or previous_command[previous_command.index(flag) + 1] != str(value):
            p.error('previous write must use exactly the same immutable base and writable state')
a.output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)

def dump(name, value):
    (a.output_dir / name).write_text(json.dumps(value, indent=2) + '\n')

def sha(path):
    if not path or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def state():
    pin = a.state / '.base-identity'
    lease = a.state / '.lease'
    return {'dirty': (a.state / '.dirty').exists(), 'base_identity': pin.read_text() if pin.exists() else None,
            'lease_inode': lease.stat().st_ino if lease.exists() else None}

command = [str(a.binary)]
if a.mode == 'prepare':
    command += ['--import', str(a.import_source), '--base', str(a.base)]
    if a.source_sha256:
        command += ['--sha256', a.source_sha256]
    if a.import_source.is_file() and not a.source_sha256:
        p.error('archive import requires --source-sha256')
else:
    lines = []
    def action(t, text):
        lines.append(f'{t:.2f} {text}')
    def tap(t, x, y):
        action(t, f'touch 7 0 {x} {y}')
        action(t + .15, f'touch 7 2 {x} {y}')
    def shot(t, name):
        action(t, f'screenshot {a.output_dir / (name + ".bmp")}')
    # Exact verified n72 bootstrap, with one-second measured slider traversal.
    action(45, 'button 0 1')
    action(45.15, 'button 0 0')
    shot(47, 'awake-lock')
    action(48, 'touch 7 0 .18 .9')
    for i in range(1, 10):
        action(48 + i * .1, f'touch 7 1 {.18 + i * .074:.3f} .9')
    action(49, 'touch 7 2 .92 .9')
    shot(54, 'home')
    tap(55, .375, .5)  # Notes icon in the verified baseline Home arrangement.
    action(59, 'guest frontmost')
    shot(60, 'notes-open')
    if a.mode == 'write':
        tap(64, .94, .09)  # Corrected create control, evidenced by linux-notes-02.
        tap(66, .5, .2)
        action(70, 'guest type ' + a.marker)
        shot(74, 'typed-marker')
        tap(75, .1, .065)
    elif a.reopen:
        tap(64, *a.reopen)
    shot(77, 'retained-notes')
    action(78, 'guest sync')
    action(79, 'guest frontmost')
    shot(80, 'saved-or-reopened')
    action(82, 'stop')
    script = a.output_dir / 'input.txt'
    script.write_text('\n'.join(lines) + '\n')
    command += ['--base', str(a.base), '--state', str(a.state), '--engine', str(a.engine),
                '--headless', '--seconds', '82', '--script', str(script),
                '--screenshot', str(a.output_dir / 'final.bmp')]
    for flag, value in (('--rom', a.rom), ('--package', a.package)):
        if value:
            command += [flag, str(value)]
    if a.recover:
        command += ['--recover']
    if a.no_audio:
        command += ['--no-audio']
dump('command.json', command)
(a.output_dir / 'command.txt').write_text(shlex.join(command) + '\n')
dump('inputs.json', {'mode': a.mode, 'marker': a.marker, 'state_before': state(),
                    'evidence_class': 'planned_prepared_import' if a.mode == 'prepare' else 'planned_native_guest_session',
                    'previous_write': str(a.previous_write.resolve()) if a.previous_write else None,
                    'sha256': {str(x): sha(x) for x in (a.binary, a.engine, a.rom, a.package,
                                                       a.base / 'device.lock.json') if x}})
if not a.execute:
    print('PLAN only; no guest/import/state writes. Review ' + str(a.output_dir))
    raise SystemExit(0)
env = dict(os.environ, EGL_PLATFORM='surfaceless', LIBGL_ALWAYS_SOFTWARE='1')
dump('environment.json', {k: env[k] for k in ('EGL_PLATFORM', 'LIBGL_ALWAYS_SOFTWARE')})
start = time.monotonic()
workers = {}
timed_out = False
last_sample = 0
with (a.output_dir / 'session.log').open('wb') as log, (a.output_dir / 'samples.jsonl').open('w') as samples:
    proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True)
    while proc.poll() is None:
        children = pathlib.Path(f'/proc/{proc.pid}/task/{proc.pid}/children')
        try:
            for child in children.read_text().split():
                if child not in workers:
                    raw = pathlib.Path(f'/proc/{child}/cmdline').read_bytes()
                    argv = raw.decode(errors='replace').split('\0')[:-1]
                    if '--worker' not in argv:
                        continue
                    stat = pathlib.Path(f'/proc/{child}/stat').read_text().rpartition(')')[2].split()
                    workers[child] = {'argv': argv,
                                      'start_ticks': stat[19], 'state': state()}
        except (FileNotFoundError, ProcessLookupError):
            pass
        if time.monotonic() - last_sample >= 1:
            for child in workers:
                try:
                    proc_dir = pathlib.Path('/proc') / child
                    fields = (proc_dir / 'stat').read_text().rpartition(')')[2].split()
                    # Check start ticks so a recycled PID never becomes this worker.
                    if fields[19] != workers[child]['start_ticks']:
                        continue
                    sample = {'observed_monotonic_ns': time.monotonic_ns(), 'pid': int(child),
                              'start_ticks': fields[19], 'cpu_user_ticks': int(fields[11]),
                              'cpu_system_ticks': int(fields[12]), 'clock_ticks_per_second': os.sysconf('SC_CLK_TCK'),
                              'rss_bytes': int(fields[21]) * os.sysconf('SC_PAGE_SIZE'), 'threads': int(fields[17])}
                    try:
                        pss = next(line for line in (proc_dir / 'smaps_rollup').read_text().splitlines()
                                   if line.startswith('Pss:'))
                        sample['pss_bytes'] = int(pss.split()[1]) * 1024
                    except (OSError, StopIteration):
                        sample['pss_bytes'] = None
                    samples.write(json.dumps(sample) + '\n')
                    samples.flush()
                except (OSError, IndexError, ValueError):
                    continue
            last_sample = time.monotonic()
        if time.monotonic() - start > 190:
            timed_out = True
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            break
        time.sleep(.25)
text = (a.output_dir / 'session.log').read_text(errors='replace')
after = state()
clean_receipt = any('state=0 confirmed=1 dirty=0' in line for line in text.splitlines())
fresh_worker = None
if prior:
    old_ids = {(str(pid), item['start_ticks']) for pid, item in prior['workers'].items()}
    new_ids = {(str(pid), item['start_ticks']) for pid, item in workers.items()}
    fresh_worker = bool(new_ids) and not bool(old_ids & new_ids)
dump('result.json', {'frontend_pid': proc.pid, 'workers': workers, 'exit_code': proc.returncode,
                     'elapsed_seconds': time.monotonic() - start, 'driver_timeout': timed_out,
                     'state_after': after, 'confirmed_clean_receipt': clean_receipt,
                     'evidence_class': 'prepared_import_attempt' if a.mode == 'prepare' else 'native_guest_attempt',
                     'same_state_fresh_worker_observed': fresh_worker,
                     'guest_visual_acceptance': 'requires integrator inspection; never inferred from host probes',
                     'visual_text_verified': False, 'visual_review_required': True})
print('Result recorded. Inspect actual Notes pixels; driver never asserts marker persistence.')
ok = proc.returncode == 0 and not timed_out
if a.mode != 'prepare':
    ok = ok and clean_receipt and not after['dirty'] and bool(workers)
    if prior:
        ok = ok and fresh_worker
raise SystemExit(0 if ok else 1)
