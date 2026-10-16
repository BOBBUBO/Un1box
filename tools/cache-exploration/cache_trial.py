"""Explicit experimental cache trials; filesystem and native-counter evidence.

These receipts qualify only the narrow Home/Settings trial. They never claim
performance benefit, audio correctness, Android support or genuine guest load.
"""
import fcntl
import hashlib
import os
from pathlib import Path
import re
import stat

from exploration_common import digest, json_digest, read

MAX_RECORDS = 4096
MAX_BYTES = 32 * 1024**2
MAX_RECORD = 104 + 128 * 1024
SUMMARY = re.compile(r'^LightTouch TCG IR cache: hits=(\d+) misses=(\d+) rejected=(\d+) stored=(\d+); frontend IR only, fresh native code$', re.M)
ENABLED = re.compile(r'^LightTouch TCG IR cache: experimental enabled, mapped-image-sha256=([0-9a-f]{64}); 32MiB/4096 records maximum$', re.M)


def selection(cfg):
    value = cfg.get('cacheTrial', {'mode': 'off'})
    if value.get('mode') not in ('off', 'cold', 'reuse'):
        raise RuntimeError('Explicit off/cold/reuse cache mode required')
    if value['mode'] == 'off' and set(value) != {'mode'}:
        raise RuntimeError('OFF cache trial must not select a cache directory')
    if value['mode'] != 'off':
        if set(value) != {'mode', 'directory'}:
            raise RuntimeError('Explicit cache mode and directory only')
        path = Path(value['directory'])
        if not path.is_absolute() or path.resolve(strict=True) != path:
            raise RuntimeError('Absolute canonical cache directory required')
        for key in ('base', 'seed', 'state'):
            asset = Path(cfg[key]).resolve(strict=True)
            if path == asset or path in asset.parents or asset in path.parents:
                raise RuntimeError('Experimental cache must be separate from guest assets/state')
    return value


def environment(cfg, env):
    """Never inherit a cache selection, including in ordinary OFF trials."""
    result = dict(env)
    result.pop('LT_TCG_IR_CACHE_ENABLE', None)
    result.pop('LT_TCG_IR_CACHE', None)
    chosen = selection(cfg)
    if chosen['mode'] != 'off':
        result['LT_TCG_IR_CACHE_ENABLE'] = 'experimental-v1'
        result['LT_TCG_IR_CACHE'] = chosen['directory']
    return result


def identity(cfg):
    result = {'pins': {key: cfg['pins'][key]['sha256'] for key in
            ('engine', 'frontend', 'rom', 'package', 'baseLock', 'baseIdentity')},
            'seedInventorySHA256': cfg['seedInventorySHA256'],
            'workload': 'n72-320x480-balanced-home-guarded-slider-settings44,326-noaudio-v1'}
    if 'matchedDiagnostic' in cfg:
        result['matchedDiagnostic'] = cfg['matchedDiagnostic']
    return result


