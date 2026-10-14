#!/usr/bin/env python3
"""Review retained Android media JSON offline; never connects to or controls a guest."""
import argparse
import bisect
import hashlib
import json
from pathlib import Path

ACTIVITY = ('producedFrames', 'presentedFrames', 'audio.pulledNonzeroSamples',
            'audio.writtenNonzeroSamples', 'audio.playedFrames')
ERRORS = ('windowFailures', 'frameCopyFailures', 'nativePcmFailures',
          'audio.writeFailures', 'audio.nativePullFailures')
REPORTED = ('audio.underruns', 'audio.shortWrites', 'pcmDroppedFrames',
            'sharedPcmDroppedFrames', 'windowGeometryFailures', 'invalidWindowBuffers')


def value(row, key):
    current = row.get('status', {}).get('observations', {})
    for part in key.split('.'):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current if isinstance(current, int) and not isinstance(current, bool) else None


def analyze(rows, expected_ms=1_200_000, window_ms=30_000, max_gap_ms=2500):
    """Require complete per-window observations; all results remain host facts."""
    starts = [i for i, row in enumerate(rows) if row.get('label') == 'measurement-start']
    ends = [i for i, row in enumerate(rows) if row.get('label') == 'measurement-end']
    issues = []
    if len(starts) != 1 or len(ends) != 1 or ends[0] <= starts[0]:
        return {'host_window_checks_pass': False, 'issues': ['One ordered measurement-start/end pair required'],
                'windows': []}
    measured = rows[starts[0]:ends[0] + 1]
    times = [row.get('hostElapsedMs') for row in measured]
    if any(not isinstance(t, int) or isinstance(t, bool) for t in times):
        return {'host_window_checks_pass': False, 'issues': ['Missing integer BOOTTIME sample timestamp'], 'windows': []}
    if any(b < a for a, b in zip(times, times[1:])):
        return {'host_window_checks_pass': False, 'issues': ['Sample clock moved backwards'], 'windows': []}
    identity = {(row.get('status', {}).get('pid'), row.get('status', {}).get('uid')) for row in measured}
    if len(identity) != 1 or any(not isinstance(n, int) or n <= 0 for pair in identity for n in pair):
        issues.append('Missing or changing worker PID/UID; counters cannot span workers')
    worker = next(iter(identity)) if len(identity) == 1 else None
    if any(row.get('status', {}).get('state') != 'running' for row in measured):
        issues.append('Measurement includes non-running or unavailable worker')
    if any(row.get('status', {}).get('error') not in ('',) for row in measured):
        issues.append('Missing or actionable worker error in measurement')
    duration = times[-1] - times[0]
    if duration < expected_ms:
        issues.append('Measurement shorter than required duration')
    gap = max((b - a for a, b in zip(times, times[1:])), default=0)
    if gap > max_gap_ms:
        issues.append('Observation gap exceeds bound; silence/starvation may be unobserved')
    missing = sorted({key for row in measured for key in ACTIVITY + ERRORS if value(row, key) is None})
    if missing:
        issues.append('Missing required counters: ' + ', '.join(missing))
    resets = sorted({key for key in ACTIVITY + ERRORS + REPORTED
                     for a, b in zip(measured, measured[1:])
                     if value(a, key) is not None and value(b, key) is not None
                     and value(b, key) < value(a, key)})
    if resets:
        issues.append('Counter reset/backwards value: ' + ', '.join(resets))
    windows = []
    start = times[0]
    for offset in range(0, expected_ms, window_ms):
        target_start = start + offset
        target_end = start + min(offset + window_ms, expected_ms)
        first_index = max(0, bisect.bisect_right(times, target_start) - 1)
        last_index = bisect.bisect_right(times, target_end) - 1
        first, last = measured[first_index], measured[max(first_index, last_index)]
        elapsed = last['hostElapsedMs'] - first['hostElapsedMs']
        deltas = {key: value(last, key) - value(first, key)
                  if value(first, key) is not None and value(last, key) is not None else None
                  for key in ACTIVITY + ERRORS + REPORTED}
        absent = [key for key in ACTIVITY if deltas[key] is None or deltas[key] <= 0]
        faults = [key for key in ERRORS if deltas[key] is None or deltas[key] != 0]
        covered = elapsed >= target_end - target_start - 2 * max_gap_ms
        windows.append({'target_start_host_ms': target_start, 'target_end_host_ms': target_end,
                        'first_observation_host_ms': first['hostElapsedMs'],
                        'last_observation_host_ms': last['hostElapsedMs'], 'observed_ms': elapsed,
                        'counter_deltas': deltas, 'missing_activity': absent, 'error_changes': faults,
                        'coverage_sufficient': covered,
                        'host_window_checks_pass': covered and not absent and not faults})
    aggregate = {key: value(measured[-1], key) - value(measured[0], key)
                 if value(measured[-1], key) is not None and value(measured[0], key) is not None else None
                 for key in ACTIVITY}
    frame_ages = [host - source for row in measured
                  if (host := value(row, 'hostMonotonicNs')) is not None
                  and (source := value(row, 'producerFrameTimestampNs')) is not None
                  and source > 0 and host >= source]
    return {'host_window_checks_pass': not issues and bool(windows) and all(x['host_window_checks_pass'] for x in windows),
            'issues': issues, 'worker_pid_uid': worker, 'observed_ms': duration,
            'max_sample_gap_ms': gap, 'first_observation_host_ms': times[0], 'last_observation_host_ms': times[-1],
            'initial_counters': {key: value(measured[0], key) for key in ACTIVITY + ERRORS},
            'final_counters': {key: value(measured[-1], key) for key in ACTIVITY + ERRORS},
            'aggregate_activity_deltas': aggregate,
            'aggregate_only_would_show_activity': all(n is not None and n > 0 for n in aggregate.values()),
            'maximum_observed_producer_frame_age_ns': max(frame_ages, default=None),
            'lifetime_cadence_highwater_ns': value(measured[-1], 'maxPresentGapNs'),
            'cadence_limit': 'Lifetime highwater is not a window cadence measurement; source clock and BOOTTIME are separate',
            'windows': windows}


