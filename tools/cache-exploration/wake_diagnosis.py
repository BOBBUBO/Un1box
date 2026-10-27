#!/usr/bin/env python3
"""Read retained wake traces without running, retrying or qualifying a guest."""
import argparse
import hashlib
import json
from pathlib import Path

SECOND = 1_000_000_000
MAX_FILE = 32 * 1024 * 1024


def read_file(directory, name, pins):
    path = directory / name
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE:
        raise ValueError('Expected bounded regular retained file: ' + name)
    content = path.read_bytes()
    pins[name] = hashlib.sha256(content).hexdigest()
    return content


def fields(row):
    return dict(item.split('=', 1) for item in row.split()[1:] if '=' in item)


def wake_predicate(status, poll_ns, home_ns):
    """The frozen controller predicate, including source/telemetry freshness."""
    source = int(status.get('frame_monotonic_ns', '0'))
    observed = int(status['observed_monotonic_ns'])
    return (int(status['state']) == 2 and int(status['sleeping']) == 0
            and int(status['backlight']) > 0 and int(status['agent']) == 1
            and home_ns <= source <= poll_ns and poll_ns - source <= 2 * SECOND
            and home_ns <= observed <= poll_ns and poll_ns - observed <= SECOND)


def summarize(directory):
    directory = Path(directory).resolve(strict=True)
    pins = {}
    def document(name):
        return json.loads(read_file(directory, name, pins))
    hello = int(document('hello.json')['monotonic_ns'])
    record = document('timed-lock-unlock-home-unlock.json')
    result = document('RESULT.json')
    cache = document('cache-trial.json')
    rows = read_file(directory, 'session.log', pins).decode().splitlines()
    controls = [json.loads(row) for row in
                read_file(directory, 'controls.jsonl', pins).decode().splitlines()]
    home = int(record['home_input_begin_ns'])
    released = int(record['home_released_ns'])
    begin = int(record['awake_wait_begin_ns'])
    deadline = int(record['awake_wait_deadline_ns'])
    end = int(record['awake_wait_end_ns'])
    input_deadline = int(record['input_deadline_ns'])
    if not hello <= home <= released <= begin <= deadline <= input_deadline or end < begin:
        raise ValueError('Retained wake clocks are inconsistent')
    # The actual recorded window is authoritative. Never enlarge or renew it.
    if deadline != min(begin + 2_500_000_000, input_deadline - 4 * SECOND):
        raise ValueError('Wake window differs from the frozen controller policy')
    observations = record['awake_wait_observations']
    previous = begin
    admitted = []
    for observation in observations:
        poll = int(observation['poll_ns'])
        if not previous <= poll <= end:
            raise ValueError('Retained poll clocks are inconsistent')
        previous = poll
        if poll < deadline and wake_predicate(observation['status'], poll, home):
            admitted.append(poll)
    signals = [int(item['observed_monotonic_ns']) for item in result['signals']]
    stop = min(signals) if signals else None
    if stop is not None and stop < home:
        raise ValueError('Stop preceded the retained Home attempt')
    statuses = [fields(row) for row in rows if row.startswith('status ')]
    transitions = []
    prior = None
    for status in statuses:
        timestamp = int(status['observed_monotonic_ns'])
        if timestamp < home:
            continue
        state = [int(status[key]) for key in ('state', 'sleeping', 'backlight', 'frames')]
        if state != prior:
            transitions.append({'relative_hello_ns': timestamp - hello,
                                'state_sleeping_backlight_frames': state,
                                'after_stop_dispatch': stop is not None and timestamp >= stop})
        prior = state
    home_controls = []
    for item in controls:
        operation = item['argv'][1:]
        if operation in (['keydown', 'h'], ['keyup', 'h']):
            home_controls.append({'operation': operation,
                                  'begin_relative_hello_ns': int(item['input_begin_ns']) - hello,
                                  'end_relative_hello_ns': int(item['input_end_ns']) - hello})
    native = cache.get('native_final')
    return {
        'scope': 'OFFLINE_RETAINED_WAKE_DIAGNOSIS_ONLY',
        'input_sha256': pins,
        'hello_ns': hello,
        'home_relative_hello_ns': home - hello,
        'home_control_commands': home_controls,
        'home_key_pair_recorded': [x['operation'] for x in home_controls] == [['keydown', 'h'], ['keyup', 'h']],
        'input_dispatch_is_guest_consumption': False,
        'wake_window_relative_hello_ns': [begin - hello, deadline - hello],
        'original_window_observation_count': len(observations),
        'original_window_predicate_observed': bool(admitted),
        'first_admitted_relative_home_ns': admitted[0] - home if admitted else None,
        'last_original_window_status': observations[-1]['status'] if observations else None,
        'stop_relative_hello_ns': stop - hello if stop is not None else None,
        'state_transitions_after_home': transitions,
        'native_cache_final': native,
        'native_replay_observed': bool(native and native.get('hits', 0) > 0),
        'original_error': record.get('error'),
        'slider_started': record.get('slider_started'),
        'cause_assigned': False,
        'guest_acceptance': False,
        'performance_qualified': False,
        'notes': [
            'Telemetry predicate success is not a personally viewed guest scene.',
            'Post-Stop display changes cannot establish a late Home wake.',
            'Missing original-window evidence cannot establish a permanent wake failure.',
            'No retry, clock change, input, guest process or cache reuse occurs in this tool.',
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.run), indent=2))


if __name__ == '__main__':
    main()
