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
DIAGNOSTICS = re.compile(r'^LightTouch TCG IR cache diagnostics: (.+); aggregate wall time, not guest CPU$', re.M)
DIAGNOSTIC_KEYS = set(('version arm_attempts a32_attempts thumb_attempts a64_attempts '
    'key_eligible key_rejected save_preflight_skips save_key_changed capture_rejected '
    'validation_rejected store_failed key_us lookup_us load_decode_us replay_us capture_us '
    'encode_us save_validate_us put_us records bytes elapsed_us capacity_observed first_capacity_us '
    'put_calls put_success file_fsync_calls file_fsync_errors file_fsync_us '
    'directory_fsync_calls directory_fsync_errors directory_fsync_us').split())


def diagnostic_selection(cfg):
    value = cfg.get('cacheDiagnostics')
    if value is not None and value != {'mode': 'aggregate-v1'}:
        raise RuntimeError('Exact explicit aggregate-v1 diagnostics required')
    return value is not None


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
    result.pop('LT_TCG_IR_CACHE_DIAGNOSTICS', None)
    chosen = selection(cfg)
    diagnostics = diagnostic_selection(cfg)
    if chosen['mode'] != 'off':
        result['LT_TCG_IR_CACHE_ENABLE'] = 'experimental-v1'
        result['LT_TCG_IR_CACHE'] = chosen['directory']
        if diagnostics:
            result['LT_TCG_IR_CACHE_DIAGNOSTICS'] = 'aggregate-v1'
    return result


def identity(cfg):
    result = {'pins': {key: cfg['pins'][key]['sha256'] for key in
            ('engine', 'frontend', 'rom', 'package', 'baseLock', 'baseIdentity')},
            'seedInventorySHA256': cfg['seedInventorySHA256'],
            'workload': 'n72-320x480-balanced-home-guarded-slider-settings44,326-noaudio-v1'}
    if 'matchedDiagnostic' in cfg:
        result['matchedDiagnostic'] = cfg['matchedDiagnostic']
    if 'imageBoundOperator' in cfg:
        result['imageBoundOperator'] = cfg['imageBoundOperator']
    if 'sceneFailurePolicy' in cfg:
        result['sceneFailurePolicy'] = cfg['sceneFailurePolicy']
    if diagnostic_selection(cfg):
        result['cacheDiagnostics'] = cfg['cacheDiagnostics']
    return result


def diagnostic_report(cfg, stderr, native_final, records):
    """Disjoint phase wall costs; fsync is a nested breakdown of put_us."""
    raw = DIAGNOSTICS.findall(stderr)
    wanted = diagnostic_selection(cfg) and selection(cfg)['mode'] != 'off'
    if not wanted:
        if raw:
            raise RuntimeError('Unselected native cache diagnostics appeared')
        return None
    if not raw:
        raise RuntimeError('Selected native cache diagnostics missing')
    samples = []
    for line in raw:
        pairs = [item.split('=') for item in line.split()]
        if any(len(pair) != 2 or not re.fullmatch(r'[0-9]+', pair[1]) for pair in pairs):
            raise RuntimeError('Malformed native diagnostic metrics')
        sample = {key: int(value) for key, value in pairs}
        if len(sample) != len(pairs) or set(sample) != DIAGNOSTIC_KEYS or sample['version'] != 1:
            raise RuntimeError('Unknown or duplicate native diagnostic metrics')
        if samples and any(sample[k] < samples[-1][k] for k in sample):
            raise RuntimeError('Native diagnostic counters reset inside one worker')
        samples.append(sample)
    final = samples[-1]
    if (final['records'] != records or final['put_success'] != native_final['stored']
            or final['arm_attempts'] != sum(final[k] for k in ['a32_attempts', 'thumb_attempts', 'a64_attempts'])
            or final['arm_attempts'] != final['key_eligible'] + final['key_rejected']
            or final['key_eligible'] != sum(native_final[k] for k in ['hits', 'misses', 'rejected'])):
        raise RuntimeError('Native diagnostics disagree with final cache counters')
    def ratio(a, b): return a / b if b else None
    return {'mode': 'aggregate-v1', 'samples': samples, 'final': final,
            'eligible_hit_fraction': ratio(native_final['hits'], final['key_eligible']),
            'all_translation_hit_fraction': ratio(native_final['hits'], final['arm_attempts']),
            'phase_wall_us': sum(final[k] for k in ['key_us', 'lookup_us', 'load_decode_us',
                'replay_us', 'capture_us', 'encode_us', 'save_validate_us', 'put_us']),
            'fsync_wall_us_within_put': final['file_fsync_us'] + final['directory_fsync_us'],
            'timing_semantics': 'aggregate measured wall time; fsync is nested in put_us; not guest CPU or a speedup',
            'performance_qualified': False}


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
        if 'imageBoundOperator' in cfg:
            from postrun_review import validate_qualification
            validate_qualification(cfg, producer, result_pin['path'], receipt_pin['path'])
        schedule = cfg.get('matchedDiagnostic')
        prior_schedule = producer.get('matched_diagnostic') or {}
        if schedule and (prior_schedule.get('mode') != schedule['mode']
                         or prior_schedule.get('status') != 'DIAGNOSTIC_SCHEDULE_COMPLETED'):
            raise RuntimeError('Reuse requires the actual completed matching producer schedule')
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
        diagnostic_report(cfg, stderr, None, 0)
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
    diagnostics = diagnostic_report(cfg, stderr, last, after['records'])
    if (not after['records'] or not after['lock_present']
            or last['stored'] != after['records'] - initial['records']
            or chosen['mode'] == 'cold' and not last['stored']
            or chosen['mode'] == 'reuse' and not last['hits']):
        raise RuntimeError('Actual cache records/stored or required reuse hits missing')
    # Existing records must remain byte-for-byte; new entries are bounded above.
    if any(after['entries'].get(name) != entry for name, entry in initial['entries'].items()):
        raise RuntimeError('Prior cache records were altered or removed')
    passed = strict_session and settings_pixel == [44, 326]
    receipt = {'scope': 'EXPERIMENTAL_CACHE_HOME_SETTINGS_CORRECTNESS_NOT_PERFORMANCE',
            'mode': chosen['mode'], 'identity': identity(cfg), 'before': initial, 'after': after,
            'native_samples': samples, 'native_final': last, 'strict_session': strict_session,
            'settings_native_pixel': settings_pixel, 'performance_qualified': False,
            'status': 'SCOPED_PASS' if passed else 'FAIL'}
    if diagnostics is not None:
        receipt['diagnostics'] = diagnostics
    return receipt
