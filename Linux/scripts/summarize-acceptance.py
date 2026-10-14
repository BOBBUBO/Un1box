#!/usr/bin/env python3
"""Read retained native logs without promoting observations to guest acceptance."""
import argparse
import hashlib
import json
import pathlib
import re

def summarize(text):
    rows = []
    for line in text.splitlines():
        if line.startswith('status '):
            row = {}
            for key, value in re.findall(r'(\w+)=([^\s]+)', line):
                try:
                    row[key] = int(value)
                except ValueError:
                    row[key] = value
            rows.append(row)
    keys = ('frames', 'pcm_captured', 'pcm_pulled', 'pcm_native_dropped', 'pcm_received',
            'pcm_nonzero_samples', 'pcm_socket_dropped', 'pcm_sdl_queued', 'pcm_sdl_discarded', 'pcm_sdl_errors')
    ranges = {key: {'first': values[0], 'last': values[-1], 'delta': values[-1] - values[0]}
              for key in keys if (values := [r[key] for r in rows if isinstance(r.get(key), int)])}
    intervals = []
    for before, after in zip(rows, rows[1:]):
        if not all(isinstance(r.get('observed_monotonic_ns'), int) for r in (before, after)):
            continue
        elapsed = (after['observed_monotonic_ns'] - before['observed_monotonic_ns']) / 1e9
        if elapsed <= 0:
            continue
        rates = {key: (after[key] - before[key]) / elapsed for key in ('frames', 'pcm_captured', 'pcm_received')
                 if key in before and key in after and isinstance(before[key], int) and isinstance(after[key], int)
                 and after[key] >= before[key]}
        intervals.append({'seconds': elapsed, 'state_before': before.get('state'), 'state_after': after.get('state'),
                          'rates_per_second': rates})
    return {'status_samples': len(rows), 'state_values_observed': sorted({r['state'] for r in rows if isinstance(r.get('state'), int)}),
            'counter_ranges': ranges, 'intervals': intervals,
            'delivered_input_reports': len(re.findall(r'^input batch=\d+ state=2 ', text, re.M)),
            'rejected_input_reports': len(re.findall(r'^input batch=\d+ state=3 ', text, re.M)),
            'guest_result_lines': [line for line in text.splitlines() if line.startswith('guest request=')],
            'confirmed_clean_status_observed': any(r.get('state') == 0 and r.get('confirmed') == 1 and
                                                  r.get('dirty') == 0 for r in rows),
            'measurement_limitations': ['Counters do not prove guest contact IDs or visible pinch.',
                                       'PCM production/nonzero/queue counts do not prove audible playback.',
                                       'Static page frames can legitimately stop; inspect workload before calling starvation.',
                                       'Input reports cover newest batch snapshots, not every intermediate delivery.',
                                       'Rates unavailable where old logs lack host observation timestamps.']}

def worker_samples(text):
    groups = {}
    for line in text.splitlines():
        row = json.loads(line)
        groups.setdefault((row['pid'], row['start_ticks']), []).append(row)
    output = []
    for (pid, started), rows in groups.items():
        first, last = rows[0], rows[-1]
        seconds = (last['observed_monotonic_ns'] - first['observed_monotonic_ns']) / 1e9
        ticks = sum(last[k] - first[k] for k in ('cpu_user_ticks', 'cpu_system_ticks'))
        cpu = 100 * ticks / first['clock_ticks_per_second'] / seconds if seconds > 0 and ticks >= 0 else None
        pss = [r['pss_bytes'] for r in rows if r.get('pss_bytes') is not None]
        output.append({'pid': pid, 'start_ticks': started, 'samples': len(rows), 'observed_seconds': seconds,
                       'cpu_percent_of_one_core': cpu, 'rss_peak_bytes': max(r['rss_bytes'] for r in rows),
                       'pss_peak_bytes': max(pss) if pss else None,
                       'maximum_threads': max(r['threads'] for r in rows)})
    return output

def file_hash(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir', type=pathlib.Path, required=True)
    p.add_argument('--evidence-kind', choices=('host-probe', 'native-guest-attempt'), required=True)
    p.add_argument('--output', type=pathlib.Path, required=True)
    a = p.parse_args()
    if not a.output.is_absolute() or a.output.exists():
        p.error('output must be a new absolute private evidence path')
    repo = pathlib.Path(__file__).resolve().parents[2]
    if repo == a.output.resolve() or repo in a.output.resolve().parents:
        p.error('retain evidence outside Git')
    files = sorted(path for path in a.run_dir.iterdir() if path.is_file())
    logs = [path for path in files if path.suffix == '.log']
    result = {'declared_evidence_kind': a.evidence_kind,
              'initial_gate_claims': 'none; integrator must qualify actual guest/target evidence',
              'gyro_scope': 'deferred; excluded from initial acceptance',
              'logs': {path.name: summarize(path.read_text(errors='replace')) for path in logs},
              'artifacts': {path.name: {'bytes': path.stat().st_size,
                                       'sha256': file_hash(path)} for path in files}}
    samples = a.run_dir / 'samples.jsonl'
    if samples.exists():
        result['worker_resource_observations'] = worker_samples(samples.read_text())
    if a.evidence_kind == 'host-probe':
        result['qualification_limit'] = 'Host probes never qualify real guest input, graphics, audio or persistence.'
    else:
        result['qualification_limit'] = 'Native attempt remains unqualified until target identity and guest pixels/replies reviewed.'
    a.output.write_text(json.dumps(result, indent=2) + '\n')
    print(a.output)

if __name__ == '__main__':
    main()