def snapshot(directory, producer):
    """Read a retired cache with its actual native-format bounds and lock.

    No deletion, chmod, repair or cache-record creation. Lock acquisition is
    possible only after the producing native worker released it.
    """
    root = Path(directory)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    lock = None
    try:
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise RuntimeError('Cache directory must be owned private0700')
        names = sorted(os.listdir(fd))
        if len(names) > MAX_RECORDS + 1:
            raise RuntimeError('Cache entry-count bound exceeded')
        entries = {}
        total = 0
        for name in names:
            if name != '.lock' and re.fullmatch('[0-9a-f]{64}', name) is None:
                raise RuntimeError('Unknown cache entry; preserve and refuse')
            item = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=fd)
            try:
                before = os.fstat(item)
                if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                        or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != 0o600):
                    raise RuntimeError('Cache entry must be private0600 regular single-link')
                if name == '.lock':
                    if before.st_size != 0:
                        raise RuntimeError('Unexpected cache lock contents')
                    fcntl.flock(item, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    lock = item
                    item = None
                    continue
                if not 104 < before.st_size <= MAX_RECORD or total + before.st_size > MAX_BYTES:
                    raise RuntimeError('Cache record/aggregate byte bound exceeded')
                data = bytearray()
                while len(data) <= before.st_size:
                    chunk = os.read(item, min(65536, before.st_size + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
                after = os.fstat(item)
                if (len(data) != before.st_size or before.st_size != after.st_size
                        or before.st_mtime_ns != after.st_mtime_ns or before.st_ctime_ns != after.st_ctime_ns):
                    raise RuntimeError('Cache record changed during inventory')
                if (data[:8] != b'LTIR\x01\0\0\0' or data[8:40].hex() != producer
                        or data[40:72].hex() != name
                        or data[72:104] != hashlib.sha256(data[104:]).digest()):
                    raise RuntimeError('Cache record producer/key/checksum mismatch')
                entries[name] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                total += len(data)
            finally:
                if item is not None:
                    os.close(item)
        if entries and lock is None:
            raise RuntimeError('Populated native cache lacks its lock inode')
        after_names = sorted(os.listdir(fd))
        after_root = os.fstat(fd)
        if names != after_names or (info.st_mtime_ns, info.st_ctime_ns) != (after_root.st_mtime_ns, after_root.st_ctime_ns):
            raise RuntimeError('Cache directory changed during inventory')
        return {'directory': str(root), 'device': info.st_dev, 'inode': info.st_ino,
                'records': len(entries), 'bytes': total, 'entries': entries,
                'inventorySHA256': json_digest(entries), 'lock_present': lock is not None,
                'native_lock_available': True, 'mappedImageSHA256': producer}
    finally:
        if lock is not None:
            os.close(lock)
        os.close(fd)


def before(cfg):
    chosen = selection(cfg)
    if chosen['mode'] == 'off':
        return {'mode': 'off'}
    result = snapshot(chosen['directory'], cfg['pins']['engine']['sha256'])
    if chosen['mode'] == 'cold':
        if result['records'] or result['lock_present']:
            raise RuntimeError('COLD cache requires a genuinely new empty directory')
    else:
        receipt_pin = cfg['pins']['cacheReuseReceipt']
        result_pin = cfg['pins']['cacheReuseResult']
        if digest(receipt_pin['path']) != receipt_pin['sha256'] or digest(result_pin['path']) != result_pin['sha256']:
            raise RuntimeError('Reuse producer receipt changed')
        prior = read(receipt_pin['path'])
        producer = read(result_pin['path'])
        if (prior.get('status') != 'SCOPED_PASS' or prior.get('mode') not in ('cold', 'reuse')
                or prior.get('identity') != identity(cfg)
                or producer.get('status') != 'SCOPED_PASS' or producer.get('cache_enabled') is not True
                or producer.get('cache_evidence_sha256') != receipt_pin['sha256']
                or producer.get('confirmed_clean_receipt') is not True
                or producer.get('strict_retirement') is not True
                or producer.get('dirty') is not False
                or prior.get('after') != result or not result['records']):
            raise RuntimeError('Reuse requires exact clean producer, workload and cache inventory')
        result['reuseProducerReceiptSHA256'] = receipt_pin['sha256']
        result['reuseProducerResultSHA256'] = result_pin['sha256']
    return dict(result, mode=chosen['mode'])


def finish(cfg, initial, stderr, strict_session, settings_pixel):
    chosen = selection(cfg)
    if chosen['mode'] == 'off':
        if ENABLED.search(stderr) or SUMMARY.search(stderr):
            raise RuntimeError('OFF trial unexpectedly emitted native cache telemetry')
        return {'mode': 'off', 'status': 'SCOPED_PASS' if strict_session else 'FAIL'}
    after = snapshot(chosen['directory'], cfg['pins']['engine']['sha256'])
    if (after['device'], after['inode']) != (initial['device'], initial['inode']):
        raise RuntimeError('Actual cache directory identity changed')
    enabled = ENABLED.findall(stderr)
    samples = [dict(zip(('hits', 'misses', 'rejected', 'stored'), map(int, values)))
               for values in SUMMARY.findall(stderr)]
    if enabled != [cfg['pins']['engine']['sha256']] or not samples:
        raise RuntimeError('Exact native mapped-image enable/counter evidence missing')
    for earlier, later in zip(samples, samples[1:]):
        if any(later[key] < earlier[key] for key in earlier):
            raise RuntimeError('Native cache counters reset inside one worker')
    last = samples[-1]
    if (not after['records'] or not after['lock_present']
            or last['stored'] != after['records'] - initial['records']
            or chosen['mode'] == 'cold' and not last['stored']
            or chosen['mode'] == 'reuse' and not last['hits']):
        raise RuntimeError('Actual cache records/stored or required reuse hits missing')
    # Existing records must remain byte-for-byte; new entries are bounded above.
    if any(after['entries'].get(name) != entry for name, entry in initial['entries'].items()):
        raise RuntimeError('Prior cache records were altered or removed')
    passed = strict_session and settings_pixel == [44, 326]
    return {'scope': 'EXPERIMENTAL_CACHE_HOME_SETTINGS_CORRECTNESS_NOT_PERFORMANCE',
            'mode': chosen['mode'], 'identity': identity(cfg), 'before': initial, 'after': after,
            'native_samples': samples, 'native_final': last, 'strict_session': strict_session,
            'settings_native_pixel': settings_pixel, 'performance_qualified': False,
            'status': 'SCOPED_PASS' if passed else 'FAIL'}
