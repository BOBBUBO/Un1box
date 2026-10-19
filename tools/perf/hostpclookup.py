#!/usr/bin/env python3
"""Summarize complete sampled host-PC lookup blocks inside one LT Bench pass.

The first block per region after pass start is excluded because its beginning
can precede that marker. Timings include clock overhead and descheduling,
exclude the region lock acquisition, and sample only one lookup per256 calls.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import statistics


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('engine_log', type=Path)
    ap.add_argument('--pass-number', type=int, default=1)
    ap.add_argument('--json', type=Path)
    args = ap.parse_args()
    lines = args.engine_log.read_text().splitlines()
    start = [i for i, line in enumerate(lines)
             if re.fullmatch(rf'\[ltbench\] start pass {args.pass_number} rounds \d+', line)]
    done = [(i, int(m[1])) for i, line in enumerate(lines)
            if (m := re.fullmatch(rf'\[ltbench\] done pass {args.pass_number} in (\d+) ms', line))]
    banners = [re.fullmatch(r'\[host-pc\] cache=(on|off) prof=on', line) for line in lines]
    banners = [b for b in banners if b]
    if len(start) != 1 or len(done) != 1 or len(banners) != 1 or start[0] >= done[0][0]:
        ap.error('need one completed pass and one profiling-enabled cache banner')
    mode = banners[0][1]
    pattern = re.compile(r'\[host-pc-sample\] cache=(on|off) tree=(\d+) '
                         r'begin-ns=(\d+) end-ns=(\d+) calls=(\d+) '
                         r'candidates=(\d+) samples=(\d+) total-ns=(\d+) max-ns=(\d+)')
    previous, first, blocks = {}, set(), []
    for i, line in enumerate(lines):
        if '[host-pc-sample]' not in line:
            continue
        m = pattern.fullmatch(line)
        if not m or m[1] != mode:
            ap.error(f'malformed or mismatched sample at line {i+1}')
        b = dict(zip(('tree', 'begin_ns', 'end_ns', 'calls', 'candidates',
                      'samples', 'total_ns', 'max_ns'), map(int, m.groups()[1:])))
        if not (b['begin_ns'] < b['end_ns'] and 0 <= b['candidates'] <= b['calls']
                and b['calls'] == b['samples'] * 256 and b['samples'] > 0
                and 0 <= b['max_ns'] <= b['total_ns'] <= b['samples'] * b['max_ns']):
            ap.error(f'invalid counts or bounds at line {i+1}')
        if b['tree'] in previous and previous[b['tree']] != b['begin_ns']:
            ap.error(f'noncontiguous region blocks at line {i+1}')
        previous[b['tree']] = b['end_ns']
        inside = start[0] < i < done[0][0]
        b['selection'] = 'outside-pass'
        if inside:
            b['selection'] = 'complete' if b['tree'] in first else 'first-crossing-start-excluded'
            first.add(b['tree'])
        b['line'] = i + 1
        b['mean_ns'] = b['total_ns'] / b['samples']
        blocks.append(b)
    selected = [b for b in blocks if b['selection'] == 'complete']
    if not selected:
        ap.error('no complete blocks after excluding first per region')
    sample_count = sum(b['samples'] for b in selected)
    calls = sum(b['calls'] for b in selected)
    result = re.findall(r'\[ltbench\] stm-unaligned best ([0-9.]+) median ([0-9.]+) ns/op',
                        '\n'.join(lines[start[0]:done[0][0]]))
    if len(result) != 1:
        ap.error('need one successful stm-unaligned byte/guard/writeback result')
    summary = dict(mode=mode, complete_blocks=len(selected), samples=sample_count, calls=calls,
                   candidate_ratio=sum(b['candidates'] for b in selected)/calls,
                   mean_ns=sum(b['total_ns'] for b in selected)/sample_count,
                   median_block_mean_ns=statistics.median(b['mean_ns'] for b in selected),
                   sampled_max_ns=max(b['max_ns'] for b in selected), pass_ms=done[0][1],
                   stm_best_ns=float(result[0][0]), stm_median_ns=float(result[0][1]))
    report = dict(input_sha256=hashlib.sha256(args.engine_log.read_bytes()).hexdigest(),
                  summary=summary, blocks=blocks,
                  limitations=__doc__.strip())
    print(json.dumps(summary, indent=2))
    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
