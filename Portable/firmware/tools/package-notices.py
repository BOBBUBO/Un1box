#!/usr/bin/env python3
"""Generate deterministic Android firmware notice assets from public inputs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
NDK_NOTICE_SHA256 = "789714a9076571c580cf531bdcebec776a4d889c12c3c1108adafc6e4338710a"
LICENSES = {
    "COPYING.firmware-GPL-3.0.txt": "XPwn-LICENSE",
    "nlohmann-json.MIT.txt": "json-LICENSE.MIT",
    "tinyxml2.zlib.txt": "tinyxml2-LICENSE.txt",
    "XPwn.minizip.zlib.txt": "minizip-LICENSE.txt",
    "XPwn.Info-ZIP.txt": "Info-ZIP-LICENSE.txt",
    "XPwn.CRC32.zlib.txt": "zlib-1.1.3-LICENSE.txt",
    "OpenSSL3.Apache-2.0.txt": "OpenSSL3-LICENSE.txt",
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--ndk-notice", type=Path, required=True)
    parser.add_argument("--source-reference", required=True,
                        help="publisher's HTTPS corresponding-source URL; availability must be verified separately")
    parser.add_argument("--source-revision", required=True, help="full source Git commit represented by the URL")
    args = parser.parse_args()
    url = urlsplit(args.source_reference)
    if url.scheme != "https" or not url.netloc or url.username or url.password:
        parser.error("source-reference must be an HTTPS URL without embedded credentials")
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_revision):
        parser.error("source-revision must be a full lowercase Git SHA1")
    checks = {}
    for line in (ROOT / "vendor/SHA256SUMS").read_text().splitlines():
        digest, name = line.split(None, 1)
        checks[name] = digest
    files = {}
    for output, name in LICENSES.items():
        data = (ROOT / "vendor" / name).read_bytes()
        if sha(data) != checks.get(name):
            parser.error("vendored notice checksum mismatch: " + name)
        files[output] = data
    ndk = args.ndk_notice.read_bytes()
    if sha(ndk) != NDK_NOTICE_SHA256:
        parser.error("NDK notice must match pinned 28.2.13676358")
    files["NDK.toolchain.NOTICE.txt"] = ndk
    files["NOTICE.firmware.txt"] = (ROOT / "NOTICE.firmware.txt").read_bytes()
    manifest = {
        "schema": 1,
        "scope": "native firmware notices, not complete APK licensing clearance",
        "source_reference": args.source_reference,
        "source_revision": args.source_revision,
        "source_reference_availability": "publisher must verify before distribution",
        "ndk_version": "28.2.13676358",
        "files": [{"path": name, "sha256": sha(files[name])} for name in sorted(files)],
    }
    files["manifest.json"] = (json.dumps(manifest, indent=2, ensure_ascii=True) + "\n").encode()
    destination = args.destination.absolute()
    if destination.exists():
        actual = {p.name: p.read_bytes() for p in destination.iterdir() if p.is_file() and not p.is_symlink()}
        if actual != files or len(list(destination.iterdir())) != len(files):
            parser.error("destination already exists with different contents; use a fresh generated-assets directory")
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".firmware-notices-", dir=destination.parent))
        try:
            for name in sorted(files):
                path = stage / name
                path.write_bytes(files[name])
                path.chmod(0o644)
            stage.chmod(0o755)
            os.rename(stage, destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    print(json.dumps({"schema": 1, "manifest_sha256": sha(files["manifest.json"]),
                      "files": len(files), "status": "OK"}, sort_keys=True))


if __name__ == "__main__":
    main()
