#!/usr/bin/env python3
"""Capture one actual SDL native frame and retain its matching diagnostic receipt."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--window', type=int, required=True)
    parser.add_argument('--log', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    before_bytes = args.log.stat().st_size
    started_ns = time.monotonic_ns()
    command = ['xdotool', 'key', '--window', str(args.window), 's']
    subprocess.run(command, check=True, timeout=5)
    deadline = time.monotonic() + 5
    receipt = None
    while time.monotonic() < deadline:
        with args.log.open('rb') as source:
            source.seek(before_bytes)
            lines = source.read().decode(errors='replace').splitlines()
        for line in lines:
            if line.startswith('screenshot sequence='):
                values = dict(re.findall(r'(\w+)=([^ ]+)', line))
                if int(values.get('observed_monotonic_ns', 0)) >= started_ns:
                    receipt = dict(values, exact_log_line=line)
                    break
        if receipt:
            break
        time.sleep(.05)
    if not receipt:
        (args.output_dir / 'failure.json').write_text(json.dumps({'started_monotonic_ns': started_ns,
                    'reason': 'No matching fresh capture receipt within5s', 'argv': command}) + '\n')
        raise RuntimeError('No fresh native screenshot receipt')
    source = Path(receipt['path'])
    target = args.output_dir / 'native.bmp'
    shutil.copyfile(source, target)
    receipt['copy_finished_monotonic_ns'] = time.monotonic_ns()
    receipt['sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
    receipt['clock'] = 'host CLOCK_MONOTONIC; separate frame-source and worker-diagnostic observations'
    receipt['display'] = os.environ.get('DISPLAY')
    receipt['native_rgb_unchanged'] = True
    (args.output_dir / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    subprocess.run(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-i', str(target),
                    '-frames:v', '1', str(args.output_dir / 'native.png')], check=True, timeout=5)
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
