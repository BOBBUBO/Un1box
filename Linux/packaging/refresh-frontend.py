#!/usr/bin/env python3
"""Refresh the desktop frontend inside the bounded saved Debian build container."""
import subprocess
import sys

if __name__ == '__main__':
    if len(sys.argv) < 3 or sys.argv[1] != '--':
        raise SystemExit('Expected -- followed by the existing collection command')
    subprocess.run(['cmake', '--build', '/build/frontend', '--parallel', '2'], check=True)
    subprocess.run(['ctest', '--test-dir', '/build/frontend', '--output-on-failure'], check=True)
    subprocess.run(sys.argv[2:], check=True)
