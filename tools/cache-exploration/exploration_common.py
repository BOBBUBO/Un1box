"""Bounded owner/capture/inventory helpers for one actual Linux exploration."""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import select
import signal
import stat
import subprocess
import time

P = Path(__file__).resolve().parent
R = P / 'exploration01'
SECOND = 10**9

def read(path):
    return json.loads(Path(path).read_text())

def save(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)

def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as source:
        for data in iter(lambda: source.read(1024 * 1024), b''):
            value.update(data)
    return value.hexdigest()

def json_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def tool(name):
    """Resolve an independently pinned executable without relying on PATH.

    Packet preparation records resolved real paths. The deployed manifest binds
    this small receipt, while direct source tests may provide their own receipt.
    Recheck the executable bytes before each helper invocation.
    """
    entry = read(P / 'EXPLORATION-TOOLS.json')[name]
    path = Path(entry['path'])
    if (not path.is_absolute() or path.resolve(strict=True) != path
            or not path.is_file() or not os.access(path, os.X_OK) or digest(path) != entry['sha256']):
        raise RuntimeError('Pinned helper changed: ' + name)
    return str(path)

def convert_bitmap(source, destination):
    env = dict(os.environ, OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1')
    subprocess.run([tool('ffmpeg'), '-y', '-hide_banner', '-loglevel', 'error', '-threads', '1',
                    '-i', str(source), '-frames:v', '1', str(destination)],
                   env=env, check=True, capture_output=True, timeout=3)

def inventory(root, checkpoint=lambda: None):
    root = Path(root)
    rows = {}
    for directory, directories, files in os.walk(root, followlinks=False):
        checkpoint()
        for path in [Path(directory)] + [Path(directory) / name for name in sorted(files)]:
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                row = {'type': 'directory', 'mode': stat.S_IMODE(info.st_mode)}
            elif stat.S_ISREG(info.st_mode):
                row = {'type': 'file', 'mode': stat.S_IMODE(info.st_mode),
                       'size': info.st_size, 'sha256': digest(path)}
            else:
                raise RuntimeError('Special/symlink inventory entry: ' + str(path))
            rows[str(path.relative_to(root))] = row
        for name in directories:
            if not stat.S_ISDIR((Path(directory) / name).lstat().st_mode):
                raise RuntimeError('Directory symlink refused')
    return {'root': str(root), 'entries': rows, 'inventorySHA256': json_digest(rows)}

def available():
    return next(int(row.split()[1]) * 1024 for row in Path('/proc/meminfo').read_text().splitlines()
                if row.startswith('MemAvailable:'))

def process(pid):
    try:
        root = Path('/proc') / str(pid)
        fields = (root / 'stat').read_text().rpartition(')')[2].split()
        return {'pid': int(pid), 'start_ticks': fields[19], 'ppid': int(fields[1]),
                'state': fields[0], 'uid': root.stat().st_uid,
                'cpu_ticks': int(fields[11]) + int(fields[12]),
                'rss_bytes': int(fields[21]) * os.sysconf('SC_PAGE_SIZE'),
                'argv': (root / 'cmdline').read_bytes().decode().split('\0')[:-1]}
    except (OSError, IndexError, ValueError):
        return None

def same_process(expected):
    current = process(expected['pid']) if expected else None
    return bool(current and current['start_ticks'] == expected['start_ticks'] and
                current['uid'] == expected['uid'] and current['argv'] == expected['argv'] and
                current['state'] != 'Z')

def alive(fd):
    poll = select.poll()
    poll.register(fd, select.POLLIN)
    return not poll.poll(0)

def rows(path):
    # Keep only complete lines; total logs are bounded by controller.
    return [row.decode(errors='replace') for row in Path(path).read_bytes().split(b'\n')[:-1]]

def fields(line):
    return dict(re.findall(r'(\w+)=([^ ]+)', line))

def strict_final(returncode, line):
    expected = {'status_available': '1', 'state': '0', 'confirmed': '1', 'dirty': '0',
                'worker_reaped': '1', 'worker_exit': '0', 'worker_disposal_requested': '0', 'frontend_return': '0'}
    actual = fields(line)
    return returncode == 0 and line.startswith('FINAL ') and all(actual.get(k) == v for k, v in expected.items())

@contextlib.contextmanager
def free_lease(state):
    with (Path(state) / '.lease').open('rb') as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            if (Path(state) / '.dirty').exists():
                raise RuntimeError('Dirty state refused; no recovery')
            yield
        finally:
            fcntl.flock(lease, fcntl.LOCK_UN)

@contextlib.contextmanager
def capture_lock():
    with (R / 'capture.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)

def cgroup():
    relative = next(row.split(':', 2)[2] for row in Path('/proc/self/cgroup').read_text().splitlines()
                    if row.startswith('0::'))
    root = Path('/sys/fs/cgroup') / relative.lstrip('/')
    return root

def bound_cgroup(root):
    quota, period = (root / 'cpu.max').read_text().split()
    controls = {name: (root / name).read_text().strip() for name in
                ['memory.max', 'memory.swap.max', 'cpu.max', 'pids.max']}
    if (controls['memory.max'] != str(4 * 1024**3) or controls['memory.swap.max'] != '0'
            or quota == 'max' or int(quota) / int(period) > 2 or controls['pids.max'] != '192'):
        raise RuntimeError('Exact bounded cgroup required: ' + repr(controls))
    return controls

def grant_valid(grant, boot, packet, now):
    return (grant.get('scope') == 'ONE_LINUX_N72_CANDIDATE_EXPLORATION' and grant.get('hostBootId') == boot
            and grant.get('packetSHA256') == packet
            and type(grant.get('issuedMonotonicNS')) is int and type(grant.get('expiresMonotonicNS')) is int
            and 0 < grant['expiresMonotonicNS'] - grant['issuedMonotonicNS'] <= 120 * SECOND
            and grant['issuedMonotonicNS'] <= now <= grant['expiresMonotonicNS'])

def owner():
    started = read(R / 'started.json')
    if Path('/proc/sys/kernel/random/boot_id').read_text().strip() != started['boot_id']:
        raise RuntimeError('Host boot changed')
    if not same_process(started['frontend']) or started['frontend']['uid'] != os.geteuid():
        raise RuntimeError('Frontend identity changed')
    worker = read(R / 'worker.json')
    if not same_process(worker) or worker['ppid'] != started['frontend']['pid']:
        raise RuntimeError('Worker identity changed')
    if (R / 'stop-request.json').exists():
        raise RuntimeError('Ordinary Stop already requested')
    return started, worker

def xcall(arguments, release=False):
    started, worker = owner() if not release else (read(R / 'started.json'), read(R / 'worker.json'))
    if release and not same_process(started['frontend']):
        raise RuntimeError('Release owner retired')
    env = dict(os.environ, DISPLAY=started['display'])
    env.pop('XAUTHORITY', None)
    window = str(read(R / 'window.json')['window'])
    if not release:
        for query, expected in [(['getwindowpid', window], str(started['frontend']['pid'])), (['getwindowfocus'], window)]:
            result = subprocess.run([tool('xdotool'), *query], env=env, check=True, capture_output=True, text=True, timeout=2)
            if result.stdout.strip() != expected:
                raise RuntimeError('Owning window/focus changed')
    begin = time.monotonic_ns()
    result = subprocess.run([tool('xdotool'), *arguments], env=env, check=True, capture_output=True, text=True, timeout=2)
    end = time.monotonic_ns()
    with (R / 'controls.jsonl').open('a') as log:
        log.write(json.dumps({'input_begin_ns': begin, 'input_end_ns': end,
                              'argv': [tool('xdotool'), *arguments], 'stdout': result.stdout}) + '\n')
    return result.stdout.strip(), begin, end

def capture(label, convert=True):
    if re.fullmatch('[a-z0-9-]{1,64}', label) is None:
        raise RuntimeError('Private capture label invalid')
    with capture_lock():
        started, worker = owner()
        output = R / label
        output.mkdir(mode=0o700, exist_ok=False)
        offset = (R / 'session.log').stat().st_size
        begin = time.monotonic_ns()
        xcall(['key', 's'])
        receipt = None
        while time.monotonic_ns() - begin < 2 * SECOND:
            with (R / 'session.log').open('rb') as source:
                source.seek(offset)
                lines = source.read(1024 * 1024).split(b'\n')[:-1]
            for raw in lines:
                line = raw.decode(errors='replace')
                if line.startswith('screenshot sequence='):
                    candidate = fields(line)
                    candidate['path'] = line.split(' path=', 1)[1]
                    if int(candidate['observed_monotonic_ns']) >= begin:
                        receipt = candidate
                        break
            if receipt:
                break
            time.sleep(.05)
        if not receipt:
            save(output / 'failure.json', {'reason': 'No fresh capture receipt', 'begin_ns': begin})
            raise RuntimeError('No fresh actual frame; retained failed capture ' + label)
        source = Path(receipt['path'])
        if source.resolve() != (R / 'live.bmp').resolve() or source.stat().st_size > 64 * 1024**2:
            raise RuntimeError('Capture path/byte bound changed')
        import shutil
        shutil.copyfile(source, output / 'native.bmp')
        receipt.update(saved_monotonic_ns=time.monotonic_ns(), sha256=digest(output / 'native.bmp'),
                       frontend_pid=started['frontend']['pid'], worker_pid=worker['pid'], worker_start=worker['start_ticks'])
        save(output / 'receipt.json', receipt)
        if convert:
            convert_bitmap(output / 'native.bmp', output / 'native.png')
        return output, receipt

def fresh(receipt, now):
    return (int(receipt['sleeping']) == 0 and int(receipt['agent']) == 1 and int(receipt['backlight']) > 0
            and 0 <= now - int(receipt['frame_monotonic_ns']) <= 2 * SECOND
            and 0 <= now - int(receipt['diagnostics_monotonic_ns']) <= SECOND)

def coherent_current(receipt, now, frontend, worker):
    """Current readback/diagnostics may describe a legitimately static source.

    Source production time remains unchanged and ordered, without pretending a
    static image was newly rendered. This does not recognize a guest scene.
    """
    try:
        keys = ['sequence', 'shared_generation', 'frame_monotonic_ns',
                'copy_started_monotonic_ns', 'copy_finished_monotonic_ns',
                'observed_monotonic_ns', 'saved_monotonic_ns', 'diagnostics_monotonic_ns']
        values = {}
        for key in keys:
            value = receipt[key]
            if isinstance(value, bool) or not re.fullmatch('[0-9]+', str(value)):
                return False
            values[key] = int(value)
            if not 0 < values[key] < 2**64:
                return False
        source, begin, end, observed, saved, diagnostics = [values[key] for key in keys[2:]]
        generation = values['shared_generation']
        return (str(receipt['coherent_readback']) == '1' and generation % 2 == 0
                and source <= begin <= end <= observed <= saved <= now
                and end - begin <= SECOND and now - begin <= 2 * SECOND
                and now - observed <= SECOND and now - saved <= SECOND
                and diagnostics <= begin and 0 <= now - diagnostics <= SECOND
                and int(receipt['sleeping']) == 0 and int(receipt['agent']) == 1
                and 0 < int(receipt['backlight']) <= 255
                and receipt['frontend_pid'] == frontend['pid']
                and receipt['worker_pid'] == worker['pid']
                and str(receipt['worker_start']) == worker['start_ticks'])
    except (KeyError, TypeError, ValueError):
        return False

def viewport(width, height):
    # Exact ui15 ControlBarLayout:88 logical pixels below guest, rotation0.
    scale = min(width / 320, max(0, height - 88) / 480)
    if scale <= 0:
        raise RuntimeError('No guest viewport')
    return {'left': (width - 320 * scale) / 2, 'top': (height - 88 - 480 * scale) / 2,
            'width': 320 * scale, 'height': 480 * scale, 'scale': scale, 'toolbar_height': 88}
