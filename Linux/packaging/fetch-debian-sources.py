#!/usr/bin/env python3
"""Fetch exact Debian source archives for an audited runtime inventory, never install."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    packages = json.loads(args.inventory.read_text())
    sources = sorted({(p['source_package'], p['source_version']) for p in packages})
    args.output.mkdir(parents=True, exist_ok=False)
    for name, version in sources:
        if not name or not version or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+.:~_-' for c in name + version):
            raise ValueError('Invalid Debian source coordinate')
        subprocess.run(['apt-get', 'source', '--download-only', '--only-source', name + '=' + version], cwd=args.output, check=True, timeout=180)
    report = dict(schema=1, requested_sources=sources, files={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(args.output.iterdir()) if p.is_file()})
    (args.output / 'SOURCE-ARCHIVE-MANIFEST.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'source_count': len(sources), 'archives': len(report['files'])}))


if __name__ == '__main__':
    main()