def check_post(sustained, segments):
    previous = sustained
    for segment in segments:
        if segment.get('worker_pid_uid') != sustained.get('worker_pid_uid'):
            segment['host_window_checks_pass'] = False
            segment.setdefault('issues', []).append('Reopened segment is not the same worker')
        first_time = segment.get('first_observation_host_ms')
        previous_end = previous.get('last_observation_host_ms')
        if first_time is None or previous_end is None or first_time < previous_end:
            segment['host_window_checks_pass'] = False
            segment.setdefault('issues', []).append('Reopened segment precedes previous measurement end')
        for key, before in previous.get('final_counters', {}).items():
            after = segment.get('initial_counters', {}).get(key)
            if before is not None and after is not None and after < before:
                segment['host_window_checks_pass'] = False
                segment.setdefault('issues', []).append('Counter rewound between segments: ' + key)
        previous = segment
    return bool(segments) and all(row['host_window_checks_pass'] for row in segments)


def read(path):
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError('Observation JSON exceeds 64MiB bound')
    rows = json.loads(path.read_text())
    if not isinstance(rows, list) or len(rows) > 10000 or any(not isinstance(row, dict) for row in rows):
        raise ValueError('Expected bounded observation array')
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observations', type=Path, required=True)
    parser.add_argument('--post-observations', type=Path, action='append', default=[],
                        help='Separate >=30s reopened/resumed measurement segment; same worker required')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--window-seconds', type=int, choices=(20, 30), default=30)
    parser.add_argument('--max-sample-gap-ms', type=int, default=2500)
    args = parser.parse_args()
    if not 1 <= args.max_sample_gap_ms <= 5000:
        parser.error('Sample gap must be 1..5000ms')
    repo = Path(__file__).resolve().parents[2]
    target = args.output.resolve()
    if not args.output.is_absolute() or target.exists() or target == repo or repo in target.parents:
        parser.error('Output must be a new absolute evidence path outside source checkout')
    sustained = analyze(read(args.observations), window_ms=args.window_seconds * 1000,
                        max_gap_ms=args.max_sample_gap_ms)
    post = [analyze(read(path), expected_ms=30000, window_ms=30000,
                    max_gap_ms=args.max_sample_gap_ms) for path in args.post_observations]
    post_ok = check_post(sustained, post)
    report = {'evidence_kind': 'offline retained host-observation review', 'audio_workload': 'periodic stock bursts; not continuous audio',
              'sustained': sustained, 'post_transition_segments': post,
              'post_transition_qualification': 'missing; immediate transition snapshots do not prove resumed progress' if not post else 'Review each supplied segment label and transition lineage manually',
              'all_supplied_host_checks_pass': sustained['host_window_checks_pass'] and post_ok,
              'guest_acceptance_passed': False, 'audible_playback_verified': False, 'visible_guest_verified': False,
              'limits': ['Positive deltas locate activity inside windows, not instantaneous overlap or continuous sound',
                         'playedFrames does not prove physical audibility or that each nonzero sample was heard',
                         'Underruns/short writes/drops reported; burst silence may underrun, must not be hidden as healthy continuous playback',
                         'Same PID/UID alone is insufficient for reuse; review retained process start ticks/receipts',
                         'Inspect current pixels, fresh source timestamps, complete wire/control receipts and post-close/reopen lineage',
                         'RSS/thermal/physical sound and finer cadence require independent retained observer evidence'],
              'input_sha256': {str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest()
                               for path in [args.observations] + args.post_observations}}
    target.write_text(json.dumps(report, indent=2) + '\n')
    print(target)
    raise SystemExit(0 if report['all_supplied_host_checks_pass'] else 1)


if __name__ == '__main__':
    main()
