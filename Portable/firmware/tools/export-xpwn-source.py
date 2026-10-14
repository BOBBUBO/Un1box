#!/usr/bin/env python3
"""Export only verified public XPwn inputs needed by the binary HFSX library."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PIN = "20c32e5c12d1b22a9d55a59a0ff6267f539b77f4"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="pinned upstream checkout or existing verified selected export")
    parser.add_argument("destination", type=Path, help="fresh public-source output directory")
    args = parser.parse_args()
    if (args.source / ".git").exists():
        revision = subprocess.check_output(["git", "-C", str(args.source), "rev-parse", "HEAD"], text=True).strip()
    elif (args.source / "XPWN-COMMIT").is_file():
        revision = (args.source / "XPWN-COMMIT").read_text().strip()
    else:
        parser.error("source has no verifiable upstream revision")
    if revision != PIN:
        parser.error("XPwn source pin mismatch")
    files = {}
    for line in (ROOT / "xpwn-selected.SHA256SUMS").read_text().splitlines():
        digest, name = line.split(None, 1)
        data = (args.source / name).read_bytes()
        if sha(data) != digest:
            parser.error("selected XPwn source checksum mismatch: " + name)
        files[name] = data
    # No .git objects, unused applications or Apple-derived folding source.
    files["XPWN-COMMIT"] = (PIN + "\n").encode()
    files["XPWN-SELECTED.SHA256SUMS"] = (ROOT / "xpwn-selected.SHA256SUMS").read_bytes()
    files["LIGHTTOUCH-ADAPTATION.txt"] = (ROOT / "NOTICE.firmware.txt").read_bytes()
    notice_checks = dict((name, digest) for digest, name in
                         (line.split(None, 1) for line in (ROOT / "vendor/SHA256SUMS").read_text().splitlines()))
    for name in ("Info-ZIP-LICENSE.txt", "minizip-LICENSE.txt", "zlib-1.1.3-LICENSE.txt"):
        data = (ROOT / "vendor" / name).read_bytes()
        if sha(data) != notice_checks.get(name):
            parser.error("vendored notice checksum mismatch: " + name)
        files["LICENSES/" + name] = data
    destination = args.destination.absolute()
    if destination.exists():
        parser.error("destination must be a fresh source-export directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".xpwn-source-", dir=destination.parent))
    try:
        for name in sorted(files):
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(files[name])
            path.chmod(0o644)
        for path in stage.rglob("*"):
            if path.is_dir():
                path.chmod(0o755)
        stage.chmod(0o755)
        os.rename(stage, destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    print(json.dumps({"schema": 1, "upstream_commit": PIN, "files": len(files),
                      "source_manifest_sha256": sha(files["XPWN-SELECTED.SHA256SUMS"]), "status": "OK"}, sort_keys=True))


if __name__ == "__main__":
    main()
